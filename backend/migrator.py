#!/usr/bin/env python3
import threading
import asyncio, json, re, time, zipfile, shutil, html as htmllib, itertools, traceback, importlib.util
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from dataclasses import dataclass, asdict, field
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip()
def canon(u):
    p=urlparse(u); path=re.sub(r'/{2,}','/',p.path or '/')
    return urlunparse((p.scheme.lower(),p.netloc.lower(),path,'','',''))

NON_PAGE_EXTENSIONS={
    '.jpg','.jpeg','.png','.gif','.webp','.avif','.svg','.ico','.bmp','.tif','.tiff',
    '.pdf','.zip','.rar','.7z','.gz','.tar',
    '.css','.js','.mjs','.map','.json','.xml','.txt',
    '.woff','.woff2','.ttf','.otf','.eot',
    '.mp4','.webm','.mov','.avi','.mkv','.mp3','.wav','.ogg','.m4a',
    '.doc','.docx','.xls','.xlsx','.ppt','.pptx'
}
IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.gif','.webp','.avif','.svg','.ico','.bmp','.tif','.tiff'}

def is_page_url(u):
    """True only for URLs that are reasonable HTML-page crawl targets."""
    try:
        p=urlparse(u)
        path=(p.path or '/').lower()
        if '/wp-content/uploads/' in path:
            return False
        blocked=('/cart','/checkout','/my-account','/account','/login','/logout','/wp-admin','/admin')
        if any(path==x or path.startswith(x+'/') for x in blocked):
            return False
        suffix=Path(path).suffix.lower()
        if suffix in NON_PAGE_EXTENSIONS:
            return False
        return p.scheme in ('http','https')
    except:
        return False

def media_url(u,base=''):
    """
    Normalize one media URL for storage/deduplication.
    - absolute URL
    - removes query/fragment duplicates
    - collapses common WordPress generated sizes like image-300x300.jpg
      back to image.jpg so srcset thumbnails do not count as separate images
    """
    if not u:
        return ''
    raw=urljoin(base,str(u).strip()) if base else str(u).strip()
    if not raw or raw.startswith(('data:','blob:','javascript:')):
        return ''
    try:
        p=urlparse(raw)
        if p.scheme not in ('http','https'):
            return ''
        path=re.sub(r'/{2,}','/',p.path or '/')
        # WordPress generates resized files with -WIDTHxHEIGHT before extension.
        if '/wp-content/uploads/' in path.lower():
            path=re.sub(r'-\d{2,5}x\d{2,5}(?=\.[A-Za-z0-9]{2,6}$)','',path)
        return urlunparse((p.scheme.lower(),p.netloc.lower(),path,'','',''))
    except:
        return ''
def slugify(x): return re.sub(r'-+','-',re.sub(r'[^a-z0-9]+','-',clean(x).lower())).strip('-')
def money(x):
    m=re.search(r'(\d+(?:\.\d{1,2})?)',clean(x).replace(',',''))
    return m.group(1) if m else ''
def uniq(seq):
    out=[]; seen=set()
    for x in seq:
        k=json.dumps(x,sort_keys=True,ensure_ascii=False) if isinstance(x,(dict,list)) else str(x)
        if k not in seen: seen.add(k); out.append(x)
    return out

@dataclass
class PageRecord:
    url:str
    kind:str='page'
    title:str=''
    slug:str=''
    meta_title:str=''
    meta_description:str=''
    body_html:str=''
    excerpt:str=''
    featured_image:str=''
    images:list=field(default_factory=list)
    categories:list=field(default_factory=list)
    tags:list=field(default_factory=list)
    published_date:str=''
    author:str=''

