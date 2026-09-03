#!/usr/bin/env python3
import asyncio, json, time, shutil, importlib.util
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from .migrator import MigrationEngine, PageRecord, clean, media_url



def _load_locked_shopify_scraper():
    """Load the user's bundled Shopify scraper as a read-only extraction library.

    The bundled tools/shopify_scraper/scraper.py is copied byte-for-byte from the
    supplied working scraper and is never modified by WebLab. Repair only calls
    its public extraction functions to reuse proven product/variant/meta logic.
    """
    root=Path(__file__).resolve().parents[1]
    src=root/'tools'/'shopify_scraper'/'scraper.py'
    spec=importlib.util.spec_from_file_location('codexifyr_locked_shopify_scraper',src)
    if not spec or not spec.loader:return None
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

_LOCKED_SHOPIFY=None
def locked_shopify():
    global _LOCKED_SHOPIFY
    if _LOCKED_SHOPIFY is None:
        try:_LOCKED_SHOPIFY=_load_locked_shopify_scraper()
        except Exception:_LOCKED_SHOPIFY=False
    return _LOCKED_SHOPIFY or None

def shopify_logic_product(html,url):
    """Map the unchanged scraper's Product dataclass to migration schema."""
    mod=locked_shopify()
    if not mod:return {}
    try:
        from dataclasses import asdict
        x=asdict(mod.extract_product(html,url,''))
        variants=[]
        for v in x.get('variants') or []:
            opts=[clean(z) for z in (v.get('options') or []) if clean(z)]
            names=[clean(z) for z in (v.get('option_names') or [])][:len(opts)]
            # Remove generated Option2/Option3 labels when those option slots are empty.
            while len(names)<len(opts):names.append('Option'+str(len(names)+1))
            av=str(v.get('available','')).strip().lower()
            available=av not in ('false','0','no','outofstock','out of stock','unavailable')
            variants.append({'title':clean(v.get('title')) or ' / '.join(opts) or 'Variation',
                'variation_id':'','sku':clean(v.get('sku')),'price':str(v.get('price','') or ''),
                'regular_price':str(v.get('compare_at_price') or v.get('price') or ''),
                'compare_at_price':str(v.get('compare_at_price','') or ''),'available':available,
                'inventory_qty':str(v.get('inventory_qty','') or ('0' if not available else '')),
                'options':opts,'option_names':names,'image':v.get('image','') or ''})
        return {'title':x.get('title',''),'description_html':x.get('body_html','') or x.get('description','') or '',
            'meta_title':x.get('seo_title','') or '', 'meta_description':x.get('seo_description','') or '',
            'images':x.get('images') or [],'featured_image':(x.get('images') or [''])[0],
            'sku':next((v.get('sku','') for v in variants if v.get('sku')),''),'variants':variants}
    except Exception:return {}

def suspicious_variant_product(p):
    vs=p.get('variants') or []
    if not vs:return True
    if len(vs)==1:
        v=vs[0] or {}
        if clean(v.get('title')).lower() in ('default title','variation','') and not (v.get('options') or []):return True
        if not (v.get('option_names') or []) and not (v.get('options') or []):return True
    return False


