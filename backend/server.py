#!/usr/bin/env python3
import io, json, mimetypes, os, re, threading, time, traceback, uuid, zipfile, subprocess, signal, sqlite3, csv, sys
from email.parser import BytesParser
from email.policy import default
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from .migrator import MigrationEngine
from .repair import RepairEngine, analyze_site_data

ROOT=Path(__file__).resolve().parents[1]
FRONT=ROOT/'frontend'
# Runtime/customer data must live outside the installed application directory.
# CODEXIFYR_DATA_DIR is set by the desktop shell/installer and also works for hosted/dev runs.
DATA_ROOT=Path(os.environ.get('CODEXIFYR_DATA_DIR', str(ROOT/'runtime'))).expanduser().resolve()
RUNTIME=DATA_ROOT
JOBS=RUNTIME/'jobs'
PLUGIN=DATA_ROOT/'codexifyr-migrator-importer.zip'
PLUGIN_SOURCE=ROOT/'wordpress-plugin'/'codexifyr-migrator-importer'
SHOPIFY_SCRAPER=ROOT/'tools'/'shopify_scraper'/'scraper.py'
for d in (RUNTIME,JOBS,RUNTIME/'uploads',RUNTIME/'logs'):d.mkdir(parents=True,exist_ok=True)

def ensure_plugin_zip():
    """Build the downloadable plugin artifact from tracked source when needed.
    This lets Git ignore generated ZIPs while the hosted Plugin page still works.
    """
    if PLUGIN.exists():return PLUGIN
    if not PLUGIN_SOURCE.exists():return PLUGIN
    tmp=PLUGIN.with_suffix('.zip.tmp')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
        for f in PLUGIN_SOURCE.rglob('*'):
            if f.is_file():z.write(f,f.relative_to(PLUGIN_SOURCE.parent))
    tmp.replace(PLUGIN)
    return PLUGIN

ensure_plugin_zip()

def ensure_shopify_json(csv_path, json_path):
    """Create a JSON download from the finished Shopify CSV without touching scraper logic or CSV output."""
    csv_path=Path(csv_path);json_path=Path(json_path)
    if not csv_path.exists():raise RuntimeError('Shopify CSV is not ready for this job.')
    # Rebuild when missing or older than the CSV so JSON always matches the exact CSV export.
    if json_path.exists() and json_path.stat().st_mtime >= csv_path.stat().st_mtime:return json_path
    with csv_path.open('r',encoding='utf-8-sig',newline='',errors='replace') as f:
        rows=list(csv.DictReader(f))
    tmp=json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(json_path)
    return json_path

