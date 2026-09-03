#!/usr/bin/env python3
import argparse, asyncio, csv, hashlib, json, os, re, sqlite3, sys, time
from dataclasses import dataclass, asdict, field
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode, quote
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

SHOPIFY_COLUMNS = [
'Handle','Title','Body (HTML)','Vendor','Product Category','Type','Tags','Published',
'Option1 Name','Option1 Value','Option1 Linked To','Option2 Name','Option2 Value','Option2 Linked To','Option3 Name','Option3 Value','Option3 Linked To',
'Variant SKU','Variant Grams','Variant Inventory Tracker','Variant Inventory Qty',
'Variant Inventory Policy','Variant Fulfillment Service','Variant Price','Variant Compare At Price',
'Variant Requires Shipping','Variant Taxable','Unit Price Total Measure','Unit Price Total Measure Unit','Unit Price Base Measure','Unit Price Base Measure Unit','Variant Barcode','Image Src','Image Position',
'Image Alt Text','Gift Card','SEO Title','SEO Description',
'Activewear clothing features (product.metafields.shopify.activewear-clothing-features)','Activity (product.metafields.shopify.activity)','Clothing features (product.metafields.shopify.clothing-features)','Color (product.metafields.shopify.color-pattern)','Fabric (product.metafields.shopify.fabric)','Fit (product.metafields.shopify.fit)','Headwear features (product.metafields.shopify.headwear-features)','Neckline (product.metafields.shopify.neckline)','Pants length type (product.metafields.shopify.pants-length-type)','Size (product.metafields.shopify.size)','Sleeve length type (product.metafields.shopify.sleeve-length-type)','Target gender (product.metafields.shopify.target-gender)','Top length type (product.metafields.shopify.top-length-type)','Variant Image','Variant Weight Unit','Variant Tax Code','Cost per item','Status']

@dataclass
class Variant:
    title:str=''; sku:str=''; price:str=''; compare_at_price:str=''; barcode:str=''; image:str=''
    available:str=''; weight:str=''; weight_unit:str=''; inventory_qty:str=''; options:list=field(default_factory=list); option_names:list=field(default_factory=list)
@dataclass
class Product:
    url:str; handle:str=''; title:str=''; body_html:str=''; vendor:str=''; product_type:str=''
    tags:list=field(default_factory=list); category:str=''; categories:list=field(default_factory=list); images:list=field(default_factory=list)
    variants:list=field(default_factory=list); seo_title:str=''; seo_description:str=''

def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip()
def money(x): return re.sub(r'[^0-9.]','',clean(x))
def clean_option_value(x):
    value=clean(x)
    value=re.sub(r'[-_]\d+$','',value).strip()
    return value
def is_placeholder_option_value(x):
    value=clean(x).lower().strip()
    if not value: return True
    value=re.sub(r'\s+',' ',value)
    value=value.rstrip(' .…:;-')
    exact={
        'select an option','select option','select a option','select size','select a size','select your size',
        'choose an option','choose option','choose a option','choose size','choose a size','choose your size',
        'please select','please select an option','please choose','please choose an option',
        'sélectionner une option','selectionner une option','sélectionnez une option','selectionnez une option',
        'sélectionner la taille','selectionner la taille','sélectionnez la taille','selectionnez la taille',
        'choisir une option','choisissez une option','choisir la taille','choisissez la taille',
        "seleziona un'opzione","selezionare un'opzione",'seleziona una taglia','selezionare una taglia',
        'seleccione una opción','selecciona una opción','seleccione una talla','selecciona una talla',
        'wählen sie eine option','option auswählen','größe auswählen','groesse auswählen',
        'selecteer een optie','kies een optie','selecteer maat','kies maat',
        'selecione uma opção','selecionar uma opção','selecione um tamanho','selecionar tamanho',
        '選択してください','请选择','請選擇'
    }
    if value in exact: return True
    # Generic placeholder patterns, but only when they clearly begin with an instruction.
    return bool(re.match(r'^(?:please\s+)?(?:select|choose|pick)\b',value))
def product_title(x):
    value=clean(x)
    value=re.sub(r'\s*(?:[-|:]\s*)?(?:shop\s+now|buy\s+now|official\s+website)\s*$', '', value, flags=re.I)
    return value.strip(' -|:')
def canon(u):
    # NOTE: query string dropped entirely so variant/query params (?variant=123 etc.)
    # don't cause the same product to be counted as multiple distinct URLs.
    p=urlparse(u); return urlunparse((p.scheme.lower(),p.netloc.lower(),p.path or '/', '','',''))
def absu(base,u): return urljoin(base,u) if u else ''
def importer_image_url(u):
    if not u: return ''
    return 'https://images.weserv.nl/?url='+quote(u, safe='')+'&bg=ffffff&output=jpg'
def same_domain(a,b): return urlparse(a).netloc.lower()==urlparse(b).netloc.lower()
def uniq(xs):
    seen=set(); out=[]
    for x in xs:
        if x and x not in seen: seen.add(x); out.append(x)
    return out
UTILITY_PATHS=('account','my-account','login','logout','register','cart','checkout','wishlist','compare','contact','about','privacy','terms','faq','faqs','blog','author','search','sitemap','feed','wp-json','shop-page','order-tracking','shipping-policy','exchange-policy','refund-return-policy')
UTILITY_LABELS={'blog','about','about us','contact','contact us','faq','faqs','login','register','sign in','sign up','my account','account','cart','checkout','wishlist','compare','privacy policy','terms','terms and conditions','shipping policy','exchange policy','refund policy','refund and return policy','order tracking','sitemap','search','home'}
def utility_label(n):
    t=re.sub(r"[^a-z0-9\s]",'',clean(n).lower()).strip()
    t=re.sub(r'\s+',' ',t)
    return t in UTILITY_LABELS
def excluded_collection(name,u):
    return clean(name).lower() == 'shop' or 'shop' in {x for x in urlparse(u).path.lower().split('/') if x}
def utility_url(u):
    segments={x for x in urlparse(u).path.lower().split('/') if x}
    return any(segment == utility or segment.startswith(utility+'-') for segment in segments for utility in UTILITY_PATHS)
def handle(url,title=''):
    s=(title or urlparse(url).path).lower(); s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
    return s[:255] or hashlib.sha1(url.encode()).hexdigest()[:16]

def next_query(url):
    p=urlparse(url); q=dict(parse_qsl(p.query))
    for key in ('page','p'):
        if key in q and q[key].isdigit():
            q[key]=str(int(q[key])+1); return urlunparse(p._replace(query=urlencode(q)))
    return None

def ldjson(soup):
    out=[]
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            o=json.loads(s.string or s.get_text()); out += o if isinstance(o,list) else [o]
        except: pass
    return out