def analyze_site_data(data):
    products=data.get('products') or []
    pages=data.get('pages') or []
    capture=data.get('capture_options') or {}
    # Older files have no capture_options, so preserve the historical assumption
    # that all sections were intended to be captured.
    intended=lambda key: bool(capture.get(key,True)) if capture else True

    variants=sum(len(p.get('variants') or []) for p in products)
    default_count=sum(1 for p in products if suspicious_variant_product(p))
    real_variations=0; attribute_products=0; attribute_values=0; variant_images=0
    for p in products:
        has_attrs=False
        for v in p.get('variants') or []:
            opts=[clean(x) for x in (v.get('options') or []) if clean(x)]
            names=[clean(x) for x in (v.get('option_names') or []) if clean(x)]
            if opts or names:
                has_attrs=True; attribute_values+=len(opts)
            if opts and clean(v.get('title')).lower() not in ('default title','variation',''):
                real_variations+=1
            if v.get('image'): variant_images+=1
        if has_attrs: attribute_products+=1

    missing_images=sum(1 for p in products if not (p.get('images') or [])) if intended('products') else 0
    content_seo_missing=sum(1 for r in pages if not clean(r.get('meta_title')) or not clean(r.get('meta_description')))
    product_seo_missing=sum(1 for p in products if not clean(p.get('meta_title')) or not clean(p.get('meta_description')))
    missing_seo=(content_seo_missing+product_seo_missing) if intended('seo') else 0
    missing_categories=sum(1 for p in products if not (p.get('categories') or [])) if intended('categories') else 0
    missing_sku=sum(1 for p in products if not clean(p.get('sku')))
    issues=[]
    if intended('products') and not products:
        issues.append({'code':'no_products_captured','severity':'warning','label':'Products were selected but no products are saved','count':0,'recommended':[]})
    if products and variants==len(products):
        issues.append({'code':'products_equal_variants','severity':'warning','label':'Products equal variants','count':variants,'recommended':['variants','variant_images','stock','prices','sku']})
    if default_count:
        issues.append({'code':'default_variants','severity':'warning','label':'Products with fallback/default variations','count':default_count,'recommended':['variants','variant_images','stock','prices','sku']})
    if products and attribute_products==0:
        issues.append({'code':'missing_attributes','severity':'warning','label':'Products with no detected WooCommerce attributes/options','count':len(products),'recommended':['variants']})
    if missing_images:issues.append({'code':'missing_product_images','severity':'warning','label':'Products missing images','count':missing_images,'recommended':['images']})
    if missing_categories:issues.append({'code':'missing_product_categories','severity':'info','label':'Products missing categories','count':missing_categories,'recommended':['categories']})
    if missing_seo:issues.append({'code':'missing_seo','severity':'info','label':'Selected content missing SEO title or description','count':missing_seo,'recommended':['seo']})
    if missing_sku:issues.append({'code':'missing_sku','severity':'info','label':'Products without source SKU (allowed)','count':missing_sku,'recommended':[]})

    denom=max(1,len(products))
    score=100
    if products:
        score-=min(65, default_count/denom*65)
        score-=min(20, (len(products)-attribute_products)/denom*20)
        score-=min(10, missing_images/denom*10)
    if intended('seo') and (pages or products):
        score-=min(5, missing_seo/max(1,len(pages)+len(products))*5)
    score=max(0,round(score))
    return {'source_url':data.get('source_url',''),'platform':data.get('platform','Unknown'),'products':len(products),'variants':variants,
            'real_variations':real_variations,'attribute_products':attribute_products,'attribute_values':attribute_values,'variant_images':variant_images,
            'pages':len(pages),'categories':len(data.get('categories') or []),'media':len(data.get('media') or []),'menus':len(data.get('menus') or []),
            'default_variant_products':default_count,'capture_options':capture,'issues':issues,'readiness_score':score}



