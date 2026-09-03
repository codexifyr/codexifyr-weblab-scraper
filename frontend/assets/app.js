const $=(q,r=document)=>r.querySelector(q), $$=(q,r=document)=>[...r.querySelectorAll(q)];
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function fmtTime(sec){sec=Math.max(0,Number(sec)||0);let h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=Math.floor(sec%60);return [h,m,s].map(x=>String(x).padStart(2,'0')).join(':')}
async function api(url,opt={}){let r=await fetch(url,{cache:'no-store',...opt});let d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d}
function post(url,data={}){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})}

function ensureAnimatedBackground(){
  if(document.getElementById('cxBackground')) return;
  document.body.insertAdjacentHTML('afterbegin',`
    <div id="cxBackground" class="cx-background" aria-hidden="true">
      <canvas id="cxParticles"></canvas>
      <div class="cx-aurora cx-aurora-one"></div>
      <div class="cx-aurora cx-aurora-two"></div>
      <div class="cx-grid"></div>
      <div class="cx-wave cx-wave-a"></div>
      <div class="cx-wave cx-wave-b"></div>
      <div class="cx-wave cx-wave-c"></div>
      <div class="cx-vignette"></div>
    </div>`);
  initParticles();
}

function initParticles(){
  const canvas=document.getElementById('cxParticles');
  if(!canvas || canvas.dataset.ready==='1') return;
  canvas.dataset.ready='1';
  const ctx=canvas.getContext('2d');
  const reduced=window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let w=0,h=0,dpr=1,particles=[],raf=0;

  function makeParticles(){
    const count=w<700?34:Math.min(110,Math.max(60,Math.floor(w/18)));
    particles=Array.from({length:count},()=>({
      x:Math.random()*w,
      y:Math.random()*h,
      r:.45+Math.random()*1.8,
      vx:(Math.random()-.5)*.12,
      vy:-.025-Math.random()*.16,
      a:.18+Math.random()*.62,
      pulse:Math.random()*Math.PI*2
    }));
  }

  function resize(){
    w=window.innerWidth; h=window.innerHeight; dpr=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.floor(w*dpr); canvas.height=Math.floor(h*dpr);
    canvas.style.width=w+'px'; canvas.style.height=h+'px';
    ctx.setTransform(dpr,0,0,dpr,0,0);
    makeParticles();
  }

  function draw(t=0){
    ctx.clearRect(0,0,w,h);
    for(const p of particles){
      if(!reduced){
        p.x+=p.vx; p.y+=p.vy; p.pulse+=.015;
        if(p.y<-12){p.y=h+12;p.x=Math.random()*w}
        if(p.x<-12)p.x=w+12; if(p.x>w+12)p.x=-12;
      }
      const alpha=Math.max(.08,p.a*(.72+.28*Math.sin(p.pulse)));
      ctx.beginPath();
      ctx.fillStyle=`rgba(197,112,255,${alpha})`;
      ctx.shadowColor='rgba(164,70,255,.88)';
      ctx.shadowBlur=9;
      ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fill();
    }
    ctx.shadowBlur=0;
    raf=requestAnimationFrame(draw);
  }

  resize(); draw();
  window.addEventListener('resize',resize,{passive:true});
  window.addEventListener('beforeunload',()=>cancelAnimationFrame(raf),{once:true});
}

function shell(active){
  const nav=[['/','Home'],['/scraper','Website Scraper'],['/repair','File Correction'],['/shopify','Shopify Scraper'],['/jobs','Job History'],['/validation','Validation'],['/export','Migration / Export'],['/plugin','WordPress Plugin'],['/docs','Info']];
  let top=$('.topbar');
  if(top) top.innerHTML=`<div class="nav"><a class="brand-mini" href="/"><img src="/assets/favicon-64.png" alt=""><span>Codexifyr <em>WebLab</em></span></a><div class="navlinks">${nav.map(([u,n])=>`<a class="${active===u?'active':''}" href="${u}">${n}</a>`).join('')}</div><span id="jobPill" class="job-pill">0 active jobs</span><button class="hamb" aria-label="Menu" aria-expanded="false">☰</button></div>`;
  $('.hamb')?.addEventListener('click',e=>{
    const links=$('.navlinks'); links?.classList.toggle('open');
    e.currentTarget.setAttribute('aria-expanded',links?.classList.contains('open')?'true':'false');
  });
  $$('.navlinks a').forEach(a=>a.addEventListener('click',()=>$('.navlinks')?.classList.remove('open')));
  ensureAnimatedBackground();
  pollJobsPill(); setInterval(pollJobsPill,2500);
}

async function pollJobsPill(){try{let {jobs}=await api('/api/jobs');let active=jobs.filter(j=>j.running||j.status==='waiting_captcha');let p=$('#jobPill');if(p){p.textContent=`${active.length} active job${active.length===1?'':'s'}`;p.classList.toggle('live',active.length>0)}}catch{}}
function jobStatusTag(j){let c=j.status==='complete'?'ok':j.status==='error'?'err':['stopped','interrupted','waiting_captcha'].includes(j.status)?'warn':'';return `<span class="tag ${c}">${esc(j.status)}</span>`}
function footer(){let f=$('.footer');if(f)f.innerHTML='Codexifyr WebLab · Local-first scrape, repair, validate and migrate workspace · Git/GitHub actions are never automatic.'}