def from_ld(obj,url,category):
    if not isinstance(obj,dict): return None
    typ=obj.get('@type','')
    if typ!='Product' and not (isinstance(typ,list) and 'Product' in typ): return None
    brand=obj.get('brand'); brand=brand.get('name','') if isinstance(brand,dict) else brand
    imgs=obj.get('image',[]); imgs=[imgs] if isinstance(imgs,str) else imgs
    p=Product(url,handle(url,obj.get('name','')),clean(obj.get('name')),obj.get('description','') or '',clean(brand),category=category)
    p.images=uniq([absu(url,x) for x in imgs if isinstance(x,str)])
    offers=obj.get('offers',[]); offers=[offers] if isinstance(offers,dict) else offers
    for o in offers:
        if isinstance(o,dict): p.variants.append(Variant(sku=clean(o.get('sku')),price=str(o.get('price','')),barcode=clean(o.get('gtin13') or o.get('gtin')),image=absu(url,o.get('image','')),available=clean(o.get('availability'))))
    return p

def _has_ld_product(soup):
    for o in ldjson(soup):
        if not isinstance(o,dict): continue
        typ=o.get('@type','')
        if typ=='Product' or (isinstance(typ,list) and 'Product' in typ): return True
        for g in o.get('@graph',[]) or []:
            if isinstance(g,dict):
                gtyp=g.get('@type','')
                if gtyp=='Product' or (isinstance(gtyp,list) and 'Product' in gtyp): return True
    return False

def looks_like_product_page(soup):
    # Some blog/article pages (especially WordPress sites with plain, non-'/blog/'-prefixed
    # permalinks) slip past the URL-based filters entirely and end up scraped as if they were
    # products - complete with a fabricated "price" scraped from some unrelated dollar amount
    # mentioned in the article text. This checks the page's actual content instead of guessing
    # from its URL: a real product page almost always has explicit Product markup, an
    # add-to-cart/variant form, or a WooCommerce/Shopify "product" body class. An article page
    # has none of those.
    if _has_ld_product(soup): return True
    body=soup.find('body')
    body_classes=' '.join(body.get('class',[])).lower() if body and body.get('class') else ''
    if any(marker in body_classes for marker in ('single-product','product-template','type-product','template-product')):
        return True
    if soup.select_one('form.cart, form.variations_form, .single_add_to_cart_button, [name="add-to-cart"], '
                        '.product-form, .product-single__form, [data-product-variations], product-form'):
        return True
    return False

def meta(soup,name):
    x=soup.find('meta',attrs={'name':name}); return clean(x.get('content')) if x else ''

def page_price(soup):
    for sel in ("[itemprop='price']",'.price ins','.product-price ins','.product__price ins','[class*="price"] ins','.price','.product-price','.product__price','[class*="price"]'):
        n=soup.select_one(sel)
        if n:
            value=clean(n.get('content') or n.get_text(' ',strip=True))
            if value: return value
    return ''

def page_prices(soup):
    regular=''; sale=''
    sale_node=soup.select_one('.price ins, .product-price ins, .product__price ins, [class*="price"] ins')
    regular_node=soup.select_one('.price del, .product-price del, .product__price del, [class*="price"] del')
    if sale_node: sale=clean(sale_node.get('content') or sale_node.get_text(' ',strip=True))
    if regular_node: regular=clean(regular_node.get('content') or regular_node.get_text(' ',strip=True))
    return sale or page_price(soup), regular