def validate_shopify_csv(path):
    """Read-only Shopify CSV validator. Never rewrites the user's CSV."""
    path=Path(path)
    if not path.exists():raise RuntimeError('Shopify CSV is not ready for this job.')
    with path.open('r',encoding='utf-8-sig',newline='',errors='replace') as f:
        rows=list(csv.DictReader(f))
    required=['Handle','Title','Option1 Name','Option1 Value','Variant Price','Image Src','SEO Title','SEO Description','Status']
    headers=list(rows[0].keys()) if rows else []
    missing_cols=[x for x in required if x not in headers]
    by_handle={}
    for r in rows:
        h=(r.get('Handle') or '').strip()
        if h:by_handle.setdefault(h,[]).append(r)
    products=len(by_handle)
    variant_rows=[];default_title=0;duplicate_variants=0;missing_price=0;missing_images=0;missing_seo=0;variant_images=0;optioned=0
    for h,rs in by_handle.items():
        first=next((r for r in rs if (r.get('Title') or '').strip()),rs[0])
        if not (first.get('SEO Title') or '').strip() or not (first.get('SEO Description') or '').strip():missing_seo+=1
        if not any((r.get('Image Src') or '').strip() for r in rs):missing_images+=1
        seen=set();vcount=0
        for r in rs:
            # Collection-only rows intentionally contain no variant data.
            has_variant=any((r.get(k) or '').strip() for k in ('Option1 Value','Option2 Value','Option3 Value','Variant Price','Variant SKU','Variant Barcode','Variant Image'))
            if not has_variant:continue
            vcount+=1;variant_rows.append(r)
            opts=tuple((r.get(f'Option{i} Value') or '').strip() for i in (1,2,3))
            names=tuple((r.get(f'Option{i} Name') or '').strip() for i in (1,2,3))
            if any(opts):optioned+=1
            if (names[0].lower()=='title' and opts[0].lower()=='default title') or (opts[0].lower()=='default title'):default_title+=1
            key=opts if any(opts) else ('__default__',)
            if key in seen:duplicate_variants+=1
            seen.add(key)
            if not (r.get('Variant Price') or '').strip():missing_price+=1
            if (r.get('Variant Image') or '').strip():variant_images+=1
    variants=len(variant_rows)
    issues=[]
    def issue(code,label,count,severity='warning'):
        if count:issues.append({'code':code,'label':label,'count':count,'severity':severity})
    issue('missing_required_columns','Missing required Shopify CSV columns',len(missing_cols),'error')
    issue('duplicate_variant_options','Duplicate variant option combinations',duplicate_variants)
    # Default Title is valid only for simple products; flag only when it coexists with optioned variants or looks excessive.
    suspicious_default=default_title if (optioned and default_title) else 0
    issue('suspicious_default_title','Suspicious Default Title variants',suspicious_default)
    issue('missing_variant_price','Variant rows missing price',missing_price)
    issue('products_missing_images','Products without any image',missing_images,'info')
    issue('products_missing_seo','Products missing SEO title or description',missing_seo,'info')
    penalty=(len(missing_cols)*20 + duplicate_variants*2 + suspicious_default*1.5 + missing_price*1 + missing_images*.25 + missing_seo*.1)
    denom=max(1,variants+products)
    score=max(0,round(100-min(100,penalty/max(1,denom)*100)))
    return {'validator':'shopify','readiness_score':score,'products':products,'variants':variants,'rows':len(rows),'variant_images':variant_images,
        'optioned_variants':optioned,'default_title_variants':default_title,'duplicate_variants':duplicate_variants,'missing_price':missing_price,
        'missing_images':missing_images,'missing_seo':missing_seo,'missing_columns':missing_cols,'issues':issues,'filename':path.name}