class RepairEngine:
    """Targeted field-level repair engine.

    Original JSON is immutable. Every successful repaired record is appended to
    repair-delta.jsonl so a restart can resume without rewriting a huge JSON file.
    A corrected-site-data.json is written once at completion (or on explicit finalize).
    """
    def __init__(self, job_dir, state_cb=None):
        self.job_dir=Path(job_dir); self.job_dir.mkdir(parents=True,exist_ok=True)
        self.state_cb=state_cb or (lambda **kw:None)
        self.stop_requested=False; self.captcha_waiting=False; self.browser=None; self.page=None
        self.helper=MigrationEngine(self.job_dir,self._helper_cb)
        self._completed=set()
    def _helper_cb(self,message=None,**kw):
        if message:self.state_cb(message=message,**kw)
        if 'captcha_waiting' in kw:self.captcha_waiting=kw['captcha_waiting']
    def emit(self,message=None,**kw):self.state_cb(message=message,**kw)
    def stop(self):
        self.stop_requested=True; self.helper.stop()
    def continue_after_captcha(self):return self.helper.continue_after_captcha()
    def request_focus(self):return self.helper.request_focus()

    def _paths(self):
        return (self.job_dir/'original-site-data.json',self.job_dir/'corrected-site-data.json',self.job_dir/'repair-delta.jsonl',self.job_dir/'repair-report.json')

    def load_original(self):
        original,_,delta,_=self._paths()
        data=json.loads(original.read_text(encoding='utf-8'))
        if delta.exists():
            by_url={p.get('url'):p for p in data.get('products') or [] if p.get('url')}
            page_by_url={p.get('url'):p for p in data.get('pages') or [] if p.get('url')}
            for line in delta.read_text(encoding='utf-8',errors='ignore').splitlines():
                try:d=json.loads(line)
                except:continue
                u=d.get('url'); typ=d.get('type')
                if typ=='product' and u in by_url:by_url[u].update(d.get('changes') or {})
                elif typ=='page' and u in page_by_url:page_by_url[u].update(d.get('changes') or {})
                elif typ=='global':data.update(d.get('changes') or {})
                self._completed.add(u)
        return data

    def append_delta(self,obj):
        _,_,delta,_=self._paths()
        with delta.open('a',encoding='utf-8') as f:
            f.write(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n')
            f.flush()

    async def run(self, fields=None, auto_detect=True, headless=False, delay=.35, retries=3, force_selected=False):
        fields=set(fields or [])
        data=self.load_original()
        analysis=analyze_site_data(data)
        if auto_detect and not fields:
            for issue in analysis['issues']:fields.update(issue.get('recommended') or [])
        if not fields:fields={'variants'}
        source=data.get('source_url') or ''
        self.helper.source_root=source
        self.helper.options={'delay':delay,'retries':retries,'headless':headless}
        products=data.get('products') or []
        target_products=[]
        variant_fields={'variants','variant_images','stock','prices','sku'}
        for p in products:
            needs=False
            if fields & variant_fields and suspicious_variant_product(p):needs=True
            if 'images' in fields and not (p.get('images') or []):needs=True
            if 'categories' in fields and not (p.get('categories') or []):needs=True
            if 'seo' in fields and (not p.get('meta_title') or not p.get('meta_description')):needs=True
            if force_selected and fields & (variant_fields|{'images','categories','seo'}):needs=True
            if needs and p.get('url') and p.get('url') not in self._completed:target_products.append(p)
        target_pages=[]
        if fields & {'seo','pages','blogs'}:
            for r in data.get('pages') or []:
                kind=r.get('kind','page')
                if kind in ('product','category'):continue
                if 'pages' in fields and kind=='page':need=True
                elif 'blogs' in fields and kind=='blog':need=True
                elif 'seo' in fields:need=force_selected or not r.get('meta_title') or not r.get('meta_description')
                else:need=False
                if need and r.get('url') and r.get('url') not in self._completed:target_pages.append(r)
        global_needed=bool(fields & {'branding','menus'}) and data.get('source_url') not in self._completed
        # Explicit variants repair revisits fallback products; unrelated good records stay locked.
        total=len(target_products)+len(target_pages)+(1 if global_needed else 0)
        repaired=0; failed=[]; started=time.time()
        self.emit(message=f'> repair analysis: {total} product URLs need targeted source repair',total=total,completed=len(self._completed),fields=sorted(fields))
        if total==0:
            corrected=self.finalize(data,fields,failed,analysis)
            return {'completed':0,'total':0,'failed':0,'corrected':str(corrected),'analysis':analysis}

        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=bool(headless));self.browser=browser
            ctx=await browser.new_context(viewport={'width':1440,'height':1000})
            page=await ctx.new_page();self.page=page;self.helper.page=page;self.helper.browser=browser
            for idx,p in enumerate(target_products,1):
                if self.stop_requested:break
                url=p.get('url')
                self.emit(message=f'> repairing {idx}/{total}: {url}',current=idx,total=total,completed=repaired,failed=len(failed),progress=round((idx-1)/max(1,total)*100,1))
                ok=await self.helper.goto(page,url)
                if not ok:
                    failed.append(url);continue
                try:
                    html=await self.helper.safe_content(page);soup=BeautifulSoup(html,'lxml')
                    # First reuse the exact, unchanged Shopify scraper extraction logic supplied by the user.
                    # It already handles WooCommerce data-product_variations, generic selects/swatches,
                    # variant images and SEO. Then merge with WebLab's browser-aware extractor for dynamic cases.
                    locked_fresh=shopify_logic_product(html,url)
                    rr=self.helper.extract_page(soup,url,'product')
                    fresh=await self.helper.extract_product(soup,url,rr,page)
                    locked_vs=locked_fresh.get('variants') or []
                    current_vs=fresh.get('variants') or []
                    locked_real=locked_vs and not (len(locked_vs)==1 and clean(locked_vs[0].get('title')).lower()=='default title' and not locked_vs[0].get('options'))
                    current_real=current_vs and not (len(current_vs)==1 and clean(current_vs[0].get('title')).lower()=='default title' and not current_vs[0].get('options'))
                    if locked_real and (not current_real or len(locked_vs)>=len(current_vs)):
                        fresh['variants']=locked_vs
                    for k in ('meta_title','meta_description','description_html','sku'):
                        if locked_fresh.get(k) and not fresh.get(k):fresh[k]=locked_fresh[k]
                    if locked_fresh.get('images') and not fresh.get('images'):fresh['images']=locked_fresh['images']
                    if 'variant_images' in fields and fresh.get('variants'):
                        fresh['variants']=await self.helper.enrich_variant_galleries(page,fresh['variants'],url)
                    changes={}
                    if fields & variant_fields:
                        fvs=fresh.get('variants') or []
                        # Do not replace a record with another fallback/default variant.
                        if fvs and not (len(fvs)==1 and clean(fvs[0].get('title')).lower()=='default title' and not fvs[0].get('options')):
                            changes['variants']=fvs
                            if 'sku' in fields and fresh.get('sku'):changes['sku']=fresh.get('sku')
                    if 'images' in fields and fresh.get('images'):changes['images']=fresh['images'];changes['featured_image']=fresh.get('featured_image','')
                    if 'categories' in fields and fresh.get('categories'):changes['categories']=fresh['categories']
                    if 'seo' in fields:
                        if fresh.get('meta_title'):changes['meta_title']=fresh['meta_title']
                        if fresh.get('meta_description'):changes['meta_description']=fresh['meta_description']
                    if changes:
                        p.update(changes)
                        self.append_delta({'type':'product','url':url,'changes':changes,'repaired_at':time.time()})
                        repaired+=1;self._completed.add(url)
                    else:
                        failed.append(url)
                        self.emit(message=f'> warning: no trustworthy replacement data found for {url}')
                except Exception as e:
                    failed.append(url);self.emit(message=f'> repair error: {url} — {e}')
                if delay:await asyncio.sleep(max(.03,float(delay)))
            base_idx=len(target_products)
            for jx,r in enumerate(target_pages,1):
                if self.stop_requested:break
                url=r.get('url');cur=base_idx+jx
                self.emit(message=f'> repairing content {cur}/{total}: {url}',current=cur,total=total,completed=repaired,failed=len(failed),progress=round((cur-1)/max(1,total)*100,1))
                ok=await self.helper.goto(page,url)
                if not ok:failed.append(url);continue
                try:
                    soup=BeautifulSoup(await self.helper.safe_content(page),'lxml');fresh=self.helper.extract_page(soup,url,r.get('kind','page'));changes={}
                    if 'seo' in fields:
                        if fresh.meta_title:changes['meta_title']=fresh.meta_title
                        if fresh.meta_description:changes['meta_description']=fresh.meta_description
                    if ('pages' in fields and r.get('kind')=='page') or ('blogs' in fields and r.get('kind')=='blog'):
                        changes.update({'title':fresh.title,'body_html':fresh.body_html,'excerpt':fresh.excerpt,'featured_image':fresh.featured_image,'images':fresh.images})
                        if r.get('kind')=='blog':changes.update({'categories':fresh.categories,'tags':fresh.tags,'published_date':fresh.published_date,'author':fresh.author})
                    if changes:r.update(changes);self.append_delta({'type':'page','url':url,'changes':changes,'repaired_at':time.time()});repaired+=1;self._completed.add(url)
                    else:failed.append(url)
                except Exception as e:failed.append(url);self.emit(message=f'> content repair error: {url} — {e}')
                if delay:await asyncio.sleep(max(.03,float(delay)))
            if global_needed and not self.stop_requested:
                url=data.get('source_url');cur=len(target_products)+len(target_pages)+1
                self.emit(message=f'> repairing global branding/navigation {cur}/{total}: {url}',current=cur,total=total,completed=repaired,progress=round((cur-1)/max(1,total)*100,1))
                if await self.helper.goto(page,url):
                    try:
                        soup=BeautifulSoup(await self.helper.safe_content(page),'lxml');changes={}
                        if 'branding' in fields:
                            logo,fav=self.helper.extract_branding(soup,url);changes['branding']={'logo':logo or (data.get('branding') or {}).get('logo',''),'favicon':fav or (data.get('branding') or {}).get('favicon','')}
                        if 'menus' in fields:changes['menus']=self.helper.extract_menus(soup,url)
                        if changes:data.update(changes);self.append_delta({'type':'global','url':url,'changes':changes,'repaired_at':time.time()});repaired+=1;self._completed.add(url)
                    except Exception as e:failed.append(url);self.emit(message=f'> branding/menu repair error: {e}')
                else:failed.append(url)
            await browser.close();self.browser=None;self.page=None;self.helper.browser=None;self.helper.page=None
        corrected=self.finalize(data,fields,failed,analysis)
        return {'completed':repaired,'total':total,'failed':len(failed),'corrected':str(corrected),'elapsed':int(time.time()-started),'analysis':analyze_site_data(data)}

    def finalize(self,data,fields,failed,analysis_before=None):
        original,corrected,_,report=self._paths()
        tmp=corrected.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(corrected)
        after=analyze_site_data(data)
        rep={'generated_at':time.time(),'original_file':original.name,'corrected_file':corrected.name,'source_url':data.get('source_url',''),
             'fields_requested':sorted(fields),'failed_urls':failed,'before':analysis_before or {},'after':after,
             'note':'Original JSON was not overwritten. Only selected/repaired fields were merged.'}
        report.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
        return corrected