def extract_variants(soup,url):
    text='\n'.join((s.string or s.get_text()) for s in soup.find_all('script'))
    variants=[]
    for marker in ('"variants":','"variants" :'):
        pos=text.find(marker)
        if pos<0: continue
        start=text.find('[',pos)
        if start<0: continue
        depth=0; quote=False; esc=False; end=-1
        for i in range(start,min(len(text),start+250000)):
            c=text[i]
            if quote:
                if esc: esc=False
                elif c=='\\': esc=True
                elif c=='"': quote=False
            elif c=='"': quote=True
            elif c=='[': depth+=1
            elif c==']':
                depth-=1
                if depth==0: end=i+1; break
        if end<0: continue
        try: arr=json.loads(text[start:end])
        except: continue
        if not isinstance(arr,list): continue

        # Shopify product JSON commonly stores the option values on each variant
        # as an `options` array (for example ["XS","BLACK"]) rather than
        # option1/option2/option3 keys. The old extractor ignored that array,
        # which made every real Shopify variant look option-less. The exporter
        # then correctly collapsed those option-less duplicates to one
        # "Default Title", causing Size/Color variants to disappear.
        #
        # Recover the product-level option names immediately before the variants
        # array when available (normally ["SIZE","COLOR"] on Shopify themes).
        product_option_names=[]
        prefix=text[max(0,pos-8000):pos]
        option_name_matches=list(re.finditer(r'"options"\s*:\s*(\[[^\]]*\])',prefix,re.S))
        if option_name_matches:
            for match in reversed(option_name_matches):
                try:
                    candidate=json.loads(match.group(1))
                except:
                    continue
                if isinstance(candidate,list) and candidate and all(isinstance(x,str) for x in candidate):
                    product_option_names=[clean(x).title() for x in candidate[:3]]
                    break

        for v in arr:
            if not isinstance(v,dict): continue

            # Variant-specific image support:
            # Shopify themes can swap the gallery image only after a Color variant
            # is selected. That image is commonly stored on the variant as
            # featured_image, featured_media.preview_image, or image. Read all
            # supported forms so each Shopify CSV variant receives its own image.
            image=''
            fi=v.get('featured_image')
            if isinstance(fi,dict):
                image=fi.get('src') or fi.get('url') or ''
            elif isinstance(fi,str):
                image=fi

            if not image:
                fm=v.get('featured_media')
                if isinstance(fm,dict):
                    preview=fm.get('preview_image')
                    if isinstance(preview,dict):
                        image=preview.get('src') or preview.get('url') or ''
                    if not image:
                        image=fm.get('src') or fm.get('url') or ''

            if not image:
                vi=v.get('image')
                if isinstance(vi,dict):
                    image=vi.get('src') or vi.get('url') or ''
                elif isinstance(vi,str):
                    image=vi

            raw_options=v.get('options')
            if isinstance(raw_options,list):
                options=[clean_option_value(x) for x in raw_options[:3]]
            else:
                options=[clean_option_value(v.get('option1','')),
                         clean_option_value(v.get('option2','')),
                         clean_option_value(v.get('option3',''))]

            options=[x if not is_placeholder_option_value(x) else '' for x in options]
            while len(options)<3: options.append('')

            option_names=list(product_option_names)
            while len(option_names)<3: option_names.append('Option'+str(len(option_names)+1))

            # Keep unavailable / sold-out variants instead of dropping them.
            # When the source explicitly says a variant is unavailable and gives
            # no inventory quantity, store quantity 0 so the exported record still
            # represents that Size/Color combination.
            available=v.get('available','')
            inventory_qty=v.get('inventory_quantity','')
            if inventory_qty in (None,'') and available is False:
                inventory_qty='0'

            variants.append(Variant(
                title=clean(v.get('title')),
                sku=clean(v.get('sku')),
                price=str(v.get('price','')),
                compare_at_price=str(v.get('compare_at_price','')),
                barcode=clean(v.get('barcode')),
                image=absu(url,image),
                available=str(available),
                weight=str(v.get('weight','')),
                inventory_qty=str(inventory_qty),
                options=options[:3],
                option_names=option_names[:3]
            ))
        if variants: return variants
    variation_form=soup.select_one('form.variations_form[data-product_variations]')
    if variation_form:
        try: variation_data=json.loads(variation_form.get('data-product_variations') or '[]')
        except (TypeError,json.JSONDecodeError): variation_data=[]
        names=[]; attribute_keys=[]
        for select in variation_form.select('select[name^="attribute_"]'):
            raw=clean(select.get('data-attribute_name') or select.get('name','').removeprefix('attribute_')).lower()
            raw=raw.removeprefix('pa_').removeprefix('pa-')
            names.append(raw.replace('_',' ').replace('-',' ').title()); attribute_keys.append(raw)
        for v in variation_data:
            if not isinstance(v,dict): continue
            attrs=v.get('attributes') or {}; options=[]
            for raw in attribute_keys[:3]:
                options.append(attrs.get('attribute_'+raw) or attrs.get('attribute_pa_'+raw) or '')
            image_data=v.get('image') or {}; image=image_data.get('url') or image_data.get('src','') if isinstance(image_data,dict) else ''
            variants.append(Variant(title=clean(v.get('variation_description') or v.get('formatted_name')),sku=clean(v.get('sku')),price=str(v.get('display_price') or v.get('price','')),compare_at_price=str(v.get('display_regular_price') or v.get('regular_price','')),image=absu(url,image),available=str(v.get('is_in_stock',v.get('is_purchasable',''))),weight=str(v.get('weight','')),options=options,option_names=names[:3]))
        if variants: return variants
    selects=soup.select('select[name*="option"], select[id*="option"], select[name*="size"], select[id*="size"], select[name*="color"], select[id*="color"]')
    if selects:
        names=[]; values=[]
        for select in selects[:3]:
            raw=clean(select.get('data-option-name') or select.get('name') or select.get('id'))
            raw=re.sub(r'^(product\[|attribute_|option[-_]?)','',raw,flags=re.I).strip('[]')
            name=raw.replace('_',' ').replace('-',' ').title() or 'Option'+str(len(names)+1)
            label=clean(select.get('aria-label') or select.get('data-label') or select.find_previous(['label','legend']).get_text(' ',strip=True) if select.find_previous(['label','legend']) else '')
            if 'size' in (name+' '+label).lower(): name='Size'
            options=[clean_option_value(o.get_text(' ',strip=True) or o.get('value')) for o in select.select('option')]
            options=[x for x in options if x and not is_placeholder_option_value(x)]
            options=uniq(options)
            if options: names.append(name); values.append(options)
        if names and values:
            from itertools import product as cartesian_product
            for combination in cartesian_product(*values):
                variants.append(Variant(options=list(combination),option_names=names[:3]))
            if variants: return variants
    attribute_groups=soup.select('[class*="variation"] [class*="attribute"], [class*="swatch"], [data-attribute_name]')
    names=[]; values_list=[]
    for group in attribute_groups:
        name=clean(group.get('data-attribute_name') or group.get('aria-label') or group.get('data-attribute') or group.get('class'))
        values=[]
        for option in group.select('[data-value], [data-option], [value]'):
            value=clean(option.get('data-value') or option.get('data-option') or option.get('value') or option.get_text(' ',strip=True))
            if value and not is_placeholder_option_value(value): values.append(clean_option_value(value))
        values=uniq(values)
        if values:
            if 'size' in name.lower(): name='Size'
            names.append(name.replace('_',' ').replace('-',' ').title() or 'Option'+str(len(names)+1)); values_list.append(values)
    if names and values_list:
        from itertools import product as cartesian_product
        return [Variant(options=list(combination),option_names=names[:3]) for combination in cartesian_product(*values_list)]
    return variants

WP_SIZE_RE=re.compile(r'-(\d+)x(\d+)(?=\.[a-zA-Z0-9]+(?:\?.*)?$)')
def wp_size_key(u): return WP_SIZE_RE.sub('',u)
def wp_size_dims(u):
    m=WP_SIZE_RE.search(u)
    return (int(m.group(1)),int(m.group(2))) if m else (10**9,10**9)

RELATED_SECTION_MARKERS=('related','recommend','upsell','cross-sell','crosssell','also-bought',
    'also-like','you-may-like','youmaylike','recently-viewed','recentlyviewed','similar-product',
    'complete-the-look','frequently-bought','trending-now','best-seller','bestseller','you-might')

def _in_related_section(img):
    # Walk a few ancestors up looking for a "related/recommended/upsell products" wrapper.
    # These widgets commonly live inside <main> right alongside the real product gallery, so
    # a naive "grab every image in main" fallback pulls in OTHER products' images too.
    node=img.parent
    for _ in range(6):
        if node is None: break
        classes=node.get('class'); classes=' '.join(classes) if isinstance(classes,list) else (classes or '')
        ident=' '.join([classes, node.get('id') or '', node.get('data-section') or '', node.get('aria-label') or '']).lower()
        if any(marker in ident for marker in RELATED_SECTION_MARKERS): return True
        node=node.parent
    return False

def product_gallery_images(soup,url):
    selectors=(
        '.woocommerce-product-gallery img', '.woocommerce-product-gallery__image img',
        '.product-gallery img', '.product__media img', '.product-media img',
        '[data-product-gallery] img', '[data-product-images] img',
        '.flex-control-thumbs img', '.product-images img')
    nodes=[]
    for selector in selectors:
        nodes.extend(soup.select(selector))
    if not nodes:
        # Narrower than "every image in main": only elements that actually look like a
        # gallery/media/carousel/zoom container. Still filtered below for related-products
        # widgets, since some themes reuse a generic "media" class for those too.
        nodes=soup.select(
            'main [class*="gallery"] img, main [class*="carousel"] img, main [class*="slider"] img, '
            'main [class*="swiper"] img, main [class*="zoom"] img, main [class*="media"] img, '
            '[role="main"] [class*="gallery"] img, [role="main"] [class*="media"] img')
    nodes=[img for img in nodes if not _in_related_section(img)]
    best={}; order=[]
    for img in nodes:
        src=img.get('data-large_image') or img.get('data-src') or img.get('data-original') or img.get('src')
        u=absu(url,src)
        if not u or u.startswith('data:'): continue
        key=wp_size_key(u)
        if key not in best:
            best[key]=u; order.append(key)
        elif wp_size_dims(u)[0]*wp_size_dims(u)[1] > wp_size_dims(best[key])[0]*wp_size_dims(best[key])[1]:
            best[key]=u
    return [best[k] for k in order]