class JobManager:
    def __init__(self):
        self.lock=threading.RLock();self.jobs={};self.engines={};self.processes={};self.load()
    def job_dir(self,jid):return JOBS/jid
    def save(self,jid):
        with self.lock:
            j=self.jobs[jid].copy();j.pop('logs_tail',None)
            p=self.job_dir(jid)/'job.json';p.parent.mkdir(parents=True,exist_ok=True)
            tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
    def load(self):
        for p in JOBS.glob('*/job.json'):
            try:
                j=json.loads(p.read_text(encoding='utf-8'));jid=j['id']
                if j.get('status') in ('running','building','waiting_captcha'):
                    j['status']='interrupted';j['message']='Interrupted by application/system restart — ready to resume.';j['running']=False
                pkg=p.parent/'codexifyr-wordpress-migration.zip'
                if pkg.exists():j['package_ready']=True
                self.jobs[jid]=j
                self.save(jid)
            except:pass
    def _id(self,prefix):return f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'
    def new(self,typ,name='',source_url=''):
        jid=self._id('shopify' if typ=='shopify' else ('scan' if typ=='scan' else 'repair'))
        now=time.time();j={'id':jid,'type':typ,'name':name or ('Shopify Product Scrape' if typ=='shopify' else ('Website Scan' if typ=='scan' else 'File Correction')),
            'source_url':source_url,'domain':urlparse(source_url).netloc if source_url else '', 'status':'ready' if typ=='repair' else 'queued',
            'message':'Ready','running':False,'package_ready':False,'progress':0,'created_at':now,'updated_at':now,'started_at':0,'elapsed':0,
            'pages':0,'blogs':0,'products':0,'categories':0,'variants':0,'images':0,'menus':0,'meta_records':0,'platform':'Unknown','logo':'','favicon':'',
            'captcha_waiting':False,'failed':0,'completed_items':0,'total_items':0,'fields':[],'options':{},'logs':['> job created']}
        with self.lock:self.jobs[jid]=j;self.job_dir(jid).mkdir(parents=True,exist_ok=True);self.save(jid)
        return j
    def log(self,jid,msg):
        with self.lock:
            j=self.jobs.get(jid)
            if not j:return
            j.setdefault('logs',[]).append(str(msg));j['logs']=j['logs'][-800:];j['message']=str(msg);j['updated_at']=time.time();self.save(jid)
    def callback(self,jid):
        def cb(message=None,**kw):
            with self.lock:
                j=self.jobs.get(jid)
                if not j:return
                if message:j.setdefault('logs',[]).append(str(message));j['logs']=j['logs'][-800:];j['message']=str(message)
                for k,v in kw.items():
                    if k in ('scanned','discovered'):
                        j[k]=v
                    elif k in ('pages','blogs','products','categories','variants','images','menus','meta_records','logo','favicon','platform','captcha_waiting','progress','failed','completed','total','current','fields'):
                        j[k]=v
                if kw.get('discovered'):
                    j['progress']=min(99,round(float(kw.get('scanned',0))/max(1,float(kw['discovered']))*100,1))
                if kw.get('total'):
                    j['total_items']=kw['total'];j['completed_items']=kw.get('completed',j.get('completed_items',0));j['progress']=kw.get('progress',j.get('progress',0))
                if kw.get('captcha_waiting') is True:j['status']='waiting_captcha'
                elif j.get('running') and j.get('status')=='waiting_captcha' and kw.get('captcha_waiting') is False:j['status']='running'
                j['updated_at']=time.time();self.save(jid)
        return cb
    def start_scan(self,payload,resume_job=None):
        if resume_job:
            jid=resume_job;j=self.jobs[jid];source=j['source_url'];opts=j.get('options') or {};delay=j.get('delay',.35);max_pages=j.get('max_pages',0);max_products=j.get('max_products',0);resume=True
        else:
            source=(payload.get('url') or '').strip()
            if not source:raise ValueError('Enter a website URL.')
            if not source.startswith(('http://','https://')):source='https://'+source
            if not urlparse(source).netloc:raise ValueError('Invalid website URL.')
            j=self.new('scan',urlparse(source).netloc,source);jid=j['id'];opts=payload.get('options') or {};delay=float(payload.get('delay',.35) or .35);max_pages=int(payload.get('max_pages',0) or 0);max_products=int(payload.get('max_products',0) or 0);resume=False
            with self.lock:j.update({'options':opts,'delay':delay,'max_pages':max_pages,'max_products':max_products}) ;self.save(jid)
        with self.lock:
            if j.get('running'):raise RuntimeError('Job is already running.')
            j.update({'running':True,'status':'running','message':'Starting website scan…','started_at':time.time(),'updated_at':time.time(),'captcha_waiting':False});self.save(jid)
        def runner():
            import asyncio
            eng=MigrationEngine(self.job_dir(jid),self.callback(jid));self.engines[jid]=eng
            try:
                result=asyncio.run(eng.run(source,delay,max_pages,opts,resume=resume,max_products=max_products))
                with self.lock:
                    j=self.jobs[jid];j.update(result);j['running']=False;j['updated_at']=time.time();j['elapsed']=int(time.time()-j.get('started_at',time.time()))
                    if eng.stop_requested:j['status']='stopped';j['message']='Stopped — checkpoint saved and ready to resume.'
                    else:j['status']='complete';j['message']='Website scan complete.';j['progress']=100
                    self.save(jid)
                self.log(jid,'> scan complete — ready to validate or build migration package' if not eng.stop_requested else '> scan stopped — checkpoint saved')
            except Exception as e:
                with self.lock:
                    j=self.jobs[jid];j.update({'running':False,'status':'error','message':str(e),'updated_at':time.time()});self.save(jid)
                self.log(jid,'> ERROR: '+str(e))
            finally:self.engines.pop(jid,None)
        threading.Thread(target=runner,daemon=True).start();return self.jobs[jid]
    def _shopify_stats(self,jid):
        jd=self.job_dir(jid);db=jd/'checkpoint.sqlite';out={'products':0,'variants':0,'images':0,'categories':0}
        if not db.exists():return out
        try:
            con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True,timeout=.2);cur=con.cursor();rows=cur.execute('SELECT data FROM products').fetchall()
            out['products']=len(rows)
            for (raw,) in rows:
                try:
                    x=json.loads(raw);out['variants']+=len(x.get('variants') or []);out['images']+=len(x.get('images') or [])
                except:pass
            try:out['categories']=int(cur.execute('SELECT COUNT(*) FROM categories').fetchone()[0] or 0)
            except:pass
            con.close()
        except:pass
        return out
    def start_shopify(self,payload,resume_job=None):
        if not SHOPIFY_SCRAPER.exists():raise RuntimeError('Bundled Shopify scraper is missing.')
        if resume_job:
            jid=resume_job;j=self.jobs[jid];source=j['source_url'];reset=False
        else:
            source=(payload.get('url') or '').strip()
            if not source:raise ValueError('Enter a website URL.')
            if not source.startswith(('http://','https://')):source='https://'+source
            if not urlparse(source).netloc:raise ValueError('Invalid website URL.')
            j=self.new('shopify',urlparse(source).netloc,source);jid=j['id'];reset=bool(payload.get('reset',True))
        if j.get('running'):raise RuntimeError('Shopify scraper job is already running.')
        delay=float(payload.get('delay',j.get('delay',.7)) or .7);retries=int(payload.get('retries',(j.get('options') or {}).get('retries',3)) or 3);timeout=int(payload.get('timeout',(j.get('options') or {}).get('timeout',45000)) or 45000)
        headless=bool(payload.get('headless',(j.get('options') or {}).get('headless',False)))
        jd=self.job_dir(jid);jd.mkdir(parents=True,exist_ok=True)
        
        if getattr(sys,'frozen',False):
            cmd=[sys.executable,'--shopify-worker','--url',source,'--output',str(jd),'--delay',str(delay),'--retries',str(retries),'--timeout',str(timeout)]
        else:
            cmd=[os.environ.get('PYTHON_EXECUTABLE') or sys.executable,'-u',str(SHOPIFY_SCRAPER),'--url',source,'--output',str(jd),'--delay',str(delay),'--retries',str(retries),'--timeout',str(timeout)]
        if reset:cmd.append('--reset')
        if headless:cmd.append('--headless')
        j.update({'running':True,'status':'running','message':'Starting unchanged Shopify product scraper…','started_at':time.time(),'delay':delay,'options':{'retries':retries,'timeout':timeout,'headless':headless},'csv_ready':False});self.save(jid)
        flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) if os.name=='nt' else 0
        proc=subprocess.Popen(cmd,cwd=str(SHOPIFY_SCRAPER.parent),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,creationflags=flags)
        self.processes[jid]=proc
        def reader():
            try:
                assert proc.stdout is not None
                for line in iter(proc.stdout.readline,''):
                    if not line:break
                    text=line.rstrip();low=text.lower()
                    if text:self.log(jid,text)
                    with self.lock:
                        jj=self.jobs.get(jid)
                        if not jj:continue
                        if 'captcha / browser challenge detected' in low or 'press enter after it is cleared' in low:
                            jj['status']='waiting_captcha';jj['captcha_waiting']=True
                        jj.update(self._shopify_stats(jid));self.save(jid)
                code=proc.wait()
                with self.lock:
                    jj=self.jobs[jid];jj.update(self._shopify_stats(jid));jj['running']=False;jj['return_code']=code;jj['elapsed']=int(time.time()-jj.get('started_at',time.time()));jj['csv_ready']=(jd/'shopify_products.csv').exists();jj['progress']=100 if code==0 else jj.get('progress',0);jj['updated_at']=time.time()
                    if code==0:jj['status']='complete';jj['message']='Shopify product scrape complete — CSV ready.'
                    elif jj.get('status')!='stopped':jj['status']='error';jj['message']=f'Shopify scraper exited with code {code}.'
                    self.save(jid)
            finally:self.processes.pop(jid,None)
        threading.Thread(target=reader,daemon=True).start();return j
    def shopify_continue(self,jid):
        proc=self.processes.get(jid)
        if not proc or proc.poll() is not None or not proc.stdin:return False
        proc.stdin.write('\n');proc.stdin.flush()
        with self.lock:self.jobs[jid].update({'status':'running','captcha_waiting':False});self.save(jid)
        self.log(jid,'[WebLab] Continue signal sent after manual CAPTCHA handling.');return True
    def shopify_stop(self,jid):
        proc=self.processes.get(jid)
        if not proc or proc.poll() is not None:return False
        with self.lock:self.jobs[jid].update({'status':'stopped','message':'Stop requested — checkpoint remains available.','running':False});self.save(jid)
        try:
            if os.name=='nt':proc.send_signal(getattr(signal,'CTRL_BREAK_EVENT',signal.SIGTERM))
            else:proc.terminate()
        except:pass
        return True
    def shopify_focus(self,jid):
        if os.name!='nt':return False
        script="$ws=New-Object -ComObject WScript.Shell; foreach($n in @('Chromium','Chrome')){if($ws.AppActivate($n)){exit 0}}; exit 1"
        try:return subprocess.run(['powershell','-NoProfile','-Command',script],capture_output=True,timeout=4).returncode==0
        except:return False

    def create_repair_upload(self,filename,content):
        j=self.new('repair',Path(filename).stem)
        jid=j['id'];orig=self.job_dir(jid)/'original-site-data.json';orig.write_bytes(content)
        try:data=json.loads(orig.read_text(encoding='utf-8'));analysis=analyze_site_data(data)
        except Exception as e:
            j.update({'status':'error','message':'Invalid site-data.json: '+str(e)});self.save(jid);raise
        j.update({'source_url':analysis.get('source_url',''),'domain':urlparse(analysis.get('source_url','')).netloc,'platform':analysis.get('platform','Unknown'),
                  'products':analysis.get('products',0),'variants':analysis.get('variants',0),'pages':analysis.get('pages',0),'categories':analysis.get('categories',0),
                  'images':analysis.get('media',0),'menus':analysis.get('menus',0),'analysis':analysis,'status':'ready','message':'File analyzed — choose repair fields.'})
        self.save(jid);return j
    def start_repair(self,jid,payload,resume=False):
        j=self.jobs[jid]
        if j.get('running'):raise RuntimeError('Repair job is already running.')
        fields=payload.get('fields') or j.get('fields') or []
        auto=bool(payload.get('auto_detect',True));headless=bool(payload.get('headless',False));delay=float(payload.get('delay',.35) or .35);retries=int(payload.get('retries',3) or 3);force_selected=bool(payload.get('force_selected',False))
        j.update({'running':True,'status':'running','message':'Starting targeted repair…','started_at':time.time(),'fields':fields,'options':{'headless':headless,'retries':retries,'force_selected':force_selected},'delay':delay});self.save(jid)
        def runner():
            import asyncio
            eng=RepairEngine(self.job_dir(jid),self.callback(jid));self.engines[jid]=eng
            try:
                result=asyncio.run(eng.run(fields=fields,auto_detect=auto,headless=headless,delay=delay,retries=retries,force_selected=force_selected))
                with self.lock:
                    jj=self.jobs[jid];jj['running']=False;jj['elapsed']=int(time.time()-jj.get('started_at',time.time()));jj['updated_at']=time.time();jj['failed']=result.get('failed',0);jj['completed_items']=result.get('completed',jj.get('completed_items',0));jj['total_items']=result.get('total',jj.get('total_items',0))
                    if eng.stop_requested:jj['status']='stopped';jj['message']='Repair stopped — progress saved; resume anytime.'
                    else:
                        jj['status']='complete';jj['message']='File correction complete.';jj['progress']=100;jj['analysis_after']=result.get('analysis')
                    self.save(jid)
                self.log(jid,'> corrected-site-data.json ready' if not eng.stop_requested else '> repair stopped — delta checkpoint saved')
            except Exception as e:
                with self.lock:self.jobs[jid].update({'running':False,'status':'error','message':str(e),'updated_at':time.time()});self.save(jid)
                self.log(jid,'> REPAIR ERROR: '+str(e))
            finally:self.engines.pop(jid,None)
        threading.Thread(target=runner,daemon=True).start();return j
    def stop(self,jid):
        if self.jobs.get(jid,{}).get('type')=='shopify':return self.shopify_stop(jid)
        eng=self.engines.get(jid)
        if eng:eng.stop();self.log(jid,'> stop requested; saving current progress')
    def continue_captcha(self,jid):
        if self.jobs.get(jid,{}).get('type')=='shopify':return self.shopify_continue(jid)
        eng=self.engines.get(jid)
        return bool(eng and eng.continue_after_captcha())
    def focus(self,jid):
        if self.jobs.get(jid,{}).get('type')=='shopify':return self.shopify_focus(jid)
        eng=self.engines.get(jid)
        return bool(eng and getattr(eng,'request_focus',lambda:False)())
    def resume(self,jid):
        j=self.jobs[jid]
        if j['type']=='scan':return self.start_scan({},resume_job=jid)
        if j['type']=='shopify':return self.start_shopify({'delay':j.get('delay',.7),'retries':(j.get('options') or {}).get('retries',3),'timeout':(j.get('options') or {}).get('timeout',45000),'headless':(j.get('options') or {}).get('headless',False)},resume_job=jid)
        return self.start_repair(jid,{'fields':j.get('fields') or [],'headless':(j.get('options') or {}).get('headless',False),'retries':(j.get('options') or {}).get('retries',3),'delay':j.get('delay',.35),'auto_detect':False,'force_selected':(j.get('options') or {}).get('force_selected',False)},resume=True)
    def build(self,jid):
        j=self.jobs[jid]
        if j.get('running'):raise RuntimeError('Wait for the active scan/repair to finish before building.')
        jd=self.job_dir(jid);data=jd/('corrected-site-data.json' if j['type']=='repair' and (jd/'corrected-site-data.json').exists() else 'site-data.json')
        if j['type']=='repair' and not data.exists():data=jd/'original-site-data.json'
        if not data.exists():raise RuntimeError('No site-data JSON is available for this job.')
        j.update({'status':'building','message':'Building full WordPress migration package…'});self.save(jid)
        def runner():
            try:
                eng=MigrationEngine(jd,self.callback(jid));path=eng.build_wordpress_package(data_path=data)
                with self.lock:self.jobs[jid].update({'status':'complete','package_ready':True,'message':'Full WordPress migration package ready.','updated_at':time.time()});self.save(jid)
                self.log(jid,'> full WordPress migration ZIP ready: '+Path(path).name)
            except Exception as e:
                with self.lock:self.jobs[jid].update({'status':'error','message':'Build failed: '+str(e),'updated_at':time.time()});self.save(jid)
        threading.Thread(target=runner,daemon=True).start()
    def get(self,jid):
        with self.lock:
            if self.jobs[jid].get('type')=='shopify':
                self.jobs[jid].update(self._shopify_stats(jid));self.jobs[jid]['csv_ready']=(self.job_dir(jid)/'shopify_products.csv').exists()
            j=dict(self.jobs[jid])
            if j.get('running') and j.get('started_at'):j['elapsed']=int(time.time()-j['started_at'])
            return j
    def list(self):return sorted([self.get(x) for x in self.jobs],key=lambda j:j.get('created_at',0),reverse=True)