class MigrationEngine:
    def __init__(self, output_dir, state_cb=None):
        self.output=Path(output_dir); self.output.mkdir(parents=True,exist_ok=True)
        self.state_cb=state_cb or (lambda **kw:None)
        self.stop_requested=False; self.records=[]; self.products=[]; self.categories={}
        self.blog_categories={}; self.tags={}; self.menus=[]; self.media=set()
        self.logo=''; self.favicon=''; self.platform='Unknown'; self.design={}; self.options={}; self.source_root=''; self.browser=None; self.page=None
        self._locked_shopify_module=None
        self.captcha_waiting=False
        self._captcha_continue=threading.Event()
        self._focus_requested=threading.Event()

    def emit(self,message=None,**stats): self.state_cb(message=message,**stats)
    def stop(self): self.stop_requested=True
    def request_focus(self): self._focus_requested.set(); return True
    async def _apply_focus_request(self,page):
        if self._focus_requested.is_set():
            self._focus_requested.clear()
            try: await page.bring_to_front()
            except: pass

    async def safe_content(self,page):
        """Product-scraper style safe page.content() for pages still settling/navigation."""
        for attempt in range(5):
            try:
                return await page.content()
            except Exception as e:
                if 'navigating' not in str(e).lower():
                    raise
                try:
                    await page.wait_for_load_state('domcontentloaded',timeout=5000)
                except:
                    pass
                await asyncio.sleep(0.5*(attempt+1))
        return await page.content()

    async def challenge_detected(self,page,response=None):
        """
        Same deliberately narrow CAPTCHA/challenge test used by the working
        Codexifyr product scraper. HTTP status alone is NOT a CAPTCHA signal.
        This avoids false positives from temporary HTTP 202 responses and from
        challenge-related scripts that remain in a normal homepage after clearance.
        """
        try:
            text=(await page.locator('body').inner_text(timeout=2500)).lower()
            title=(await page.title()).lower()
            markup=(await self.safe_content(page)).lower()
        except:
            return False

        text_markers=(
            'captcha','recaptcha','hcaptcha','verify you are human',
            'checking your browser','attention required','security check',
            'robot challenge'
        )
        markup_markers=('sgcaptcha','/.well-known/sgcaptcha','cf-chl-')

        return (
            any(x in text or x in title for x in text_markers)
            or any(x in markup for x in markup_markers)
        )

    async def wait_for_manual_captcha(self,page,url):
        """
        Safe manual CAPTCHA handling based on the working product scraper.

        - Keeps the same browser page/session open.
        - NEVER reloads the page after the user solves the challenge.
        - Automatically resumes if the challenge page redirects to the real site.
        - The dashboard Continue button is still available for manual confirmation.
        """
        self.captcha_waiting=True
        self._captcha_continue.clear()
        self.emit(
            message='> CAPTCHA / browser challenge detected — solve it in Chromium. The scan will resume automatically when cleared, or click CONTINUE AFTER CAPTCHA.',
            captcha_waiting=True
        )
        try:
            await page.bring_to_front()
        except:
            pass

        cleared_automatically=False

        while not self.stop_requested:
            # Some anti-bot pages clear themselves after a few seconds.
            try:
                if not await self.challenge_detected(page):
                    cleared_automatically=True
                    break
            except:
                pass

            if self._captcha_continue.is_set():
                break

            await page.wait_for_timeout(750)

        if self.stop_requested:
            self.captcha_waiting=False
            self._captcha_continue.clear()
            self.emit(captcha_waiting=False)
            return False

        self._captcha_continue.clear()

        # Product scraper behavior: let any client-side redirect/navigation settle.
        try:
            await page.wait_for_load_state('domcontentloaded',timeout=5000)
        except:
            pass
        await page.wait_for_timeout(500)

        # If user clicked Continue too early, do not reload or create a new challenge.
        # Stay paused until the actual challenge has cleared.
        if await self.challenge_detected(page):
            self.emit(
                message='> CAPTCHA is still visible — finish solving it; waiting without reloading the page…',
                captcha_waiting=True
            )
            while not self.stop_requested:
                try:
                    if not await self.challenge_detected(page):
                        break
                except:
                    pass
                await page.wait_for_timeout(750)

        if self.stop_requested:
            self.captcha_waiting=False
            self.emit(captcha_waiting=False)
            return False

        self.captcha_waiting=False
        self.emit(
            message=('> CAPTCHA cleared automatically — resuming scan'
                     if cleared_automatically
                     else '> CAPTCHA cleared — resuming scan'),
            captcha_waiting=False
        )
        return True

    def continue_after_captcha(self):
        if not self.captcha_waiting:
            return False
        self._captcha_continue.set()
        return True

    async def goto(self,page,url):
        """
        Navigation behavior mirrors the working Codexifyr product scraper:
        navigate -> settle -> detect visible challenge -> wait manually -> continue
        in the SAME page/session. No post-CAPTCHA reload.
        """
        retry_count=max(0,int(self.options.get('retries',3) or 0))
        attempts=retry_count+1
        last_error=''

        for attempt in range(attempts):
            if self.stop_requested:
                return False
            await self._apply_focus_request(page)

            response=None
            try:
                response=await page.goto(url,wait_until='domcontentloaded',timeout=50000)
            except PWTimeout:
                last_error='page load timeout'
            except Exception as e:
                # Preserve a browser-side redirect if it stayed on the source domain.
                try:
                    if urlparse(page.url).netloc.lower()!=urlparse(self.source_root or url).netloc.lower():
                        last_error=str(e)
                    else:
                        last_error=''
                except:
                    last_error=str(e)
                low=last_error.lower()
                network_markers=('err_internet_disconnected','err_name_not_resolved','err_network_changed','err_connection_closed','err_connection_reset','temporary failure in name resolution')
                if any(x in low for x in network_markers):
                    self.emit(message='> network unavailable — waiting for connection; this URL will not be skipped')
                    while not self.stop_requested:
                        await asyncio.sleep(5)
                        await self._apply_focus_request(page)
                        try:
                            response=await page.goto(url,wait_until='domcontentloaded',timeout=20000)
                            last_error=''
                            break
                        except Exception as ne:
                            last_error=str(ne)
                            if not any(x in last_error.lower() for x in network_markers):break
                    if self.stop_requested:return False

            try:
                await page.wait_for_load_state('networkidle',timeout=5000)
            except:
                pass

            if await self.challenge_detected(page,response):
                ok=await self.wait_for_manual_captcha(page,url)
                if not ok:
                    return False

                # Same as the proven product scraper: after clearance, do NOT reload.
                try:
                    await page.wait_for_load_state('domcontentloaded',timeout=5000)
                except:
                    pass

                if not await self.challenge_detected(page):
                    try:
                        await page.wait_for_timeout(int(float(self.options.get('delay',0.35))*1000))
                    except:
                        pass
                    return True

                last_error='challenge still present'
            else:
                # HTTP 202 is allowed if the browser has already reached real page content.
                try:
                    html=await self.safe_content(page)
                except:
                    html=''
                try:
                    status=int(response.status) if response is not None else 200
                except:
                    status=200

                # Permanent missing pages are not worth retrying.
                if status in (404,410):
                    self.emit(message=f'> permanent HTTP {status}; skipped without retries: {url}')
                    return False

                # Normal success, including 2xx responses such as 202.
                if status < 400 and len(html.strip()) >= 200:
                    try:
                        await page.wait_for_timeout(int(float(self.options.get('delay',0.35))*1000))
                    except:
                        pass
                    return True

                last_error=f'HTTP {status}' if status >= 400 else 'empty/incomplete HTML response'

            if attempt < attempts-1 and not self.stop_requested:
                self.emit(message=f'> retry {attempt+1}/{retry_count} for {url} — {last_error}')
                await page.wait_for_timeout(min(5000,1000*(attempt+1)))

        self.emit(message=f'> failed after {attempts} attempts: {url} — {last_error}')
        return False

    def _has_product_schema(self,soup):
        for node in soup.select('script[type="application/ld+json"]'):
            try: data=json.loads(node.string or node.get_text() or '{}')
            except: continue
            stack=data if isinstance(data,list) else [data]
            while stack:
                obj=stack.pop()
                if isinstance(obj,list): stack.extend(obj); continue
                if not isinstance(obj,dict): continue
                typ=obj.get('@type','')
                types=[str(x).lower() for x in typ] if isinstance(typ,list) else [str(typ).lower()]
                if 'product' in types:return True
                graph=obj.get('@graph')
                if isinstance(graph,list):stack.extend(graph)
        return False

    def _detect_platform(self,html,soup=None):
        t=(html or '').lower()
        if any(x in t for x in ('cdn.shopify.com','shopify.theme','shopify.routes','myshopify.com','shopify-section')):
            return 'Shopify'
        if any(x in t for x in ('woocommerce','wc-ajax','wp-content','wp-json')):
            return 'WooCommerce / WordPress'
        return 'Generic'

    def classify(self,soup,url):
        """Classify by page evidence first, URL hints second.

        This matters for Shopify -> WordPress migrations: /products/* is a Product,
        /collections/* is a product category, /policies/* and /pages/* are Pages.
        """
        body=' '.join(soup.body.get('class',[])).lower() if soup.body else ''
        p=urlparse(url).path.lower().rstrip('/') or '/'
        product_evidence=(
            self._has_product_schema(soup)
            or any(x in body for x in ('single-product','product-template','template-product','type-product'))
            or soup.select_one('form.variations_form,form.cart,.single_add_to_cart_button,[name="add-to-cart"],.product-form,.product-single__form,[data-product-variations],product-form')
        )
        if product_evidence or '/product/' in p or '/products/' in p:return 'product'
        if 'single-post' in body or soup.select_one('article.type-post') or any(x in p for x in ('/blog/','/blogs/','/news/','/journal/')):return 'blog'
        if 'tax-product_cat' in body or any(x in p for x in ('/collection/','/collections/','/product-category/')):return 'category'
        return 'page'

    def _load_locked_shopify_scraper(self):
        """Load the bundled proven Shopify scraper WITHOUT modifying its source file."""
        if self._locked_shopify_module is not None:return self._locked_shopify_module
        src=Path(__file__).resolve().parent.parent/'tools'/'shopify_scraper'/'scraper.py'
        spec=importlib.util.spec_from_file_location('codexifyr_locked_shopify_scraper',src)
        if not spec or not spec.loader:return None
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        self._locked_shopify_module=mod
        return mod

    def _locked_scraper_variants(self,html,url):
        """Run the bundled proven product scraper's extraction logic read-only.

        The bundled scraper.py is never edited.  This bridge only converts its Variant
        dataclasses into WebLab's normalized WordPress/WooCommerce representation.
        It is intentionally platform-neutral because the proven scraper already knows
        Shopify embedded variants, WooCommerce variation forms, generic Size/Color
        selects and swatch-style attributes.
        """
        mod=self._load_locked_shopify_scraper()
        if not mod:return []
        try:
            prod=mod.extract_product(html,url,'')
            raw=asdict(prod) if hasattr(prod,'__dataclass_fields__') else dict(prod)
        except Exception as e:
            self.emit(message=f'> locked product-variant bridge fallback: {e}')
            return []
        out=[]
        for vv in raw.get('variants') or []:
            v=asdict(vv) if hasattr(vv,'__dataclass_fields__') else dict(vv)
            opts=[clean(x) for x in (v.get('options') or [])]
            names=[clean(x) for x in (v.get('option_names') or [])]
            while opts and not opts[-1]:opts.pop()
            while len(names)>len(opts):names.pop()
            # A Default Title / option-less row is not useful as an attribute recovery source.
            if not opts:
                continue
            available=str(v.get('available','')).lower()
            in_stock=available not in ('false','0','outofstock','out of stock','https://schema.org/outofstock')
            out.append({
                'variation_id':clean(v.get('id') or v.get('variation_id')),
                'title':clean(v.get('title')) or ' / '.join(opts),
                'sku':clean(v.get('sku')),
                'price':money(v.get('price')),
                'regular_price':money(v.get('compare_at_price')) or money(v.get('price')),
                'compare_at_price':money(v.get('compare_at_price')),
                'available':in_stock,
                'inventory_qty':clean(v.get('inventory_qty')),
                'options':opts,
                'option_names':names or ['Option'+str(i+1) for i in range(len(opts))],
                'image':media_url(v.get('image'),url),
                'images':uniq([media_url(x,url) for x in (v.get('images') or []) if media_url(x,url)])
            })
        return self._dedupe_variants(out)

    def _shopify_product_adapter(self,html,url,r):
        """Normalize the LOCKED scraper's product result for WordPress migration JSON.

        IMPORTANT: the bundled Shopify scraper is treated as read-only.  This adapter may
        supplement its normalized result for WordPress migration, but never edits scraper.py.
        """
        mod=self._load_locked_shopify_scraper()
        if not mod:return None
        try:
            prod=mod.extract_product(html,url,'')
            raw=asdict(prod) if hasattr(prod,'__dataclass_fields__') else dict(prod)
        except Exception as e:
            self.emit(message=f'> Shopify product adapter fallback: {e}')
            return None
        images=uniq([media_url(x,url) for x in (raw.get('images') or []) if media_url(x,url)])
        variants=[]
        for vv in raw.get('variants') or []:
            v=asdict(vv) if hasattr(vv,'__dataclass_fields__') else dict(vv)
            opts=[clean(x) for x in (v.get('options') or [])]
            names=[clean(x) for x in (v.get('option_names') or [])]
            # Trim empty trailing placeholders but preserve real custom attribute names/values.
            while opts and not opts[-1]:opts.pop()
            while len(names)>len(opts):names.pop()
            available=str(v.get('available','')).lower()
            in_stock=available not in ('false','0','outofstock','out of stock','https://schema.org/outofstock')
            variants.append({
                'variation_id':clean(v.get('id') or v.get('variation_id')),
                'title':clean(v.get('title')) or (' / '.join(opts) if opts else 'Default Title'),
                'sku':clean(v.get('sku')),
                'price':money(v.get('price')),
                'regular_price':money(v.get('compare_at_price')) or money(v.get('price')),
                'compare_at_price':money(v.get('compare_at_price')),
                'available':in_stock,
                'inventory_qty':clean(v.get('inventory_qty')),
                'options':opts,
                'option_names':names,
                'image':media_url(v.get('image'),url),
                'images':uniq([media_url(x,url) for x in (v.get('images') or []) if media_url(x,url)])
            })
        variants=self._dedupe_variants(variants)

        # Supplemental DOM dropdown support for Shopify themes/apps that do not expose
        # the full variant array in static script JSON.  The proven scraper remains
        # untouched; this only repairs the WordPress-normalization adapter.  Some themes
        # use a generic <select name="id"> / <select class="single-option-selector">
        # whose nearby label says "Select Size".  The older adapter missed those because
        # the select's own name/id did not contain "size" or "option".
        if not variants or all(not (v.get('options') or []) for v in variants):
            try:
                soup=BeautifulSoup(html,'lxml')
                product_scope=(soup.select_one('product-form, .product-form, form[action*="/cart/add"], form[action$="/cart/add"], .product__info-container, .product-single, main') or soup)
                dropdown_names=[]; dropdown_values=[]
                for sel in product_scope.select('select'):
                    # Ignore obvious quantity/sorting/currency selectors.
                    sig=' '.join([clean(sel.get('name')),clean(sel.get('id')),' '.join(sel.get('class') or [])]).lower()
                    if any(x in sig for x in ('quantity','qty','sort','orderby','currency','country','province')):
                        continue
                    label=''
                    sid=sel.get('id')
                    if sid:
                        lab=soup.find('label',attrs={'for':sid})
                        if lab:label=clean(lab.get_text(' ',strip=True))
                    if not label:
                        prev=sel.find_previous(['label','legend'])
                        if prev:label=clean(prev.get_text(' ',strip=True))
                    data_name=clean(sel.get('data-option-name') or sel.get('data-name') or sel.get('aria-label'))
                    name=data_name or label or clean(sel.get('name') or sel.get('id'))
                    name=re.sub(r'^(?:select\s+|choose\s+)', '', name, flags=re.I).strip(' :*')
                    if 'size' in (name+' '+label).lower():name='Size'
                    elif 'colo' in (name+' '+label).lower():name='Color'
                    elif name.lower() in ('id','variant','variant id','product id',''):name='Option'+str(len(dropdown_names)+1)
                    vals=[]
                    for opt in sel.select('option'):
                        txt=clean(opt.get_text(' ',strip=True) or opt.get('value'))
                        low=txt.lower().rstrip(' .…:;-')
                        if not txt or low in ('select an option','select option','choose an option','choose option','select size','choose size') or low.startswith('select an option'):
                            continue
                        # Generic instruction placeholders such as "Select an option...".
                        if re.match(r'^(?:please\s+)?(?:select|choose|pick)\b',low):
                            continue
                        if txt not in vals:vals.append(txt)
                    # A real option dropdown has at least one non-placeholder value.
                    if vals:
                        dropdown_names.append(name or 'Option'+str(len(dropdown_names)+1))
                        dropdown_values.append(vals)
                    if len(dropdown_names)>=3:break
                if dropdown_names and dropdown_values:
                    from itertools import product as cartesian_product
                    supplemental=[]
                    for combo in cartesian_product(*dropdown_values):
                        supplemental.append({
                            'variation_id':'','title':' / '.join(combo),'sku':'','price':'','regular_price':'','compare_at_price':'',
                            'available':True,'inventory_qty':'','options':list(combo),'option_names':dropdown_names[:len(combo)],
                            'image':'','images':[]
                        })
                    # Prefer meaningful dropdown options over a fallback Default Title.
                    if supplemental:
                        variants=self._dedupe_variants(supplemental)
                        self.emit(message=f'> Shopify dropdown attributes detected: {", ".join(dropdown_names)} ({len(variants)} option combinations)')
            except Exception as e:
                self.emit(message=f'> Shopify dropdown supplement skipped: {e}')

        return {
            'url':url,'slug':raw.get('handle') or r.slug,'title':raw.get('title') or r.title,
            'description_html':raw.get('body_html') or r.body_html,'short_description_html':'',
            'meta_title':raw.get('seo_title') or r.meta_title,'meta_description':raw.get('seo_description') or r.meta_description,
            'images':images or r.images,'featured_image':((images or r.images) or [''])[0],
            'categories':uniq(raw.get('categories') or ([raw.get('category')] if raw.get('category') else [])),
            'tags':uniq(raw.get('tags') or []),'sku':'','variants':variants
        }

    def meta(self,soup,name,prop=False):
        n=soup.find('meta',attrs={'property' if prop else 'name':name})
        return clean(n.get('content')) if n else ''

    def extract_page(self,soup,url,kind):
        title=clean(soup.title.get_text() if soup.title else '')
        h1=soup.find('h1'); title1=clean(h1.get_text(' ',strip=True)) if h1 else title

        # Keep the actual page-builder/content area, not only a tiny article fragment.
        main=soup.select_one(
            'main,#main,.site-content,.main-page-wrapper,.website-wrapper,'
            '.page-content,.entry-content,.content-area,[role="main"],article'
        )
        if not main and soup.body:
            main=soup.body

        imgs=[]
        # Capture normal/lazy/srcset images from the content area.
        for im in (main or soup).select('img'):
            candidates=[
                im.get('data-large_image'), im.get('data-src'), im.get('data-lazy-src'),
                im.get('data-original'), im.get('src')
            ]
            ss=im.get('srcset') or im.get('data-srcset')
            if ss:
                for bit in ss.split(','):
                    if bit.strip():
                        candidates.append(bit.strip().split()[0])
            for u in candidates:
                if u and not str(u).startswith(('data:','blob:')):
                    imgs.append(media_url(u,url))

        # Capture picture/source assets.
        for srcn in (main or soup).select('source[srcset]'):
            for bit in (srcn.get('srcset') or '').split(','):
                u=bit.strip().split()[0] if bit.strip() else ''
                if u and not u.startswith(('data:','blob:')):
                    imgs.append(media_url(u,url))

        # Capture inline CSS background banners.
        bg_re=re.compile(r'url\([\'"]?([^\'")]+)',re.I)
        for n in (main or soup).select('[style*="background"],[style*="background-image"]'):
            for u in bg_re.findall(n.get('style') or ''):
                if u and not u.startswith(('data:','blob:')):
                    imgs.append(media_url(u,url))

        # OpenGraph/Twitter often contain the hero/featured image.
        feat=self.meta(soup,'og:image',True) or self.meta(soup,'twitter:image')
        if feat: imgs.append(media_url(feat,url))

        body_html=''
        if main:
            try:
                frag=BeautifulSoup(str(main),'html.parser')
                for bad in frag.select('script,style,noscript,template'):
                    bad.decompose()
                body_html=''.join(str(x) for x in frag.contents)
            except:
                body_html=str(main)

        r=PageRecord(
            url=url,kind=kind,title=title1,
            slug=urlparse(url).path.rstrip('/').split('/')[-1] or 'home',
            meta_title=title,
            meta_description=self.meta(soup,'description') or self.meta(soup,'og:description',True),
            body_html=body_html,
            excerpt=self.meta(soup,'og:description',True),
            featured_image=media_url(feat,url) if feat else '',
            images=uniq([x for x in imgs if x])
        )

        if kind=='blog':
            tm=soup.find('time')
            r.published_date=clean(tm.get('datetime') or tm.get_text(' ',strip=True)) if tm else ''
            au=soup.select_one('[rel="author"],.author,.post-author,.entry-author,.author-name')
            r.author=clean(au.get_text(' ',strip=True)) if au else ''
            r.categories=uniq([
                clean(a.get_text(' ',strip=True))
                for a in soup.select('.cat-links a,.post-categories a,a[rel="category tag"],.entry-meta a[href*="/category/"]')
                if clean(a.get_text(' ',strip=True))
            ])
            r.tags=uniq([
                clean(a.get_text(' ',strip=True))
                for a in soup.select('.tag-links a,a[rel="tag"],.tags-links a')
                if clean(a.get_text(' ',strip=True))
            ])
        return r

    def menu_tree(self,ul,base,depth=0):
        if not ul or depth>6:return []
        out=[]
        for li in ul.find_all('li',recursive=False):
            a=li.find('a',recursive=False) or li.find('a')
            if not a:continue
            title=clean(a.get_text(' ',strip=True))
            if not title:continue
            sub=li.find(['ul','ol'],recursive=False)
            out.append({'title':title,'url':urljoin(base,a.get('href') or '#'),'children':self.menu_tree(sub,base,depth+1)})
        return out

    def extract_menus(self,soup,base):
        found=[]; used=set()
        for name,sel in [('Primary Menu','header ul.menu'),('Primary Menu','header nav ul'),('Mobile Menu','.mobile-nav ul'),('Footer Menu','footer ul.menu'),('Footer Menu','footer nav ul')]:
            for ul in soup.select(sel):
                if ul.find_parent(['ul','ol']): continue
                items=self.menu_tree(ul,base); key=json.dumps(items,sort_keys=True)
                if items and key not in used:
                    used.add(key); found.append({'name':name+' '+str(1+sum(x['name'].startswith(name) for x in found)) if any(x['name'].startswith(name) for x in found) else name,'items':items})
        return found

    def extract_branding(self,soup,base):
        logo=''
        selectors=(
            'header img.custom-logo','img.custom-logo','header .site-logo img',
            'header .wd-logo img','header .logo img','header [class*="logo"] img',
            'a.custom-logo-link img','[class*="site-brand"] img'
        )
        for sel in selectors:
            n=soup.select_one(sel)
            if not n: continue
            u=n.get('data-src') or n.get('data-lazy-src') or n.get('src')
            if not u:
                ss=n.get('srcset') or n.get('data-srcset')
                if ss:
                    bits=[x.strip().split()[0] for x in ss.split(',') if x.strip()]
                    if bits:u=bits[-1]
            if u and not str(u).startswith('data:'):
                logo=media_url(u,base);break

        fav=''
        # Prefer site icon / apple icon / shortcut icon.
        preferred=[]
        for n in soup.find_all('link',href=True):
            rel=' '.join(n.get('rel',[])).lower()
            if 'icon' in rel:
                score=0
                if 'apple-touch-icon' in rel: score+=1
                if 'shortcut icon' in rel or rel=='icon': score+=3
                sizes=clean(n.get('sizes'))
                m=re.search(r'(\d+)',sizes)
                if m: score+=min(int(m.group(1)),512)/512
                preferred.append((score,media_url(n['href'],base)))
        if preferred:fav=sorted(preferred,key=lambda x:x[0],reverse=True)[0][1]
        return logo,fav

    def product_categories(self,soup,url):
        out=[]
        for a in soup.select('.woocommerce-breadcrumb a,.breadcrumb a,.breadcrumbs a,.product_meta .posted_in a'):
            href=urljoin(url,a.get('href') or '')
            if '/product-category/' in href or '/collection/' in href or '/category/' in href:
                t=clean(a.get_text(' ',strip=True))
                if t:out.append(t)
        return uniq(out)

    def _variant_attribute_schema(self,soup):
        """Collect WooCommerce + generic attribute controls without assuming layout."""
        form=soup.select_one('form.variations_form') or soup.select_one('form.cart') or soup
        names=[]; values={}
        seen=set()
        for sel in form.select('select[name]'):
            key=clean(sel.get('name'))
            if not key or ('attribute_' not in key and not key.startswith('pa_')):
                continue
            lab=key.replace('attribute_','').replace('pa_','').replace('-',' ').replace('_',' ').title()
            label=soup.find('label',attrs={'for':sel.get('id')}) if sel.get('id') else None
            if label: lab=clean(label.get_text(' ',strip=True)) or lab
            vals=[]
            for o in sel.find_all('option'):
                val=clean(o.get('value'))
                txt=clean(o.get_text(' ',strip=True))
                if val and val not in ('0','-1') and txt.lower() not in ('choose an option','select an option','choose option','select option'):
                    vals.append({'value':val,'label':txt or val})
            if key not in seen:
                seen.add(key); names.append((key,lab)); values[key]=vals
        # Swatches/radios are often cosmetic mirrors of hidden selects. Keep them as fallback.
        if not names:
            groups={}
            for n in soup.select('[data-attribute_name],[data-attribute-name],[name^="attribute_"],[class*="swatch"] [data-value]'):
                key=clean(n.get('data-attribute_name') or n.get('data-attribute-name') or n.get('name'))
                val=clean(n.get('data-value') or n.get('value'))
                if key and val:
                    groups.setdefault(key,[]).append({'value':val,'label':clean(n.get('title') or n.get('aria-label') or n.get_text(' ',strip=True)) or val})
            for key,vals in groups.items():
                lab=key.replace('attribute_','').replace('pa_','').replace('-',' ').replace('_',' ').title()
                names.append((key,lab)); values[key]=uniq(vals)
        return form,names,values

    def _parse_variation_array(self,raw):
        if not raw:return []
        candidates=[raw,htmllib.unescape(raw)]
        for txt in candidates:
            try:
                arr=json.loads(txt)
                if isinstance(arr,list):return arr
            except: pass
        return []

    def _variation_from_wc(self,v,names,url):
        attrs=v.get('attributes') or {}
        opts=[]; option_names=[]
        for k,lab in names:
            val=clean(attrs.get(k,''))
            if not val:
                # Some plugins use keys without the attribute_ prefix.
                val=clean(attrs.get(k.replace('attribute_',''),''))
            opts.append(val); option_names.append(lab)
        im=v.get('image') or v.get('variation_image') or {}
        if isinstance(im,dict): image=im.get('full_src') or im.get('src') or im.get('url') or ''
        else: image=im if isinstance(im,str) else ''
        reg=str(v.get('display_regular_price',v.get('regular_price','')) or '')
        price=str(v.get('display_price',v.get('price','')) or '')
        in_stock=v.get('is_in_stock',v.get('is_purchasable',v.get('available',True)))
        return {'title':' / '.join([x for x in opts if x]) or clean(v.get('variation_description')) or 'Variation',
                'variation_id':str(v.get('variation_id',v.get('id','')) or ''),
                'sku':clean(v.get('sku')),'price':price,'regular_price':reg,
                'compare_at_price':reg if reg and reg!=price else '',
                'available':bool(in_stock),'inventory_qty':'0' if in_stock is False else '',
                'options':opts,'option_names':option_names,'image':media_url(image,url) if image else ''}

    def woo_variants(self,soup,url):
        """Fast HTML/embedded-data pass. Kept for compatibility and non-browser uses."""
        form,names,all_vals=self._variant_attribute_schema(soup)
        if not names:return []
        arr=[]
        if form:
            arr=self._parse_variation_array(form.get('data-product_variations'))
        # Some themes move the same JSON to arbitrary data attributes/scripts.
        if not arr:
            for n in soup.select('[data-product_variations]'):
                arr=self._parse_variation_array(n.get('data-product_variations'))
                if arr:break
        if not arr:
            html=str(soup)
            for m in re.finditer(r'(?s)(\[\s*\{[^\[]*?"variation_id".*?\}\s*\])',html):
                arr=self._parse_variation_array(m.group(1))
                if arr:break
        out=[]
        if arr:
            for v in arr:
                if isinstance(v,dict):out.append(self._variation_from_wc(v,names,url))
        return out

    async def woo_variants_robust(self,page,soup,url):
        """Layered variation engine for WooCommerce and custom layouts.

        1) embedded WooCommerce variation JSON
        2) JS DOM data after page scripts settle
        3) WooCommerce get_variation AJAX per attribute combination
        4) browser interaction fallback for custom controls
        It never invents combinations when the source can confirm valid ones.
        """
        form,names,all_vals=self._variant_attribute_schema(soup)
        if not names:
            return []
        out=self.woo_variants(soup,url)
        if out:
            return self._dedupe_variants(out)

        # Read jQuery/DOM data after JavaScript initialization.
        try:
            arr=await page.evaluate("""() => {
              const f=document.querySelector('form.variations_form');
              if(!f) return null;
              const a=f.getAttribute('data-product_variations');
              if(a && a !== 'false') { try { return JSON.parse(a); } catch(e){} }
              if(window.jQuery){ const d=window.jQuery(f).data('product_variations'); if(Array.isArray(d)) return d; }
              return null;
            }""")
            if isinstance(arr,list) and arr:
                out=[self._variation_from_wc(v,names,url) for v in arr if isinstance(v,dict)]
                if out:return self._dedupe_variants(out)
        except: pass

        product_id=''
        if form:
            product_id=clean(form.get('data-product_id'))
            if not product_id:
                pid=form.select_one('input[name="product_id"],input[name="add-to-cart"]')
                product_id=clean(pid.get('value')) if pid else ''

        keys=[k for k,_ in names]
        value_lists=[]
        for k in keys:
            value_lists.append([x['value'] for x in all_vals.get(k,[]) if x.get('value')])
        combos=list(itertools.product(*value_lists)) if value_lists and all(value_lists) else []
        # Guard huge malformed forms. Real stores normally have far fewer combinations.
        if len(combos)>240:
            self.emit(message=f'> variation combinations capped at 240 for {url}; using interactive discovery')
            combos=combos[:240]

        # WooCommerce AJAX is much faster than clicking every option and returns real combinations only.
        if product_id and combos:
            endpoint=urljoin(url,'/?wc-ajax=get_variation')
            ctx=getattr(page,'context',None)
            request=getattr(ctx,'request',None) if ctx else None
            if request:
                ajax_out=[]
                for combo in combos:
                    if self.stop_requested:break
                    data={'product_id':product_id}
                    for k,v in zip(keys,combo): data[k]=v
                    try:
                        res=await request.post(endpoint,form=data,timeout=12000)
                        if res.ok:
                            v=await res.json()
                            if isinstance(v,dict) and v.get('variation_id'):
                                ajax_out.append(self._variation_from_wc(v,names,url))
                    except: pass
                if ajax_out:
                    return self._dedupe_variants(ajax_out)

        # Last resort: exercise the actual page controls. This supports custom swatch plugins
        # that update hidden WooCommerce selects and the variation image when clicked.
        if combos and page:
            interactive=[]
            for combo in combos:
                if self.stop_requested:break
                ok=True
                for k,v in zip(keys,combo):
                    try:
                        await page.locator(f'select[name="{k}"]').select_option(v,timeout=2500)
                    except:
                        ok=False; break
                if not ok:continue
                try: await page.wait_for_timeout(120)
                except: pass
                try:
                    d=await page.evaluate("""() => {
                      const f=document.querySelector('form.variations_form');
                      if(!f)return null;
                      const id=(f.querySelector('input.variation_id')||{}).value||'';
                      if(!id)return null;
                      const sku=(document.querySelector('.sku')||{}).textContent||'';
                      const price=(document.querySelector('.single_variation .price .amount,.single_variation .price')||{}).textContent||'';
                      const img=document.querySelector('.woocommerce-product-gallery__image.flex-active-slide img,.woocommerce-product-gallery img');
                      const stock=(document.querySelector('.single_variation .stock')||{}).textContent||'';
                      return {id,sku,price,image:img?(img.getAttribute('data-large_image')||img.currentSrc||img.src):'',stock};
                    }""")
                    if d and d.get('id'):
                        opts=[clean(x) for x in combo]
                        pr=money(d.get('price'))
                        available='out of stock' not in clean(d.get('stock')).lower()
                        interactive.append({'title':' / '.join(opts),'variation_id':str(d.get('id')),'sku':clean(d.get('sku')),
                            'price':pr,'regular_price':pr,'compare_at_price':'','available':available,
                            'inventory_qty':'0' if not available else '','options':opts,'option_names':[n for _,n in names],
                            'image':media_url(d.get('image'),url) if d.get('image') else ''})
                except: pass
            if interactive:return self._dedupe_variants(interactive)
        return []

    async def enrich_variant_galleries(self,page,variants,url):
        """Capture dynamic gallery images associated with a real option selection.
        Stored as variant['images']; standard WooCommerce uses the first as the
        variation thumbnail, while extra images remain available to migration data.
        """
        if not page or not variants:return variants
        for v in variants:
            names=v.get('option_names') or [];opts=v.get('options') or []
            if not names or not opts:continue
            selected=True
            for name,val in zip(names,opts):
                # Resolve by label/name heuristics because stored display names may differ from attribute keys.
                try:
                    sels=page.locator('form.variations_form select[name^="attribute_"]')
                    count=await sels.count();matched=False
                    target=name.lower().replace(' ','').replace('-','').replace('_','')
                    for i in range(count):
                        el=sels.nth(i);nm=(await el.get_attribute('name') or '').lower().replace('attribute_','').replace('pa_','').replace('-','').replace('_','')
                        if target in nm or nm in target:
                            try:await el.select_option(str(val),timeout=1800);matched=True;break
                            except:
                                # Try option label if stored value is display text.
                                try:await el.select_option(label=str(val),timeout=1800);matched=True;break
                                except:pass
                    if not matched:selected=False;break
                except:selected=False;break
            if not selected:continue
            try:await page.wait_for_timeout(160)
            except:pass
            try:
                imgs=await page.evaluate("""() => [...document.querySelectorAll('.woocommerce-product-gallery img,.woocommerce-product-gallery__image img,[class*=variation] [class*=gallery] img')].map(i=>i.getAttribute('data-large_image')||i.currentSrc||i.src).filter(Boolean)""")
                imgs=uniq([media_url(x,url) for x in (imgs or []) if media_url(x,url)])
                if imgs:
                    v['images']=imgs
                    if not v.get('image'):v['image']=imgs[0]
            except:pass
        return variants

    def _dedupe_variants(self,variants):
        out=[]; seen=set()
        for v in variants:
            key=(str(v.get('variation_id') or ''),tuple(v.get('options') or []))
            if not key[0] and not any(key[1]):
                key=('fallback',v.get('title',''))
            if key in seen:continue
            seen.add(key);out.append(v)
        return out

    async def extract_product(self,soup,url,r,page=None):
        short=soup.select_one('.woocommerce-product-details__short-description')
        desc=soup.select_one('#tab-description,.woocommerce-Tabs-panel--description')
        pbox=soup.select_one('.summary .price,.summary p.price')
        price=money(pbox.get_text(' ',strip=True) if pbox else '')
        deln=soup.select_one('.summary .price del,.summary p.price del'); reg=money(deln.get_text(' ',strip=True)) if deln else ''
        sku=soup.select_one('.sku'); sku=clean(sku.get_text(' ',strip=True)) if sku else ''
        imgs=[]
        pselectors=(
            '.woocommerce-product-gallery img,.woocommerce-product-gallery__image img,'
            '.product-images img,[class*="product-gallery"] img,[class*="product-image"] img,'
            '.product-image-summary img,.woocommerce-product-gallery source[srcset]'
        )
        for im in soup.select(pselectors):
            candidates=[im.get('data-large_image'),im.get('data-src'),im.get('data-lazy-src'),im.get('data-original'),im.get('src')]
            ss=im.get('srcset') or im.get('data-srcset')
            if ss:candidates += [x.strip().split()[0] for x in ss.split(',') if x.strip()]
            for u in candidates:
                if u and not str(u).startswith(('data:','blob:')):imgs.append(media_url(u,url))
        for node in soup.select('script[type="application/ld+json"]'):
            try: jd=json.loads(node.string or node.get_text() or '{}')
            except: continue
            stack=jd if isinstance(jd,list) else [jd]
            for obj in stack:
                if isinstance(obj,dict) and str(obj.get('@type','')).lower()=='product':
                    ji=obj.get('image') or []
                    if isinstance(ji,str):ji=[ji]
                    if isinstance(ji,list):
                        for u in ji:
                            if isinstance(u,str):imgs.append(media_url(u,url))
        cats=self.product_categories(soup,url)
        tags=uniq([clean(a.get_text(' ',strip=True)) for a in soup.select('.tagged_as a') if clean(a.get_text(' ',strip=True))])
        vs=await self.woo_variants_robust(page,soup,url) if page else self.woo_variants(soup,url)

        # The proven standalone product scraper already handles several Size/Color dropdown
        # patterns that are not registered as native WooCommerce `attribute_*` controls
        # (for example product-addon/custom select fields). Reuse that exact extraction logic
        # as a read-only supplement instead of reimplementing it here.
        locked_vs=self._locked_scraper_variants(str(soup),url)
        current_has_options=bool(vs and any(v.get('options') for v in vs))
        locked_has_options=bool(locked_vs and any(v.get('options') for v in locked_vs))
        if locked_has_options and not current_has_options:
            vs=locked_vs
            labels=[]
            for v in locked_vs:
                for n in v.get('option_names') or []:
                    if n and n not in labels: labels.append(n)
            self.emit(message=f'> product attributes recovered by locked scraper engine: {", ".join(labels) or "custom options"} ({len(locked_vs)} variants)')

        if not vs:
            stock=soup.select_one('.stock'); available=not(stock and 'out of stock' in clean(stock.get_text()).lower())
            vs=[{'title':'Default Title','sku':sku,'price':price,'regular_price':reg or price,'compare_at_price':reg if reg and reg!=price else '',
                 'available':available,'inventory_qty':'0' if not available else '','options':[],'option_names':[],'image':(imgs or r.images or [''])[0]}]
        for v in vs:
            if not v.get('price'):v['price']=price
            if not v.get('regular_price'):v['regular_price']=reg or price
            if reg and reg!=price and not v.get('compare_at_price'):v['compare_at_price']=reg
        return {'url':url,'slug':r.slug,'title':r.title,'description_html':str(desc) if desc else r.body_html,
                'short_description_html':str(short) if short else '','meta_title':r.meta_title,'meta_description':r.meta_description,
                'images':uniq([x for x in (imgs+r.images) if x]),'featured_image':(uniq([x for x in (imgs+r.images) if x]) or [''])[0],
                'categories':cats,'tags':tags,'sku':sku,'variants':vs}

    async def focus_browser(self):
        if self.page:
            try:
                await self.page.bring_to_front()
                return True
            except:
                return False
        return False

    async def sitemap_urls(self,page,root):
        urls=set(); seen=set()
        async def fetch(u,depth=0):
            if u in seen or depth>4:return
            seen.add(u)
            try:
                res=await page.request.get(u,timeout=20000)
                if not res.ok:return
                txt=await res.text()
            except:return
            soup=BeautifulSoup(txt,'xml')
            for loc in soup.find_all('loc'):
                x=clean(loc.get_text())
                if not x:continue
                if x.endswith('.xml') or 'sitemap' in urlparse(x).path.lower(): await fetch(x,depth+1)
                elif urlparse(x).netloc.lower()==urlparse(root).netloc.lower():
                    cx=canon(x)
                    if is_page_url(cx):
                        urls.add(cx)
        for u in (urljoin(root,'/sitemap_index.xml'),urljoin(root,'/wp-sitemap.xml'),urljoin(root,'/sitemap.xml')):await fetch(u)
        return urls

    def _capture_enabled_for_kind(self, kind):
        if kind=='product': return bool(self.options.get('products',True))
        if kind=='category': return bool(self.options.get('categories',True))
        if kind=='blog': return bool(self.options.get('blogs',True))
        return bool(self.options.get('pages',True))

    def _apply_seo_policy(self, record):
        if not self.options.get('seo',True):
            record.meta_title=''
            record.meta_description=''
        return record

    def _rebuild_media(self):
        """Rebuild exported media strictly from capture types the user selected.

        Discovery pages may expose thousands of assets, but they are navigation only
        unless their content type is selected. Product images remain part of Products.
        """
        media=set()
        for r in self.records:
            media.update(media_url(x) for x in (r.images or []) if x)
            if r.featured_image: media.add(media_url(r.featured_image))
        if self.options.get('products',True):
            for p in self.products:
                media.update(media_url(x) for x in (p.get('images') or []) if x)
                if p.get('featured_image'): media.add(media_url(p.get('featured_image')))
                for v in p.get('variants') or []:
                    if v.get('image'): media.add(media_url(v.get('image')))
                    media.update(media_url(x) for x in (v.get('images') or []) if x)
        if self.options.get('categories',True):
            for c in self.categories.values():
                media.update(media_url(x) for x in (c.get('images') or []) if x)
                if c.get('featured_image'): media.add(media_url(c.get('featured_image')))
        if self.options.get('branding',True):
            if self.logo: media.add(media_url(self.logo))
            if self.favicon: media.add(media_url(self.favicon))
        if self.options.get('design',True):
            media.update(media_url(x) for x in (self.design.get('homepage_images') or []) if x)
        self.media={x for x in media if x}

    async def run(self,url,delay=.35,max_pages=0,options=None,resume=False,max_products=0):
        self.options=options or {}
        self.stop_requested=False
        self.captcha_waiting=False
        self._captcha_continue.clear()
        self.records=[]; self.products=[]; self.categories={}
        self.blog_categories={}; self.tags={}; self.menus=[]; self.media=set()
        self.logo=''; self.favicon=''; self.platform='Unknown'; self.design={}
        if not re.match(r'^https?://',url):url='https://'+url
        root=canon(url); self.source_root=root; host=urlparse(root).netloc.lower()
        seen=set(); discovered={root}; resume_queue=[]
        checkpoint_path=self.output/'scan-checkpoint.json'
        data_path=self.output/'site-data.json'
        if resume and checkpoint_path.exists() and data_path.exists():
            try:
                cp=json.loads(checkpoint_path.read_text(encoding='utf-8'))
                d=json.loads(data_path.read_text(encoding='utf-8'))
                seen=set(cp.get('seen') or [])
                discovered=set(cp.get('discovered') or [root])
                resume_queue=list(cp.get('queue') or [])
                self.platform=d.get('platform','Unknown')
                b=d.get('branding') or {};self.logo=b.get('logo','');self.favicon=b.get('favicon','')
                self.menus=(d.get('menus') or []) if self.options.get('menus',True) else []
                self.design=(d.get('design') or {}) if self.options.get('design',True) else {}
                self.products=(d.get('products') or []) if self.options.get('products',True) else []
                self.categories={x.get('name'):x for x in (d.get('categories') or []) if x.get('name')} if self.options.get('categories',True) else {}
                self.blog_categories={x.get('name'):x for x in (d.get('blog_categories') or []) if x.get('name')} if self.options.get('blogs',True) else {}
                self.tags={x.get('name'):x for x in (d.get('tags') or []) if x.get('name')} if self.options.get('blogs',True) else {}
                self.records=[]
                for rr in (d.get('pages') or []):
                    kind=rr.get('kind','page')
                    if (kind=='blog' and not self.options.get('blogs',True)) or (kind!='blog' and not self.options.get('pages',True)):
                        continue
                    rec=PageRecord(**{k:v for k,v in rr.items() if k in PageRecord.__dataclass_fields__})
                    self.records.append(self._apply_seo_policy(rec))
                if not self.options.get('branding',True): self.logo=self.favicon=''
                self._rebuild_media()
                self.emit(message=f'> resumed checkpoint: {len(seen)} URLs already completed; {len(resume_queue)} remaining')
            except Exception as e:
                self.emit(message=f'> checkpoint could not be restored; starting clean: {e}')
                seen=set();discovered={root};resume_queue=[]

        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=bool(self.options.get('headless',False)))
            self.browser=browser
            ctx=await browser.new_context(viewport={'width':1440,'height':1000})
            page=await ctx.new_page(); self.page=page

            # Establish a real browser session FIRST. Protected WordPress/WooCommerce
            # sites may refuse sitemap/API requests until browser cookies/challenges are solved.
            self.emit(message='> opening homepage first to establish browser session',scanned=0,discovered=1)
            preflight_ok=await self.goto(page,root)
            if not preflight_ok:
                if self.stop_requested:
                    await browser.close()
                    return self.summary(root,len(seen),len(discovered))
                raise RuntimeError('Homepage could not be loaded after retries/manual challenge handling.')

            def pri(u):
                if canon(u)==root:return -100
                p=urlparse(u).path.lower().rstrip('/')
                # Prioritize selected capture types, while still allowing unselected
                # archive/pagination pages to act as discovery paths.
                if p in ('','/'): return -100
                is_product=('/product/' in p or '/products/' in p)
                is_blog=any(x in p for x in ('/blog/','/news/','/journal/'))
                is_category=any(x in p for x in ('/product-category/','/collection/','/collections/','/category/'))
                is_page=any(x in p for x in ('/about','/contact','/faq','/shipping','/returns','/privacy','/terms','/pages/'))
                if is_product and self.options.get('products',True): return 0
                if is_page and self.options.get('pages',True): return 1
                if is_blog and self.options.get('blogs',True): return 1
                if is_category and self.options.get('categories',True): return 2
                if is_category: return 3  # useful discovery path, not exported unless selected
                if is_product: return 4
                if is_blog or is_page: return 5
                return 6

            if resume_queue:
                queue=[u for u in resume_queue if u not in seen and is_page_url(u)]
                self.emit(message=f'> continuing saved queue; {len(seen)} completed / {len(discovered)} discovered',scanned=len(seen),discovered=len(discovered))
            else:
                # Homepage MUST remain the first content record on a clean scan.
                queue=[] if root in seen else [root]
                sitemap=set(await self.sitemap_urls(page,root))
                discovered.update(sitemap)
                queue += [u for u in sorted(sitemap,key=lambda u:(pri(u),u)) if u!=root and u not in seen and is_page_url(u)]
                self.emit(message=f'> homepage queued first; {len(discovered)} URLs discovered from sitemaps',scanned=len(seen),discovered=len(discovered))

            while queue and not self.stop_requested:
                if max_pages and len(seen)>=int(max_pages):break
                if max_products and self.options.get('products',True) and len(self.products)>=int(max_products):
                    self.emit(message=f'> max products reached ({int(max_products)}); finishing scan')
                    break
                current=queue.pop(0)
                if current in seen:continue
                if not is_page_url(current):
                    continue
                self.emit(message=f'> scanning {current}',scanned=len(seen)+1,discovered=len(discovered))
                loaded=await self.goto(page,current)
                if not loaded:
                    if self.stop_requested: break
                    continue
                seen.add(current)
                html=await self.safe_content(page); soup=BeautifulSoup(html,'lxml')

                if current==root:
                    self.platform=self._detect_platform(html,soup)

                kind=self.classify(soup,current)
                r=self._apply_seo_policy(self.extract_page(soup,current,kind))
                capture_this=self._capture_enabled_for_kind(kind)

                if kind=='product' and self.options.get('products',True):
                    # For Shopify source sites, reuse the proven LOCKED Shopify scraper's
                    # extraction logic through an adapter. The original scraper file remains
                    # byte-for-byte unchanged and still produces its own CSV separately.
                    if self.platform=='Shopify' or '/products/' in urlparse(current).path.lower():
                        p=self._shopify_product_adapter(html,current,r)
                    else:
                        p=None
                    if not p:
                        p=await self.extract_product(soup,current,r,page)
                    if not self.options.get('seo',True):
                        p['meta_title']=''; p['meta_description']=''
                    # Category taxonomy/assignments are controlled by the Categories checkbox.
                    if not self.options.get('categories',True):
                        p['categories']=[]
                    self.products.append(p)
                    if max_products and len(self.products)>=int(max_products):
                        self.emit(message=f'> product limit progress: {len(self.products)}/{int(max_products)}')
                    if self.options.get('categories',True):
                        for c in p.get('categories') or []:
                            self.categories.setdefault(c,{'name':c,'slug':slugify(c),'parent':'','url':'','meta_title':'','meta_description':''})

                elif kind=='category' and self.options.get('categories',True):
                    self.categories[r.title]={
                        'name':r.title,'slug':r.slug,'parent':'','url':current,
                        'meta_title':r.meta_title if self.options.get('seo',True) else '',
                        'meta_description':r.meta_description if self.options.get('seo',True) else '',
                        'images':r.images,'featured_image':r.featured_image,
                        'description_html':r.body_html
                    }

                elif kind=='blog' and self.options.get('blogs',True):
                    self.records.append(r)
                    for c in r.categories:self.blog_categories.setdefault(c,{'name':c,'slug':slugify(c)})
                    for t in r.tags:self.tags.setdefault(t,{'name':t,'slug':slugify(t)})

                elif kind=='page' and self.options.get('pages',True):
                    self.records.append(r)

                if not capture_this:
                    self.emit(message=f'> discovery only — {kind} not selected, so it was not saved: {current}',scanned=len(seen),discovered=len(discovered))

                # Capture global branding/navigation/design specifically from homepage,
                # not whichever sitemap URL happened to be scanned first.
                if current==root:
                    if self.options.get('menus',True):
                        self.menus=self.extract_menus(soup,current)
                    if self.options.get('branding',True):
                        self.logo,self.favicon=self.extract_branding(soup,current)
                        if self.logo:self.media.add(media_url(self.logo))
                        if self.favicon:self.media.add(media_url(self.favicon))
                    if self.options.get('design',True):
                        self.design={
                            'body_classes':' '.join(soup.body.get('class',[])) if soup.body else '',
                            'stylesheets':[urljoin(current,x.get('href')) for x in soup.select('link[rel="stylesheet"][href]')],
                            'header_html':str(soup.select_one('header') or ''),
                            'footer_html':str(soup.select_one('footer') or ''),
                            'homepage_html':r.body_html,
                            'homepage_images':r.images
                        }
                    self.emit(
                        message=f'> homepage processed — selected exports only | logo: {"yes" if self.logo else "not selected/detected"} | favicon: {"yes" if self.favicon else "not selected/detected"} | menus: {len(self.menus)}',
                        scanned=len(seen),discovered=len(discovered)
                    )

                # Discover normal links too. Selected content is prioritized; in Products-only
                # mode only product URLs and useful product-discovery paths are queued.
                newly=[]
                for a in soup.find_all('a',href=True):
                    u=urljoin(current,a['href']); p=urlparse(u)
                    if p.scheme not in ('http','https') or p.netloc.lower()!=host:continue
                    if any(x in p.path.lower() for x in ('/account','/cart','/checkout','/login','/logout','/wp-admin','/admin','/my-account')):continue

                    # Asset links are media/resources, not pages. Keep image references
                    # if useful, but never navigate Chromium to them.
                    ext=Path(p.path.lower()).suffix
                    if '/wp-content/uploads/' in p.path.lower() or ext in NON_PAGE_EXTENSIONS:
                        continue

                    u=canon(u)
                    if not is_page_url(u):
                        continue

                    # Products-only mode: use the homepage/menu/sitemap and product archive
                    # paths as navigation, but do not waste crawl budget on policy/blog/info
                    # pages that cannot lead the selected export. This changes navigation only;
                    # saved output is still controlled independently by capture checkboxes.
                    selected_core=[k for k in ('pages','blogs','products','categories') if self.options.get(k,True)]
                    products_only=(selected_core==['products'])
                    if products_only:
                        lp=urlparse(u).path.lower().rstrip('/') or '/'
                        is_prod=('/product/' in lp or '/products/' in lp)
                        is_discovery=(lp in ('/','/shop','/collections','/collection','/product-category')
                                      or any(x in lp for x in ('/collections/','/collection/','/product-category/','/category/','/shop/')))
                        # Shopify collection pagination and generic/Woo shop/category pagination are allowed.
                        if not (is_prod or is_discovery):
                            continue
                    if u not in discovered:
                        discovered.add(u); newly.append(u)
                if newly:
                    queue.extend(sorted(newly,key=lambda u:(pri(u),u)))
                    # Re-sort remainder so selected Products stay ahead of discovery archives.
                    queue=sorted(dict.fromkeys(queue),key=lambda u:(pri(u),u))

                # Rebuild exported media from selected capture types only, then save.
                self._rebuild_media()
                # Write partial site-data.json after every visited URL so selected-output
                # counters remain live without exporting discovery-only content.
                self.write_data(root)
                try:
                    checkpoint_path.write_text(json.dumps({'source_url':root,'seen':sorted(seen),'discovered':sorted(discovered),'queue':queue,'updated_at':time.time(),'complete':False},ensure_ascii=False),encoding='utf-8')
                except Exception:
                    pass
                stats=self.summary(root,len(seen),len(discovered))
                self.emit(
                    scanned=len(seen),discovered=len(discovered),
                    pages=stats['pages'],blogs=stats['blogs'],products=stats['products'],
                    categories=stats['categories'],variants=stats['variants'],
                    images=stats['images'],menus=stats['menus'],
                    meta_records=stats['meta_records'],logo=self.logo,favicon=self.favicon,
                    platform=self.platform
                )
                await asyncio.sleep(max(.05,float(delay)))

            await browser.close()
            self.browser=None; self.page=None

        self._rebuild_media()
        self.write_data(root)
        try:
            checkpoint_path.write_text(json.dumps({'source_url':root,'seen':sorted(seen),'discovered':sorted(discovered),'queue':queue if 'queue' in locals() else [],'updated_at':time.time(),'complete':not self.stop_requested},ensure_ascii=False),encoding='utf-8')
        except Exception:
            pass
        return self.summary(root,len(seen),len(discovered))

    def summary(self,root,scanned,discovered):
        self._rebuild_media()
        kinds=[x.kind for x in self.records]
        meta_records=0
        if self.options.get('seo',True):
            meta_records=sum(bool(r.meta_title or r.meta_description) for r in self.records)
            meta_records+=sum(bool(p.get('meta_title') or p.get('meta_description')) for p in self.products)
        return {'url':root,'domain':urlparse(root).netloc,'platform':self.platform,
                'pages':sum(x=='page' for x in kinds) if self.options.get('pages',True) else 0,
                'blogs':sum(x=='blog' for x in kinds) if self.options.get('blogs',True) else 0,
                'products':len(self.products) if self.options.get('products',True) else 0,
                'categories':len(self.categories) if self.options.get('categories',True) else 0,
                'variants':sum(len(p.get('variants') or []) for p in self.products) if self.options.get('products',True) else 0,
                'images':len(self.media),'menus':len(self.menus) if self.options.get('menus',True) else 0,
                'meta_records':meta_records,'logo':self.logo if self.options.get('branding',True) else '',
                'favicon':self.favicon if self.options.get('branding',True) else '',
                'scanned':scanned,'discovered':discovered}

    def write_data(self,root):
        self._rebuild_media()
        d={'source_url':root,'platform':self.platform,'capture_options':dict(self.options),
           'branding':{'logo':self.logo,'favicon':self.favicon} if self.options.get('branding',True) else {'logo':'','favicon':''},
           'menus':self.menus if self.options.get('menus',True) else [],
           'categories':list(self.categories.values()) if self.options.get('categories',True) else [],
           'blog_categories':list(self.blog_categories.values()) if self.options.get('blogs',True) else [],
           'tags':list(self.tags.values()) if self.options.get('blogs',True) else [],
           'pages':[asdict(r) for r in self.records if (r.kind=='blog' and self.options.get('blogs',True)) or (r.kind!='blog' and self.options.get('pages',True))],
           'products':self.products if self.options.get('products',True) else [],
           'media':sorted(x for x in self.media if x),
           'design':self.design if self.options.get('design',True) else {},'generated_at':time.time()}
        (self.output/'site-data.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.output/'migration-report.json').write_text(json.dumps(self.summary(root,0,0),indent=2),encoding='utf-8')

    def build_wordpress_package(self, data_path=None, package_name="codexifyr-wordpress-migration.zip"):

        """
        Build ONE migration ZIP for the standalone Codexifyr WordPress importer.
        The WordPress plugin is installed once; this ZIP is uploaded inside that plugin.
        """
        data=Path(data_path) if data_path else self.output/'site-data.json'
        if not data.exists():
            raise RuntimeError('Run a website scan first.')

        build=self.output/'_build'
        if build.exists():
            shutil.rmtree(build)
        theme=build/'codexifyr-source-theme'
        theme.mkdir(parents=True)

        style=r"""/*
Theme Name: Codexifyr Source Store Theme
Version: 2.0.0
*/
:root{--ink:#111;--line:#e8e8e8;--max:1460px}*{box-sizing:border-box}body{margin:0;color:var(--ink);background:#fff;font-family:Arial,Helvetica,sans-serif}a{color:inherit;text-decoration:none}img{max-width:100%;height:auto}.cx-wrap{width:min(var(--max),calc(100% - 48px));margin:auto}.cx-top{text-align:center;background:#111;color:#fff;padding:9px;font-size:11px;letter-spacing:.06em}.cx-head{min-height:88px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center}.custom-logo{max-height:58px;width:auto}.cx-brand{font-size:25px;font-weight:900;letter-spacing:.08em}.cx-tools{text-align:right;font-size:12px}.site-header{border-bottom:1px solid var(--line)}.primary-nav{border-top:1px solid var(--line)}.primary-nav ul{list-style:none;padding:0;margin:0;display:flex;justify-content:center;gap:30px}.primary-nav>div>ul>li{position:relative;padding:16px 0;text-transform:uppercase;font-size:12px;font-weight:700}.primary-nav li ul{display:none;position:absolute;top:100%;left:0;background:#fff;min-width:220px;padding:12px;box-shadow:0 12px 32px rgba(0,0,0,.12);z-index:30;flex-direction:column;gap:0}.primary-nav li:hover>ul{display:flex}.primary-nav li ul li{padding:8px}.cx-page{padding:38px 0}.entry-title{text-align:center;text-transform:uppercase;font-size:31px;letter-spacing:.04em}.woocommerce .products{display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr));gap:28px 20px}.woocommerce ul.products:before,.woocommerce ul.products:after{display:none!important}.woocommerce ul.products li.product{width:auto!important;margin:0!important;float:none!important;text-align:center}.woocommerce ul.products li.product img{aspect-ratio:1/1.22;object-fit:cover;background:#f7f7f7}.woocommerce ul.products li.product .woocommerce-loop-product__title{font-size:14px!important}.woocommerce ul.products li.product .price{color:#111!important;font-size:14px!important}.woocommerce span.onsale{background:#111!important;border-radius:0!important;min-height:auto!important;line-height:1!important;padding:8px!important;font-size:10px!important}.woocommerce div.product{padding-top:42px}.woocommerce div.product .product_title{font-size:30px}.woocommerce div.product form.cart .variations select{min-height:44px;border:1px solid #ccc;padding:0 12px;background:#fff}.woocommerce .button,.woocommerce button.button,.woocommerce a.button{border-radius:0!important;background:#111!important;color:#fff!important;text-transform:uppercase;font-size:11px!important;letter-spacing:.07em;padding:14px 18px!important}.cx-footer{background:#111;color:#fff;margin-top:70px;padding:55px 0 25px}.cx-foot{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:40px}.cx-footer ul{list-style:none;padding:0;line-height:2}.cx-copy{border-top:1px solid #333;padding-top:20px;margin-top:30px;color:#aaa;font-size:11px}@media(max-width:950px){.woocommerce .products{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.cx-wrap{width:calc(100% - 28px)}.cx-head{grid-template-columns:1fr auto}.cx-head>div:first-child{display:none}.primary-nav{overflow:auto}.primary-nav ul{justify-content:flex-start;white-space:nowrap;padding:0 14px}.woocommerce .products{grid-template-columns:repeat(2,1fr);gap:18px 10px}.cx-foot{grid-template-columns:1fr}}"""
        (theme/'style.css').write_text(style,encoding='utf-8')
        (theme/'functions.php').write_text(r"""<?php
add_action('after_setup_theme',function(){add_theme_support('title-tag');add_theme_support('post-thumbnails');add_theme_support('custom-logo');add_theme_support('woocommerce');add_theme_support('wc-product-gallery-zoom');add_theme_support('wc-product-gallery-lightbox');add_theme_support('wc-product-gallery-slider');register_nav_menus(['primary'=>'Primary Menu','footer'=>'Footer Menu']);});
add_action('wp_enqueue_scripts',function(){wp_enqueue_style('cx',get_stylesheet_uri(),[],wp_get_theme()->get('Version'));});
add_filter('loop_shop_columns',fn()=>4,20);
""",encoding='utf-8')
        (theme/'header.php').write_text(r"""<!doctype html><html <?php language_attributes();?>><head><meta charset="<?php bloginfo('charset');?>"><meta name="viewport" content="width=device-width,initial-scale=1"><?php wp_head();?></head><body <?php body_class();?>><?php wp_body_open();?><div class="cx-top">WORLDWIDE SHIPPING · SECURE PAYMENT · PREMIUM QUALITY</div><header class="site-header"><div class="cx-wrap cx-head"><div>MENU</div><div class="cx-brand"><?php if(has_custom_logo())the_custom_logo();else bloginfo('name');?></div><div class="cx-tools"><?php if(class_exists('WooCommerce')):?><a href="<?php echo esc_url(wc_get_cart_url());?>">CART</a><?php endif;?></div></div><nav class="primary-nav"><div class="cx-wrap"><?php wp_nav_menu(['theme_location'=>'primary','container'=>false,'fallback_cb'=>false]);?></div></nav></header><main>""",encoding='utf-8')
        (theme/'footer.php').write_text(r"""</main><footer class="cx-footer"><div class="cx-wrap"><div class="cx-foot"><div><h3><?php bloginfo('name');?></h3><p><?php bloginfo('description');?></p></div><div><h3>Shop</h3><?php wp_nav_menu(['theme_location'=>'primary','container'=>false,'fallback_cb'=>false]);?></div><div><h3>Information</h3><?php wp_nav_menu(['theme_location'=>'footer','container'=>false,'fallback_cb'=>false]);?></div></div><div class="cx-copy">© <?php echo date('Y');?> <?php bloginfo('name');?></div></div></footer><?php wp_footer();?></body></html>""",encoding='utf-8')
        idx=r"""<?php get_header();?><div class="cx-wrap cx-page"><?php if(have_posts()):while(have_posts()):the_post();?><article <?php post_class();?>><h1 class="entry-title"><?php the_title();?></h1><div class="entry-content"><?php the_content();?></div></article><?php endwhile;endif;?></div><?php get_footer();?>"""
        for n in ('index.php','page.php','single.php','front-page.php'):
            (theme/n).write_text(idx,encoding='utf-8')
        (theme/'woocommerce.php').write_text(r"""<?php get_header();?><div class="cx-wrap cx-page"><?php woocommerce_content();?></div><?php get_footer();?>""",encoding='utf-8')

        def zipdir(folder,target):
            with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
                for f in folder.rglob('*'):
                    if f.is_file():
                        z.write(f,f.relative_to(folder.parent))

        tz=self.output/'codexifyr-source-theme.zip'
        zipdir(theme,tz)

        instructions=self.output/'IMPORT-INSTRUCTIONS.txt'
        instructions.write_text("""CODEXIFYR ONE-ZIP WORDPRESS MIGRATION

FIRST TIME ONLY:
1. Install and activate WooCommerce.
2. Install and activate codexifyr-migrator-importer.zip in WordPress.

FOR EACH WEBSITE:
1. Finish a Website Scraper job, or finish File Correction if the scan needed repair.
2. Validate the active scan/corrected data.
3. Build the FULL WORDPRESS MIGRATION from Migration / Export or the completed Repair page.
4. Download codexifyr-wordpress-migration.zip.
5. WordPress Admin > Codexifyr.
6. Upload codexifyr-wordpress-migration.zip.
7. Click INSTALL WEBSITE / RESUME IMPORT.
8. Keep the WordPress admin tab open while the batched importer runs.

The migration ZIP always exposes the active master data as site-data.json, even when it was built from corrected-site-data.json.
It also contains the generated source theme and migration/repair report.
You DO NOT upload the migration ZIP under Plugins. Upload it inside the installed Codexifyr importer.
The generated theme is already inside the full migration ZIP; it does not need to be uploaded separately.
""",encoding='utf-8')

        pkg=self.output/package_name
        with zipfile.ZipFile(pkg,'w',zipfile.ZIP_DEFLATED) as z:
            # The importer contract always expects site-data.json even when the
            # source is corrected-site-data.json from File Correction.
            z.write(data,'site-data.json')
            report=self.output/'migration-report.json'
            if not report.exists() and (self.output/'repair-report.json').exists():
                report=self.output/'repair-report.json'
            if report.exists():
                z.write(report,'migration-report.json')
            else:
                try:
                    d=json.loads(data.read_text(encoding='utf-8'))
                    summary={'source_url':d.get('source_url',''),'platform':d.get('platform','Unknown'),'products':len(d.get('products') or []),'variants':sum(len(p.get('variants') or []) for p in (d.get('products') or [])),'pages':len(d.get('pages') or []),'categories':len(d.get('categories') or []),'media':len(d.get('media') or []),'menus':len(d.get('menus') or []),'generated_at':time.time()}
                    z.writestr('migration-report.json',json.dumps(summary,ensure_ascii=False,indent=2))
                except Exception:
                    pass
            if tz.exists():z.write(tz,tz.name)
            if instructions.exists():z.write(instructions,instructions.name)
        return str(pkg)