def _humanize_slug(slug):
    slug = re.sub(r'\.(html?|php)$', '', slug, flags=re.I)
    slug = re.split(r'[?#]', slug)[0]
    if not slug or slug.isdigit():
        return ''
    name = re.sub(r'[-_]+', ' ', slug).strip()
    if not name:
        return ''
    return ' '.join(w.capitalize() for w in name.split())

def url_collection_name(url):
    """Derive a collection name straight from its URL, e.g.
    /collections/ravnar-hoodie -> 'Ravnar Hoodie', or a flat store path like
    ravnar.com/hoodies -> 'Hoodies'.

    This is the most reliable identifier for a collection: page <h1>s and nav
    labels are frequently generic or reused across multiple distinct
    collections on the same theme (e.g. several hoodie collections all
    rendering an h1 of just "Hoodie"). Relying on those instead of the URL
    causes distinct collections to collapse into the same stored name, which
    fragments/duplicates a single logical collection across output rows.
    """
    path = urlparse(url).path
    segments = [s for s in path.split('/') if s]
    if not segments:
        return ''
    keywords = ('collections', 'collection', 'category', 'categories', 'product-category')
    for i, seg in enumerate(segments):
        if seg.lower() in keywords and i + 1 < len(segments):
            # Use the deepest remaining segment - handles nested taxonomies like
            # /product-category/mens/jackets/ resolving to the specific leaf "Jackets"
            # rather than the first level "Mens".
            return _humanize_slug(segments[-1])
    # No recognized collection keyword in the path - likely a flat store structure
    # (e.g. ravnar.com/hoodies). This function is only ever called on URLs already
    # vetted as collection/listing pages (discovered via nav, filtered past
    # utility_url), so it's safe to just take the last path segment as the slug.
    return _humanize_slug(segments[-1])

def collection_page_name(soup, url, fallback):
    # Priority: URL slug (stable, unique per collection) > on-page heading/title
    # (can be generic/reused) > nav menu label (least reliable, often just a
    # marketing label like "SALE" or "New In").
    slug_name = url_collection_name(url)
    if slug_name:
        return slug_name
    for sel in ('h1.collection-title','h1[itemprop="name"]','.collection-hero__title','.collection-title','main h1','h1'):
        n = soup.select_one(sel)
        if n:
            t = clean(n.get_text(' ', strip=True))
            if t: return t
    og = soup.find('meta', attrs={'property': 'og:title'})
    if og and clean(og.get('content')): return clean(og.get('content'))
    t = clean(soup.title.get_text() if soup.title else '')
    if t: return re.sub(r'\s*[-|]\s*.*$', '', t).strip()  # trim trailing "| Store Name"
    return fallback

def collection_seo(soup):
    og_title = soup.find('meta', attrs={'property': 'og:title'})
    seo_title = clean(og_title.get('content')) if og_title and clean(og_title.get('content')) else clean(soup.title.get_text() if soup.title else '')
    seo_description = meta(soup, 'description')
    if not seo_description:
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        seo_description = clean(og_desc.get('content')) if og_desc else ''
    return seo_title, seo_description

def extract_product(html,url,category):
    soup=BeautifulSoup(html,'lxml'); p=None
    for o in ldjson(soup):
        if isinstance(o,dict) and '@graph' in o:
            for g in o['@graph']:
                p=from_ld(g,url,category)
                if p: break
        else: p=from_ld(o,url,category)
        if p: break
    h1_text=''
    for h in soup.find_all('h1'):
        t=clean(h.get_text(' ',strip=True))
        if t: h1_text=t; break
    name_node=soup.select_one('[itemprop="name"]')
    page_name=clean(h1_text or (name_node.get('content') or name_node.get_text(' ',strip=True) if name_node else ''))
    seo_title=clean(soup.title.get_text() if soup.title else '')
    title=product_title(page_name or (p.title if p else '') or seo_title)
    if not p: p=Product(url,handle(url,title),title,category=category)
    else:
        p.title=title
        p.handle=handle(url,title)
    p.categories=uniq([category]+p.categories) if category else p.categories
    p.seo_title=seo_title; p.seo_description=meta(soup,'description')
    if not p.body_html:
        for sel in ("[itemprop='description']",'.product-description','.product__description','#product-description','#description','.description'):
            n=soup.select_one(sel)
            if n: p.body_html=str(n); break
    p.images.extend(product_gallery_images(soup,url))
    p.images=uniq(p.images)
    ev=extract_variants(soup,url)
    if ev: p.variants=ev
    sale_price, regular_price=page_prices(soup)
    if p.variants:
        for variant in p.variants:
            if not variant.price or 'original price' in variant.price.lower(): variant.price=sale_price
            if not variant.compare_at_price: variant.compare_at_price=regular_price
    if not p.variants:
        p.variants=[Variant(price=sale_price,compare_at_price=regular_price,image=p.images[0] if p.images else '',option_names=['Option1','Option2','Option3'])]
    kw=meta(soup,'keywords'); p.tags=[clean(x) for x in kw.split(',') if clean(x)] if kw else []
    sku=soup.select_one('[itemprop="sku"]')
    if sku and p.variants and not p.variants[0].sku: p.variants[0].sku=clean(sku.get('content') or sku.get_text())
    return p

class DB:
    def __init__(self,out):
        os.makedirs(out,exist_ok=True); self.path=os.path.join(out,'checkpoint.sqlite'); self.c=sqlite3.connect(self.path)
        self.c.execute('CREATE TABLE IF NOT EXISTS categories(url TEXT PRIMARY KEY,name TEXT,done INTEGER DEFAULT 0)')
        self.c.execute('CREATE TABLE IF NOT EXISTS products(url TEXT PRIMARY KEY,data TEXT)')
        self.c.execute('CREATE TABLE IF NOT EXISTS collection_meta(name TEXT PRIMARY KEY,url TEXT,seo_title TEXT,seo_description TEXT)')
        self.c.commit()
    def cats(self): return self.c.execute('SELECT name,url FROM categories WHERE done=0 ORDER BY rowid').fetchall()
    def addcats(self,x):
        for n,u in x:self.c.execute('INSERT OR IGNORE INTO categories(url,name) VALUES(?,?)',(u,n))
        self.c.commit()
    def done(self,u):self.c.execute('UPDATE categories SET done=1 WHERE url=?',(u,));self.c.commit()
    def has(self,u):return self.c.execute('SELECT 1 FROM products WHERE url=?',(u,)).fetchone() is not None
    def save(self,p):self.c.execute('INSERT OR REPLACE INTO products VALUES(?,?)',(p.url,json.dumps(asdict(p),ensure_ascii=False)));self.c.commit()
    def add_category(self,url,name):
        row=self.c.execute('SELECT data FROM products WHERE url=?',(url,)).fetchone()
        if not row: return
        data=json.loads(row[0])
        cats=data.get('categories') or ([data.get('category')] if data.get('category') else [])
        if name and name not in cats:
            cats.append(name); data['categories']=cats
            self.c.execute('UPDATE products SET data=? WHERE url=?',(json.dumps(data,ensure_ascii=False),url)); self.c.commit()
    def products(self):return [json.loads(x[0]) for x in self.c.execute('SELECT data FROM products ORDER BY rowid').fetchall()]
    def delete_product(self,url):self.c.execute('DELETE FROM products WHERE url=?',(url,));self.c.commit()
    def replace_product_data(self,url,data):
        self.c.execute('UPDATE products SET data=? WHERE url=?',(json.dumps(data,ensure_ascii=False),url)); self.c.commit()
    def save_collection_meta(self,name,url,seo_title,seo_description):
        self.c.execute('INSERT OR REPLACE INTO collection_meta(name,url,seo_title,seo_description) VALUES(?,?,?,?)',(name,url,seo_title,seo_description))
        self.c.commit()
    def collection_metas(self):
        return self.c.execute('SELECT name,url,seo_title,seo_description FROM collection_meta ORDER BY rowid').fetchall()
    def close(self):self.c.close()

