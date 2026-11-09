(() => {
  'use strict';
  if (window.__clipgoPage) return;
  const script = document.currentScript;
  const config = window.__clipgoPageConfig || {};
  const base = script?.dataset.clipgoBase || config.basePath || config.base;
  if (!/^\/__clipgo\/[A-Za-z0-9_-]{24,128}$/.test(base || '')) return;
  const sources = new Map();
  let current = null, currentKey = '', selectedSource = null, busy = false;
  let connection = true, ended = false, scheduled = false, fallback = false;
  let downloadId = null, progress = null, diagnosticKey = '', toastTimer;
  let hookCount=0, detailSeen=false, videoCount=0, vueComponents=0, vueFeed=false;
  const buttons = new Set();
  const text = (value, length) => typeof value === 'string' ? value.slice(0, length) : '';
  const scalar = value => typeof value === 'string' ? value.slice(0, 128) :
    Number.isSafeInteger(value) ? String(value) : undefined;
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : undefined;
  const unref = value => value && typeof value === 'object' && 'value' in value ? value.value : value;

  function cleanFeed(value) {
    value = unref(value);
    if (!value || typeof value !== 'object') return null;
    const description = value.objectDesc;
    const media = description?.media?.[0];
    if (!description || !media || typeof media.url !== 'string' || media.url.length>16384 || /[\u0000-\u0020\u007f]/.test(media.url)) return null;
    if (description.mediaType != null && Number(description.mediaType) !== 4) return null;
    let mediaUrl;
    try {
      const url = new URL(media.url);
      if (!['https:','http:'].includes(url.protocol) ||
          !['finder.video.qq.com','wxapp.tc.qq.com'].includes(url.hostname) ||
          url.username || url.password || url.hash || (url.port && url.port !== '443')) return null;
      // The official Feed.videoUrl getter maps this legacy media origin to the
      // HTTPS finder origin too. The backend still accepts only that one host.
      const origin=media.url.match(/^https?:\/\/([^/?#]+)/i);
      if(!origin) return null;
      const port=origin[1].match(/:(\d+)$/);
      if(port && port[1]!=='443') return null;
      // Replace only the origin text. URL.href would reserialize characters in
      // the signed query; retain every path/query byte supplied by the page.
      mediaUrl='https://finder.video.qq.com'+(port?':443':'')+media.url.slice(origin[0].length);
    } catch { return null; }
    const item = {url:text(mediaUrl,16384),urlToken:text(media.urlToken,8192),
      thumbUrl:text(media.thumbUrl,16384),coverUrl:text(media.coverUrl,16384)};
    const key = scalar(media.decodeKey);
    if (key && /^\d{1,20}$/.test(key)) item.decodeKey = key;
    for (const name of ['videoPlayLen','duration']) {
      const number = Number(media[name]);
      if (Number.isFinite(number) && number >= 0 && number < 10000000) item[name] = number;
    }
    const feed = {objectDesc:{description:text(description.description,4096),mediaType:4,media:[item]},
      contact:{nickname:text(value.contact?.nickname,256)}};
    for (const name of ['id','objectId']) { const id=scalar(value[name]); if (id) feed[name]=id; }
    for (const name of ['favCount','forwardCount','likeCount','commentCount']) {
      const number=count(value[name]); if(number !== undefined) feed[name]=number;
    }
    return feed;
  }
  const keyOf = feed => feed ? JSON.stringify([feed.id,feed.objectId,feed.objectDesc.media[0].url,
    feed.objectDesc.media[0].urlToken,feed.objectDesc.media[0].decodeKey]) : '';

  async function request(path, data) {
    const options = {credentials:'omit',cache:'no-store'};
    if (data !== undefined) Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(),5000);
    options.signal=controller.signal;
    try {
      const response=await fetch(base+'/'+path,options);
      if(!response.ok) throw Error('bridge');
      const result=await response.json();
      connection=true;
      return result;
    } finally { clearTimeout(timeout); }
  }
  const event = value => request('event',value).catch(() => {connection=false;render();});

  function publish(feed, source) {
    const normalized=cleanFeed(feed);
    const key=keyOf(normalized);
    if(source) selectedSource=source;
    if(key === currentKey) return;
    current=normalized; currentKey=key;
    render();
    event({kind:'current',feed:normalized});
  }
  function selected(ref) {
    const state=unref(ref);
    if(!state || !Array.isArray(state.feeds)) return null;
    const index=Number(unref(state.currentFeedIndex));
    if(!Number.isInteger(index) || index<0 || index>=state.feeds.length) return null;
    return cleanFeed(state.feeds[index]);
  }
  function track(ref, name) {
    if(ref && typeof ref==='object') sources.set(ref,{name});
    return ref;
  }
  function wrap(fn, read, name) {
    if(typeof fn !== 'function') return fn;
    return function(...args) {
      hookCount=Math.min(32,hookCount+1);
      publish(null);
      const result=Reflect.apply(fn,this,args);
      const completed=() => {
        try {const ref=read();track(ref,name);publish(selected(ref),ref);} catch {publish(null);}
      };
      if(result && typeof result.then==='function') Promise.resolve(result).then(completed,()=>{});
      else completed();
      return result;
    };
  }
  function detail(result) {
    detailSeen=true;
    if(selectedSource && selected(selectedSource)) return;
    publish(result?.data?.object || result?.resp?.data?.object);
  }

  function visibleSlideFeed() {
    const videos=Array.from(document.querySelectorAll('video'));
    videoCount=Math.min(32,videos.length);vueComponents=0;vueFeed=false;
    const visible=videos.map(video=>{
      const rect=video.getBoundingClientRect();
      const width=Math.max(0,Math.min(rect.right,innerWidth)-Math.max(rect.left,0));
      const height=Math.max(0,Math.min(rect.bottom,innerHeight)-Math.max(rect.top,0));
      return {video,area:width*height};
    }).filter(item=>item.area>10000).sort((a,b)=>b.area-a.area);
    if(!visible.length || (visible[1] && visible[0].area<visible[1].area*1.2)) return null;
    let node=visible[0].video;
    const values=new Map();
    for(let depth=0;node && depth<8;depth++,node=node.parentElement) {
      let component=node.__vueParentComponent || node.__vue__;
      for(let level=0;component && level<3;level++,component=component.parent) {
        vueComponents=Math.min(32,vueComponents+1);
        for(const state of [component.props,component.setupState,component.$props]) {
          if(!state || typeof state!=='object') continue;
          for(const name of ['feed','currentFeed','finderObject','feedInfo','object','item','data']) {
            const feed=cleanFeed(state[name]);
            if(feed) {values.set(keyOf(feed),feed);vueFeed=true;}
          }
        }
      }
      if(node.classList?.contains('slides-item')) break;
    }
    return values.size===1 ? values.values().next().value : null;
  }
  function refreshCurrent() {
    if(ended) return;
    const slide=visibleSlideFeed();
    if(slide) {publish(slide);return;}
    if(selectedSource) {publish(selected(selectedSource),selectedSource);return;}
    const found=[];
    for(const ref of sources.keys()) {const feed=selected(ref);if(feed) found.push({ref,feed});}
    const keys=new Set(found.map(item=>keyOf(item.feed)));
    if(keys.size===1) {publish(found[0].feed,found[0].ref);}
    else if(keys.size>1) publish(null);
  }

  function notice(message) {
    if(!document.body) return;
    let toast=document.getElementById('clipgo-page-toast');
    if(!toast) {toast=document.createElement('div');toast.id='clipgo-page-toast';toast.setAttribute('role','status');document.body.append(toast);}
    toast.textContent=text(message,160);toast.hidden=false;
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toast.hidden=true;},4500);
  }
  async function download(eventObject) {
    eventObject.preventDefault();eventObject.stopPropagation();
    refreshCurrent();
    if(busy) return;
    if(!current) {notice('正在识别当前视频，请播放几秒后重试');return;}
    const snapshot=JSON.parse(JSON.stringify(current));
    busy=true;progress=null;render();
    try {
      const result=await request('download',{feed:snapshot});
      if(!result.ok) {busy=false;notice(result.message || '暂时无法下载，请在 ClipGo 中查看');}
      else {downloadId=result.id || result.download?.id || null;notice('已开始下载');pollStatus();}
    } catch {busy=false;connection=false;notice('请打开 ClipGo 并启用页内下载');}
    render();
  }
  function makeButton(sidebar=false) {
    const button=document.createElement('button');button.type='button';
    button.className='clipgo-page-download'+(sidebar?' clipgo-page-sidebar':'');
    button.setAttribute('aria-label','ClipGo 下载');
    button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/></svg><span>下载</span>';
    button.addEventListener('click',download);buttons.add(button);return button;
  }
  function render() {
    for(const button of buttons) {
      if(!button.isConnected) {buttons.delete(button);continue;}
      const label=busy?(progress==null?'下载中':`${progress}%`):'下载';
      if(button.lastElementChild.textContent!==label) button.lastElementChild.textContent=label;
      button.disabled=busy || !current || !connection;
      button.title=!connection?'ClipGo 下载 · 请打开 ClipGo 并启用页内下载':!current?'ClipGo 下载 · 正在识别当前视频':busy?'ClipGo 下载 · 正在保存当前视频':'ClipGo 下载';
    }
  }
  function reportButtons() {
    const activeButtons=Array.from(buttons).filter(button=>button.isConnected);
    const position=activeButtons.some(button=>!button.classList.contains('clipgo-page-sidebar'))?'toolbar':activeButtons.length?'sidebar':'none';
    const candidateCount=Array.from(sources.keys()).filter(ref=>selected(ref)).length;
    const diagnostics={source_count:Math.min(32,sources.size),candidate_count:Math.min(32,candidateCount),
      selected_index_valid:!!(selectedSource && selected(selectedSource)) || candidateCount>0,
      hook_count:hookCount,detail_seen:detailSeen,video_count:videoCount,
      vue_component:vueComponents>0,vue_components:vueComponents,vue_feed:vueFeed};
    const key=JSON.stringify([activeButtons.length,!!current,position,diagnostics]);
    if(key!==diagnosticKey) {diagnosticKey=key;event({kind:'diagnostic',buttons:Math.min(activeButtons.length,32),active:!!current,position,
      placement:position==='toolbar'?'inline':position==='sidebar'?'floating':'none',page:location.pathname,...diagnostics});}
  }
  function mount() {
    scheduled=false;if(ended || !document.body) return;
    const parents=new Set();
    for(const item of document.querySelectorAll('.slides-item .click-box.op-item')) if(item.parentElement) parents.add(item.parentElement);
    for(const parent of document.querySelectorAll('.full-opr-wrp.layout-col,.full-opr-wrp.layout-row')) parents.add(parent);
    let count=0;
    for(const parent of parents) {
      if(count++>=24) break;
      if(!Array.from(parent.children).some(child=>child.classList?.contains('clipgo-page-download'))) parent.append(makeButton());
    }
    const sidebar=document.querySelector('.clipgo-page-sidebar');
    if(parents.size && sidebar) sidebar.remove();
    else if(!parents.size && fallback && !sidebar) document.body.append(makeButton(true));
    render();reportButtons();
  }
  function schedule() {if(!scheduled){scheduled=true;requestAnimationFrame(mount);}}
  async function pollStatus() {
    if(ended) return;
    try {
      const result=await request('status');
      const job=result.download;
      if(busy && job && (!downloadId || !job.id || job.id===downloadId)) {
        const percent=Number(job.percent);progress=Number.isFinite(percent)?Math.max(0,Math.min(100,Math.floor(percent))):null;
        if(['done','error','cancelled','saved'].includes(job.state)) {
          busy=false;notice(job.message || (['done','saved'].includes(job.state)?'视频已保存':'下载未完成，请在 ClipGo 中查看'));
        }
      }
    } catch {connection=false;}
    render();reportButtons();
  }
  const style=document.createElement('style');
  if(script?.nonce) style.nonce=script.nonce;
  style.textContent=`.clipgo-page-download{box-sizing:border-box!important;display:inline-flex!important;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-width:44px;min-height:44px;padding:6px 10px;border:0;border-radius:8px;background:transparent;color:#8b5cf6;font:12px/1.4 system-ui,"Microsoft YaHei",sans-serif;cursor:pointer;flex-shrink:0}.clipgo-page-download svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.clipgo-page-download:hover:not(:disabled){background:rgba(139,92,246,.16)}.clipgo-page-download:focus-visible{outline:2px solid #8b5cf6;outline-offset:2px}.clipgo-page-download:disabled{opacity:.45;cursor:default}.clipgo-page-sidebar{position:fixed!important;right:20px;bottom:180px;z-index:2147482000;background:rgba(20,24,28,.92);border:1px solid #8b5cf6;box-shadow:0 4px 20px #0003;padding:10px 14px}.clipgo-page-sidebar:hover:not(:disabled){background:#302342}#clipgo-page-toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);max-width:min(80vw,520px);padding:12px 18px;border-radius:10px;background:rgba(20,24,28,.94);color:white;font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;z-index:2147482100;pointer-events:none;box-shadow:0 6px 24px #0003}#clipgo-page-toast[hidden]{display:none}`;
  (document.head || document.documentElement).append(style);
  window.__clipgoPage={track,wrap,detail,refresh:refreshCurrent,diagnostics:()=>({buttons:Array.from(buttons).filter(b=>b.isConnected).length,active:!!current,connected:connection})};
  const observer=new MutationObserver(schedule);
  function start() {
    observer.observe(document.body,{childList:true,subtree:true});mount();
    event({kind:'ready',page:location.pathname});pollStatus();
  }
  if(document.body) start();else document.addEventListener('DOMContentLoaded',start,{once:true});
  const currentTimer=setInterval(()=>{refreshCurrent();reportButtons();},350);
  const statusTimer=setInterval(pollStatus,1500);
  setTimeout(()=>{fallback=true;mount();},3000);
  window.addEventListener('pagehide',()=>{ended=true;observer.disconnect();clearInterval(currentTimer);clearInterval(statusTimer);},{once:true});
})();