MAN=JobManager()

def parse_multipart(handler):
    ctype=handler.headers.get('Content-Type','');m=re.search(r'boundary=(?:"([^"]+)"|([^;]+))',ctype)
    if not m:raise ValueError('Expected multipart upload.')
    boundary=(m.group(1) or m.group(2)).strip();length=int(handler.headers.get('Content-Length','0') or 0);body=handler.rfile.read(length)
    msg=BytesParser(policy=default).parsebytes((f'Content-Type: multipart/form-data; boundary="{boundary}"\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
    fields={};files={}
    for part in msg.iter_parts():
        name=part.get_param('name',header='content-disposition');filename=part.get_filename();payload=part.get_payload(decode=True) or b''
        if filename:files[name]=(filename,payload)
        else:fields[name]=payload.decode(part.get_content_charset() or 'utf-8',errors='replace')
    return fields,files

class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def safe_write(self,b):
        try:self.wfile.write(b);return True
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError,OSError):return False
    def sendj(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.safe_write(b)
    def send_file(self,f,download_name=None):
        f=Path(f)
        if not f.exists() or not f.is_file():self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(f))[0] or 'application/octet-stream')
        if download_name:self.send_header('Content-Disposition',f'attachment; filename="{download_name}"')
        self.send_header('Content-Length',str(f.stat().st_size));self.send_header('Cache-Control','no-store');self.end_headers()
        try:
            with f.open('rb') as fh:
                while True:
                    chunk=fh.read(1024*1024)
                    if not chunk:break
                    if not self.safe_write(chunk):break
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError,OSError):pass
    def route_static(self,path):
        mapping={'/':'index.html','/scraper':'scraper.html','/repair':'repair.html','/jobs':'jobs.html','/validation':'validation.html','/export':'export.html','/plugin':'plugin.html','/docs':'docs.html','/shopify':'shopify.html','/dashboard':'scraper.html'}
        rel=mapping.get(path,path.lstrip('/'))
        f=(FRONT/rel).resolve()
        if FRONT.resolve() not in f.parents and f!=FRONT.resolve():return False
        if f.exists() and f.is_file():self.send_file(f);return True
        return False
    def do_GET(self):
        u=urlparse(self.path);p=u.path;q=parse_qs(u.query)
        if p=='/api/jobs':self.sendj({'jobs':MAN.list()});return
        m=re.fullmatch(r'/api/jobs/([^/]+)',p)
        if m:
            try:self.sendj(MAN.get(m.group(1)))
            except:self.sendj({'error':'Job not found'},404)
            return
        if p=='/api/job-data':
            jid=(q.get('job') or [''])[0];kind=(q.get('kind') or ['auto'])[0]
            try:
                jd=MAN.job_dir(jid);j=MAN.get(jid)
                candidates=[]
                if kind=='corrected' or (kind=='auto' and j['type']=='repair'):candidates.append(jd/'corrected-site-data.json')
                candidates += [jd/'site-data.json',jd/'original-site-data.json']
                f=next((x for x in candidates if x.exists()),None)
                if not f:self.sendj({'ready':False});return
                # Avoid dumping hundreds of MB into the UI. Return compact summaries/lists.
                d=json.loads(f.read_text(encoding='utf-8'))
                compact={'ready':True,'source_url':d.get('source_url',''),'platform':d.get('platform','Unknown'),'branding':d.get('branding',{}),'menus':d.get('menus',[]),
                         'categories':d.get('categories',[]),'blog_categories':d.get('blog_categories',[]),'tags':d.get('tags',[]),'products':d.get('products',[])[:500],
                         'pages':d.get('pages',[])[:500],'media':d.get('media',[])[:500],'counts':{'products':len(d.get('products',[])),'pages':len(d.get('pages',[])),'media':len(d.get('media',[]))}}
                self.sendj(compact);return
            except Exception as e:self.sendj({'ready':False,'error':str(e)},500);return
        if p=='/api/download':
            jid=(q.get('job') or [''])[0];typ=(q.get('type') or ['package'])[0]
            try:
                jd=MAN.job_dir(jid);j=MAN.get(jid)
                mp={'package':('codexifyr-wordpress-migration.zip','codexifyr-wordpress-migration.zip'),'site':('site-data.json','site-data.json'),
                    'original':('original-site-data.json','original-site-data.json'),'corrected':('corrected-site-data.json','corrected-site-data.json'),
                    'report':('migration-report.json','migration-report.json'),'repair-report':('repair-report.json','repair-report.json'),'shopify-csv':('shopify_products.csv','shopify_products.csv'),'shopify-json':('shopify_products.json','shopify_products.json'),'shopify-report':('scrape_report.json','scrape_report.json')}
                if typ=='shopify-json':ensure_shopify_json(jd/'shopify_products.csv',jd/'shopify_products.json')
                fn,dn=mp.get(typ,mp['package']);self.send_file(jd/fn,dn);return
            except:self.send_error(404);return
        if p=='/api/plugin/download':self.send_file(ensure_plugin_zip(),'codexifyr-migrator-importer.zip');return
        if p=='/api/analyze':
            jid=(q.get('job') or [''])[0];validator=(q.get('validator') or [''])[0]
            try:
                jd=MAN.job_dir(jid);j=MAN.get(jid)
                if validator=='shopify' or j.get('type')=='shopify':
                    self.sendj(validate_shopify_csv(jd/'shopify_products.csv'));return
                candidates=[jd/'corrected-site-data.json',jd/'site-data.json',jd/'original-site-data.json']
                f=next((x for x in candidates if x.exists()),None)
                if not f:raise RuntimeError('No site-data file found for this job.')
                d=json.loads(f.read_text(encoding='utf-8'));out=analyze_site_data(d);out['validator']='wordpress';self.sendj(out);return
            except Exception as e:self.sendj({'error':str(e)},400);return
        if p=='/api/system':
            import sys
            self.sendj({'app_version':'4.2.3 WebLab','plugin_version':'3.0.0','python':sys.version.split()[0],'jobs':len(MAN.jobs),'runtime':str(RUNTIME),'plugin_ready':ensure_plugin_zip().exists()});return
        if self.route_static(p):return
        self.send_error(404)
    def read_json(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        try:return json.loads(self.rfile.read(n) or b'{}')
        except:return {}
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            if p=='/api/jobs/scan':self.sendj({'job':MAN.start_scan(self.read_json())});return
            if p=='/api/jobs/shopify':self.sendj({'job':MAN.start_shopify(self.read_json())});return
            if p=='/api/repair/upload':
                _,files=parse_multipart(self);up=files.get('file')
                if not up:raise ValueError('Choose a site-data.json file.')
                name,content=up
                if name.lower().endswith('.zip'):
                    import zipfile
                    z=zipfile.ZipFile(io.BytesIO(content));names=z.namelist(); cands=[n for n in names if Path(n).name=='corrected-site-data.json'] or [n for n in names if Path(n).name=='site-data.json']
                    if not cands:raise ValueError('ZIP does not contain site-data.json or corrected-site-data.json.')
                    content=z.read(cands[0]);name='site-data.json'
                self.sendj({'job':MAN.create_repair_upload(name,content)});return
            m=re.fullmatch(r'/api/jobs/([^/]+)/(start-repair|stop|pause|focus|continue-captcha|resume|build)',p)
            if m:
                jid,act=m.groups();payload=self.read_json() if act=='start-repair' else {}
                if act=='start-repair':self.sendj({'job':MAN.start_repair(jid,payload)});return
                if act in ('stop','pause'):MAN.stop(jid);self.sendj({'ok':True});return
                if act=='focus':self.sendj({'ok':MAN.focus(jid)});return
                if act=='continue-captcha':self.sendj({'ok':MAN.continue_captcha(jid)});return
                if act=='resume':self.sendj({'job':MAN.resume(jid)});return
                if act=='build':MAN.build(jid);self.sendj({'ok':True});return
            self.sendj({'error':'Not found'},404)
        except KeyError:self.sendj({'error':'Job not found'},404)
        except Exception as e:self.sendj({'error':str(e)},400)
    def log_message(self,*args):pass

def serve(host='127.0.0.1',port=8877):
    print(f'Codexifyr WebLab: http://{host}:{port}/')
    server=ThreadingHTTPServer((host,port),H);server.daemon_threads=True;server.serve_forever()