class Scraper:
    def __init__(self,url,args,page,db):
        self.url=canon(url); self.args=args; self.page=page; self.db=db; self.host=urlparse(self.url).netloc.lower()
        self.stats={'categories':0,'pages':0,'product_urls':0,'products':0,'variants':0,'images':0}
        self.category_urls=set()
    async def challenge(self):
        try: text=(await self.page.locator('body').inner_text(timeout=2500)).lower(); title=(await self.page.title()).lower(); markup=(await self.safe_content()).lower()
        except:return False
        text_markers=('captcha','recaptcha','hcaptcha','verify you are human','checking your browser','attention required','security check','robot challenge')
        markup_markers=('sgcaptcha','/.well-known/sgcaptcha','cf-chl-')
        return any(x in text or x in title for x in text_markers) or any(x in markup for x in markup_markers)
    async def goto(self,url):
        for _ in range(self.args.retries):
            try: await self.page.goto(url,wait_until='domcontentloaded',timeout=self.args.timeout)
            except PWTimeout: pass
            except Exception:
                if not same_domain(self.page.url,self.url): raise
            try: await self.page.wait_for_load_state('networkidle',timeout=5000)
            except: pass
            if await self.challenge():
                print('\nCAPTCHA / browser challenge detected. Solve it manually in the visible browser.')
                input('Press ENTER after it is cleared... ')
                try: await self.page.wait_for_load_state('domcontentloaded',timeout=5000)
                except: pass
                if not await self.challenge():
                    await asyncio.sleep(self.args.delay); return
                continue
            await asyncio.sleep(self.args.delay); return
        raise RuntimeError('navigation failed: '+url)
    async def safe_content(self):
        # page.content() races with in-flight navigation (client-side redirects, themes that
        # swap in content after 'networkidle' already fired, etc.) and throws "Unable to
        # retrieve content because the page is navigating and changing the content." That's
        # transient - the fix is to wait a beat for navigation to settle and try again rather
        # than letting it bubble up and abort the whole crawl.
        for attempt in range(5):
            try:
                return await self.page.content()
            except Exception as e:
                if 'navigating' not in str(e).lower():
                    raise
                try: await self.page.wait_for_load_state('domcontentloaded',timeout=5000)
                except: pass
                await asyncio.sleep(0.5*(attempt+1))
        return await self.page.content()
    async def platform(self):
        t=(await self.safe_content()).lower()
        if 'cdn.shopify.com' in t or 'shopify.theme' in t or 'myshopify' in t:return 'Shopify'
        if 'woocommerce' in t or 'wc-ajax' in t:return 'WooCommerce'
        if 'magento' in t or 'mage/cookies' in t:return 'Magento'
        if 'bigcommerce' in t or 'stencil-utils' in t:return 'BigCommerce'
        if 'prestashop' in t:return 'PrestaShop'
        return 'Generic'
    NAV_LINK_SELECTOR=('nav a,header a,[role="navigation"] a,.mega-menu a,.megamenu a,.dropdown-menu a,'
             '.sub-menu a,.submenu a,[class*="dropdown"] a,[class*="mega-menu"] a,'
             '[class*="megamenu"] a,[class*="submenu"] a,[class*="sub-menu"] a')
    def parse_nav_links(self,soup):
        found=[]
        for a in soup.select(self.NAV_LINK_SELECTOR):
            n=clean(a.get_text(' ',strip=True)); h=a.get('href'); u=canon(absu(self.url,h)) if h else ''
            if not n or not u or len(n)>100 or not same_domain(u,self.url) or u.rstrip('/')==self.url.rstrip('/') or utility_url(u) or utility_label(n) or excluded_collection(n,u):continue
            found.append((n,u))
        # Whole-page collection-shaped URL sweep, not limited to nav/header. Mega-menu panels
        # are sometimes rendered outside <nav>/<header> entirely (portals/overlays appended
        # near <body>), so this catches those even when the selector list above misses them.
        for a in soup.find_all('a',href=True):
            u=canon(absu(self.url,a['href'])); n=clean(a.get_text(' ',strip=True)); path=urlparse(u).path.lower()
            if not n or len(n)>100 or not same_domain(u,self.url) or utility_url(u) or utility_label(n) or excluded_collection(n,u): continue
            if any(x in path for x in ('/collections/','/category/','/categories/','/product-category/')):
                found.append((n,u))
        return found
    async def categories(self):
        await self.goto(self.url)
        soup=BeautifulSoup(await self.safe_content(),'lxml'); arr=self.parse_nav_links(soup)
        start_url=self.page.url
        # Hover-triggered submenus (desktop mega-menus that reveal on :hover, markup already
        # present in the DOM but hidden via CSS until then).
        try:
            triggers=self.page.locator('nav li, header li, [role="navigation"] li, .menu li, .main-menu li, .navbar li')
            count=min(await triggers.count(),60)
            for i in range(count):
                try:
                    await triggers.nth(i).hover(timeout=500); await self.page.wait_for_timeout(120)
                    arr+=self.parse_nav_links(BeautifulSoup(await self.safe_content(),'lxml'))
                except: pass
        except: pass
        # Click-to-open submenus: the "CLOTHING ⌄" chevron/accordion style, common on
        # mobile-first nav and plenty of desktop boutique themes too. Hovering alone won't
        # reveal these - the submenu only renders/becomes visible after an actual click.
        # Re-parse the page after each click since some of these are accordions where opening
        # one collapses the last, so links must be captured right after their own click.
        try:
            toggles=self.page.locator(
                '.menu-item-has-children > a, .has-submenu > a, .has-dropdown > a, '
                '[aria-haspopup="true"], [class*="dropdown-toggle"], '
                'nav li:has(ul) > a, header li:has(ul) > a, '
                'nav > ul > li > a, header nav > ul > li > a, .menu > li > a, .main-menu > li > a, '
                'nav button, header nav button, [class*="menu"] button')
            tcount=min(await toggles.count(),60)
            for i in range(tcount):
                try:
                    await toggles.nth(i).click(timeout=500,force=True); await self.page.wait_for_timeout(200)
                    if self.page.url!=start_url:
                        await self.page.go_back(wait_until='domcontentloaded',timeout=5000); await self.page.wait_for_timeout(150)
                        continue
                    arr+=self.parse_nav_links(BeautifulSoup(await self.safe_content(),'lxml'))
                except: pass
        except: pass
        if len(arr)<2:
            for a in soup.find_all('a',href=True):
                u=canon(absu(self.url,a['href'])); n=clean(a.get_text(' ',strip=True)); path=urlparse(u).path.lower()
                if n and same_domain(u,self.url) and not utility_url(u) and not excluded_collection(n,u) and any(x in path for x in ('/collections/','/category/','/categories/','/shop')):arr.append((n,u))
        out=[]; seen=set()
        for x in arr:
            if x[1] not in seen:seen.add(x[1]);out.append(x)
        self.db.addcats(out); self.stats['categories']=len(out); return out
    async def load_more(self):
        # NOTE: this used to compare self.page.locator('body').count() before/after each
        # scroll to detect "did new content load". That count is always 1 (there's exactly
        # one <body> element on any page), so it never changed and the loop always bailed
        # out after just 2 scrolls, regardless of --scrolls. That silently truncated every
        # infinite-scroll collection page (and the common "shop all" page) to whatever fit
        # in ~2 screen-heights of lazy-loaded content - those products were never even seen,
        # so they never got added to their collection at all.
        # Fix: track actual document height growth, and require two consecutive stagnant
        # readings before giving up (some themes render the next batch a beat after the
        # scroll fires, so a single unchanged reading isn't reliable proof of the bottom).
        last_height=0; stagnant=0
        for _ in range(self.args.scrolls):
            try:
                await self.page.evaluate('window.scrollTo(0,document.body ? document.body.scrollHeight : 0)')
            except: return
            await self.page.wait_for_timeout(900)
            try: height=await self.page.evaluate('document.body ? document.body.scrollHeight : 0')
            except: break
            if not height: return
            if height<=last_height:
                stagnant+=1
                if stagnant>=2: break
            else:
                stagnant=0; last_height=height
        # Same issue for "Load more" buttons: capped at 4 clicks, so any shop page needing
        # more than 4 batches to reach the end also silently truncated. Loop until a click
        # genuinely doesn't happen anymore instead of a fixed count.
        for _ in range(50):
            buttons=self.page.locator('button,a'); count=min(await buttons.count(),300); clicked=False
            for i in range(count):
                try:
                    t=clean(await buttons.nth(i).inner_text()).lower()
                    if t in ('load more','show more','view more'):
                        await buttons.nth(i).click(timeout=1200); await self.page.wait_for_timeout(1000); clicked=True; break
                except:pass
            if not clicked:break
    COLLECTION_PATH_MARKERS=('/collections/','/collection/','/category/','/categories/','/product-category/')
    async def product_links(self,url):
        await self.goto(url); await self.load_more(); soup=BeautifulSoup(await self.safe_content(),'lxml'); links=[]
        for a in soup.find_all('a',href=True):
            u=canon(absu(url,a['href'])); path=urlparse(u).path.lower()
            if not same_domain(u,self.url) or utility_url(u) or u.rstrip('/') in (url.rstrip('/'), self.url.rstrip('/')) or u in self.category_urls: continue
            if any(x in path for x in ('/products/','/product/','/item/','/p/')):
                links.append(u); continue
            if any(x in path for x in self.COLLECTION_PATH_MARKERS):
                # A collection/category-shaped URL is never a product, even if its card
                # carries a "product" class - subcategory tiles rendered inside a "shop" or
                # "products" grid are a common source of that false positive, and letting
                # them through here is what made a category page get scraped as a "product"
                # (with every thumbnail on that listing then treated as its image gallery).
                continue
            classes=' '.join(a.get('class',[])).lower()
            product_card=a.find_parent(class_=lambda value: value and 'product' in ' '.join(value if isinstance(value,list) else [value]).lower())
            if 'product' in classes or a.get('itemprop') == 'url' or product_card:
                links.append(u)
        return uniq(links),soup
    def next_page(self,current,soup):
        a=soup.select_one('a[rel="next"]')
        if not a:
            for x in soup.find_all('a',href=True):
                t=clean(x.get_text(' ',strip=True)).lower(); ar=(x.get('aria-label') or '').lower()
                if t in ('next','next page','›','→','»') or 'next page' in ar:a=x;break
        if a:return canon(absu(current,a.get('href')))
        return next_query(current)
    async def category(self,name,url):
        current=url; seen=set(); n=0
        while current and current not in seen and n<self.args.max_pages:
            seen.add(current); n+=1; self.stats['pages']+=1
            links,soup=await self.product_links(current); self.stats['product_urls']+=len(links)
            if n==1:
                name=collection_page_name(soup,current,name)   # prefer URL slug over heading/nav label
                print(f'  using collection name: {name}')
                seo_title,seo_description=collection_seo(soup)
                self.db.save_collection_meta(name,url,seo_title,seo_description)
            print(f'  page {n}: {len(links)} product URLs')
            if not links: break
            for u in links:
                if self.db.has(u):
                    self.db.add_category(u,name)
                    continue
                if self.args.max_products and self.stats['products']>=self.args.max_products:return
                try:
                    await self.goto(u); html=await self.safe_content()
                    page_soup=BeautifulSoup(html,'lxml')
                    if not looks_like_product_page(page_soup):
                        print('    skipped (not a product page):',u); continue
                    p=extract_product(html,u,name); self.db.save(p)
                    self.stats['products']+=1; self.stats['variants']+=len(p.variants); self.stats['images']+=len(p.images)
                except Exception as e:print('    failed:',u,e)
            nxt=self.next_page(current,soup)
            if not nxt or nxt in seen:break
            current=nxt
        self.db.done(url)

def export(products,out,stats,url,platform,collection_metas=None):
    os.makedirs(out,exist_ok=True); path=os.path.join(out,'shopify_products.csv')
    fieldnames=SHOPIFY_COLUMNS+['Collection']  # Collection is the one extra column Shopify allows in product CSV imports

    def build_rows(p,collection_name):
        rows=[]; emitted_images=set()
        vs=p.get('variants') or [{}]

        # Shopify rejects duplicate variant option combinations. Keep each genuine
        # Size/Color/etc. combination once. If a product truly has no options,
        # emit exactly one option-less variant (Shopify's built-in Default Title).
        optioned_vs=[]
        optionless_vs=[]
        seen_option_combinations=set()
        for v in vs:
            raw_opts=(v.get('options') or [])[:3]
            opts=tuple(clean_option_value(x) for x in raw_opts)
            opts=tuple('' if is_placeholder_option_value(x) else x for x in opts)
            if any(opts):
                if opts in seen_option_combinations:
                    continue
                seen_option_combinations.add(opts)
                vv=dict(v)
                vv['options']=list(opts)
                optioned_vs.append(vv)
            else:
                optionless_vs.append(v)

        if optioned_vs:
            vs=optioned_vs
        elif optionless_vs:
            vs=[optionless_vs[0]]
        else:
            vs=[{}]

        categories=uniq(p.get('categories') or ([p.get('category')] if p.get('category') else []))
        tags_str=', '.join(uniq(categories + (p.get('tags') or [])))
        for i,v in enumerate(vs):
            opts=(v.get('options') or [])+['','','']; names=(v.get('option_names') or [])+['Option1','Option2','Option3']
            img=importer_image_url(v.get('image') or ((p.get('images') or [''])[0]))
            if img in emitted_images: img=''
            elif img: emitted_images.add(img)
            r={c:'' for c in fieldnames}; r.update({'Handle':p.get('handle',''),'Title':p.get('title','') if i==0 else '',
            'Body (HTML)':p.get('body_html','') if i==0 else '','Vendor':p.get('vendor',''),'Product Category':'',
            'Type':p.get('product_type',''),'Tags':tags_str,'Published':'TRUE',
            'Option1 Name':names[0] if opts[0] else '','Option1 Value':opts[0],'Option2 Name':names[1] if opts[1] else '','Option2 Value':opts[1],
            'Option3 Name':names[2] if opts[2] else '','Option3 Value':opts[2],'Variant SKU':v.get('sku',''),'Variant Grams':v.get('weight',''),
            'Variant Inventory Qty':v.get('inventory_qty',''),'Variant Inventory Policy':'deny','Variant Fulfillment Service':'manual','Variant Price':money(v.get('price','')),'Variant Compare At Price':money(v.get('compare_at_price','')),
            'Variant Requires Shipping':'TRUE','Variant Taxable':'FALSE','Variant Barcode':v.get('barcode',''),'Image Src':img,
            'Image Position':str(i+1) if img else '','Image Alt Text':p.get('title','') if img else '','Variant Image':img,'Variant Weight Unit':v.get('weight_unit',''),
            'SEO Title':p.get('seo_title',''),'SEO Description':p.get('seo_description',''),'Status':'active',
            'Collection':collection_name});rows.append(r)
        for pos, image in enumerate(p.get('images', [])[1:], 2):
            image=importer_image_url(image)
            if image in emitted_images: continue
            emitted_images.add(image)
            r={c:'' for c in fieldnames}
            r.update({'Handle':p.get('handle',''),'Image Src':image,'Image Position':str(pos),'Image Alt Text':p.get('title','')})
            rows.append(r)
        return rows

    def bare_collection_row(handle,collection_name):
        # Additional-collection assignment row for a product already fully written under its
        # primary collection. Shopify groups CSV rows by Handle into one product; a full
        # variant/option/image block must appear EXACTLY ONCE per Handle, or the repeated
        # option-value combinations look like duplicate variants and the whole product is
        # rejected on import. To put a product in more than one collection, add a bare row
        # that carries only the Handle + Collection - never repeat the variant/image data.
        r={c:'' for c in fieldnames}
        r['Handle']=handle; r['Collection']=collection_name
        return r

    # Main file: one full row-block per product (written once, under its first/primary
    # collection), plus one bare Handle+Collection row for every additional collection it
    # belongs to. This is what fixes the old behavior of repeating the entire variant/image
    # block once per collection, which created duplicate-variant rows under the same Handle
    # and caused Shopify to reject the products on import (import silently showed 0 products).
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader()
        for p in products:
            categories=uniq(p.get('categories') or ([p.get('category')] if p.get('category') else []))
            if categories:
                primary,rest=categories[0],categories[1:]
            else:
                primary,rest='',[]
            for r in build_rows(p,primary): w.writerow(r)
            for collection_name in rest:
                w.writerow(bare_collection_row(p.get('handle',''),collection_name))

    # Per-collection CSVs (one complete, self-sufficient file per collection) - each of these
    # still gets the FULL product block, since within a single collection's own file there's
    # no duplicate-Handle conflict.
    by_collection={}
    for p in products:
        categories=uniq(p.get('categories') or ([p.get('category')] if p.get('category') else []))
        for name in categories:
            by_collection.setdefault(name,[]).append(p)

    collections_dir=os.path.join(out,'collections')
    if by_collection: os.makedirs(collections_dir,exist_ok=True)
    for name,plist in by_collection.items():
        safe=re.sub(r'[^a-zA-Z0-9]+','-',name).strip('-').lower() or 'collection'
        cpath=os.path.join(collections_dir,f'{safe}.csv')
        with open(cpath,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader()
            for p in plist:
                for r in build_rows(p,name): w.writerow(r)

    # per-collection SEO title/description (Shopify's product CSV has no column for this,
    # so it's exported separately for a manual paste into each Collection's edit page,
    # or a Matrixify-style custom collections import).
    if collection_metas:
        meta_path=os.path.join(out,'collections_meta.csv')
        with open(meta_path,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f); w.writerow(['Collection','URL','SEO Title','SEO Description'])
            for name,curl,seo_title,seo_description in collection_metas:
                w.writerow([name,curl,seo_title,seo_description])

    with open(os.path.join(out,'scrape_report.json'),'w',encoding='utf8') as f:json.dump({'url':url,'platform':platform,'stats':stats,'time':time.time()},f,indent=2)
    return path

    # Main file: now fully self-sufficient for multi-collection tagging. Every product gets
    # one complete row-block per collection it belongs to (same Handle repeated, different
    # Collection value each time) - not just its first-discovered "primary" collection.
    # This matches how Matrixify-style bulk importers expect multi-collection assignment
    # (repeat the Handle, vary Collection) and means a product no longer silently drops out
    # of a collection just because it was scraped under a different one first.
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader()
        for p in products:
            categories=uniq(p.get('categories') or ([p.get('category')] if p.get('category') else []))
            collection_values=categories or ['']
            for collection_name in collection_values:
                for r in build_rows(p,collection_name): w.writerow(r)

    # Also keep the per-collection CSVs (one complete, self-sufficient file per collection) -
    # handy when someone wants to import/manage just one collection at a time.
    by_collection={}
    for p in products:
        categories=uniq(p.get('categories') or ([p.get('category')] if p.get('category') else []))
        for name in categories:
            by_collection.setdefault(name,[]).append(p)

    collections_dir=os.path.join(out,'collections')
    if by_collection: os.makedirs(collections_dir,exist_ok=True)
    for name,plist in by_collection.items():
        safe=re.sub(r'[^a-zA-Z0-9]+','-',name).strip('-').lower() or 'collection'
        cpath=os.path.join(collections_dir,f'{safe}.csv')
        with open(cpath,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=fieldnames);w.writeheader()
            for p in plist:
                for r in build_rows(p,name): w.writerow(r)

    # per-collection SEO title/description (Shopify's product CSV has no column for this,
    # so it's exported separately for a manual paste into each Collection's edit page,
    # or a Matrixify-style custom collections import).
    if collection_metas:
        meta_path=os.path.join(out,'collections_meta.csv')
        with open(meta_path,'w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f); w.writerow(['Collection','URL','SEO Title','SEO Description'])
            for name,curl,seo_title,seo_description in collection_metas:
                w.writerow([name,curl,seo_title,seo_description])

    with open(os.path.join(out,'scrape_report.json'),'w',encoding='utf8') as f:json.dump({'url':url,'platform':platform,'stats':stats,'time':time.time()},f,indent=2)
    return path

async def cleanup(output,args):
    # Fixes up an existing checkpoint in place instead of requiring a full re-crawl:
    #  1. Drops any "product" whose own URL is actually a collection/category page - those
    #     got stored as products by the old product_links() class-name bug.
    #  2. Re-fetches every remaining real product URL and re-runs extraction with the fixed
    #     product_gallery_images() logic, so images leaked in from "related products" /
    #     "you may also like" widgets get dropped, while keeping each product's already-known
    #     collection membership intact.
    # 3. Also re-checks every remaining product against looks_like_product_page() - catches
    #    non-product pages (blog articles etc.) that slipped past the URL-based filters
    #    entirely and got scraped with fabricated data (e.g. a stray dollar figure in the
    #    article text mistaken for a price).
    db=DB(output)
    all_products=db.products()
    bad_urls=[p['url'] for p in all_products if any(m in urlparse(p['url']).path.lower() for m in Scraper.COLLECTION_PATH_MARKERS)]
    for u in bad_urls: db.delete_product(u)
    remaining=[p for p in all_products if p['url'] not in bad_urls]
    print(f'Removed {len(bad_urls)} category page(s) that were stored as products.')
    refreshed=0; failed=0; not_product=0
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=args.headless)
        ctx=await browser.new_context(viewport={'width':1440,'height':900})
        page=await ctx.new_page()
        scraper=Scraper(args.url or (remaining[0]['url'] if remaining else 'https://example.com'),args,page,db)
        for i,p in enumerate(remaining,1):
            try:
                await scraper.goto(p['url']); html=await scraper.safe_content()
                page_soup=BeautifulSoup(html,'lxml')
                if not looks_like_product_page(page_soup):
                    db.delete_product(p['url']); not_product+=1
                    print(f'  [{i}/{len(remaining)}] removed (not a product page): {p["url"]}'); continue
                fresh=extract_product(html,p['url'],'')
                data=asdict(fresh)
                data['categories']=p.get('categories') or ([p.get('category')] if p.get('category') else [])
                data['category']=p.get('category','')
                if p.get('handle'): data['handle']=p['handle']
                db.replace_product_data(p['url'],data)
                refreshed+=1; print(f'  [{i}/{len(remaining)}] refreshed: {p["url"]}')
            except Exception as e:
                failed+=1; print(f'  [{i}/{len(remaining)}] failed: {p["url"]} ({e})')
        await browser.close()
    report_path=os.path.join(output,'scrape_report.json')
    platform='Unknown'; stats={}; store_url=args.url or ''
    if os.path.exists(report_path):
        try:
            with open(report_path,encoding='utf8') as f: old=json.load(f)
            platform=old.get('platform',platform); stats=old.get('stats',stats); store_url=store_url or old.get('url','')
        except: pass
    stats=dict(stats); stats['cleanup_removed_category_pages']=len(bad_urls); stats['cleanup_removed_non_product_pages']=not_product; stats['cleanup_refreshed_products']=refreshed; stats['cleanup_failed_refresh']=failed
    path=export(db.products(),output,stats,store_url,platform,db.collection_metas())
    db.close()
    print(f'\nCleanup done. Removed {len(bad_urls)} misfiled category "products" and {not_product} non-product page(s) (blog posts etc). Refreshed {refreshed} products ({failed} failed to refresh, left as-is).')
    print('CSV:',path)

async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--url');ap.add_argument('--output',default='output');ap.add_argument('--reset',action='store_true');ap.add_argument('--headless',action='store_true');ap.add_argument('--max-products',type=int,default=0);ap.add_argument('--max-pages',type=int,default=10000);ap.add_argument('--delay',type=float,default=.7);ap.add_argument('--scrolls',type=int,default=40);ap.add_argument('--timeout',type=int,default=45000);ap.add_argument('--retries',type=int,default=3)
    ap.add_argument('--export-only',action='store_true',help='Skip crawling entirely and just regenerate the CSVs from the existing checkpoint.sqlite in --output')
    ap.add_argument('--cleanup',action='store_true',help='Skip crawling. Remove category pages that were previously misfiled as products, then re-fetch and refresh every remaining product in the --output checkpoint.sqlite using the current extraction logic (fixes contaminated image galleries).')
    a=ap.parse_args()

    if a.cleanup:
        if a.reset:
            print("--reset ignored with --cleanup (that would wipe the checkpoint you're trying to clean up)")
        await cleanup(a.output,a); return

    if a.export_only:
        if a.reset:
            print("--reset ignored with --export-only (that would wipe the checkpoint you're trying to export from)")
        db=DB(a.output)
        report_path=os.path.join(a.output,'scrape_report.json')
        platform='Unknown'; stats={}; store_url=a.url or ''
        if os.path.exists(report_path):
            try:
                with open(report_path,encoding='utf8') as f: old=json.load(f)
                platform=old.get('platform',platform); stats=old.get('stats',stats); store_url=store_url or old.get('url','')
            except: pass
        path=export(db.products(),a.output,stats,store_url,platform,db.collection_metas())
        db.close(); print('\nDONE (export-only, no crawling)'); print('CSV:',path); return

    url=a.url or input('Enter store URL: ').strip()
    if not re.match(r'^https?://',url):url='https://'+url
    if a.reset:
        checkpoint=os.path.join(a.output,'checkpoint.sqlite')
        if os.path.exists(checkpoint):os.remove(checkpoint)
    db=DB(a.output)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=a.headless)
        ctx=await browser.new_context(viewport={'width':1440,'height':900})
        page=await ctx.new_page(); s=Scraper(url,a,page,db)
        cats=await s.categories(); platform=await s.platform();print(f'Platform: {platform} | categories: {len(cats)}')
        s.category_urls={u for _,u in cats}
        pending=db.cats()
        for i,(name,u) in enumerate(pending,1):
            if excluded_collection(name,u):
                db.done(u)
                print(f'\n[{i}/{len(pending)}] skipping collection: {name}')
                continue
            print(f'\n[{i}/{len(pending)}] {name}')
            await s.category(name,u)
        path=export(db.products(),a.output,s.stats,url,platform,db.collection_metas());await browser.close()
    db.close();print('\nDONE');print(json.dumps(s.stats,indent=2));print('CSV:',path);print('Checkpoint:',os.path.join(a.output,'checkpoint.sqlite'))

if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:print('\nStopped. Run again to resume from checkpoint.')