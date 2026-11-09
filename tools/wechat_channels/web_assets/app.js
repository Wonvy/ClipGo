/* Local presentation only. Signed media addresses and decode keys stay in Python. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const dom = Object.fromEntries(['clear-library','clear-dialog','clear-description','confirm-clear','cancel-clear','settings-trigger','settings-menu','auto-refresh','preview-video','preview-loading','preview-message','preview-progress','preview-retry','refresh','search','clear-search','video-count','video-grid','scan-status','scan-message','error-banner','empty-state','empty-title','empty-message','empty-refresh','filter-recognized','filter-all','folder-path','choose-folder','open-folder','download-panel','download-state','download-title','download-amount','download-progress','download-message','cancel-download','open-download','details-dialog','details-title','details-author','details-action','detail-image','detail-placeholder','detail-duration','close-details','toast'].map(id => [id,$(id)]));
  let state = {revision:-1,source:'all',folder:'',scan:{state:'idle',message:''},items:[],download:{state:'idle'}};
  let apiReady = false, booted = false, polling = false, actionPending = false;
  let filter = 'recognized', manualFilter = false, detailId = null, toastTimer = null;
  let renderedRevision = -1, frontendErrors = 0;
  let autoRefresh = true, nextAutoScan = Date.now() + 4500, refreshPending = false;
  let hoveredId = null, selectedId = null, detailItem = null, previewUrl = null, previewRequest = 0;
  let desiredPlaying = true, mediaError = false, mediaStarting = false;
  const newUntil = new Map();
  const discoveryOrder = new Map();
  let discoverySequence = 0;
  let libraryBusy = false, clearedLibrary = false;
  try { autoRefresh = localStorage.getItem('clipgo.autoRefresh') !== 'false'; } catch (_) {}
  const cards = new Map();
  const pageToggle = $('page-button-toggle'), pageStatus = $('page-button-status');
  const certificateKeep = $('page-certificate-keep'), certificateRemove = $('page-certificate-remove');
  const text = value => value == null ? '' : String(value);
  const title = item => text(item.title).trim() || '暂未读取到标题';
  const downloadActive = () => ['downloading','cancelling'].includes(state.download.state);
  const safeThumbnail = value => typeof value === 'string' && value.length < 2_000_000 && /^data:image\/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(value) ? value : null;
  const statNames = {recommend:'喜欢',share:'分享',like:'点赞',comment:'评论'};
  const validCount = value => typeof value==='number' && Number.isSafeInteger(value) && value>=0;
  function formatCount(value) {
    if(!validCount(value))return '—';
    if(value>=100000)return '10万+';
    if(value>=10000)return String(Math.floor(value/1000)/10)+'万';
    return String(value);
  }
  function updateStats(container, engagement) {
    for(const number of container.querySelectorAll('[data-stat]')){
      const key=number.dataset.stat,value=engagement?.[key];
      number.textContent=formatCount(value);
      const label=statNames[key]+'：'+(validCount(value)?String(value):'暂未读取');
      number.parentElement.title=label;
      number.parentElement.setAttribute('aria-label',label);
    }
  }
  function duration(value) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) return '—';
    const seconds = Math.floor(n), h = Math.floor(seconds/3600), m = Math.floor(seconds%3600/60), s = String(seconds%60).padStart(2,'0');
    return h ? `${h}:${String(m).padStart(2,'0')}:${s}` : `${m}:${s}`;
  }
  function notify(message) {
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 4500);
  }
  async function invoke(name, ...args) {
    if (!apiReady || !window.pywebview?.api?.[name]) {
      notify('正在连接下载工具，请稍候。');
      return {ok:false};
    }
    try {
      const result = await window.pywebview.api[name](...args);
      if (result?.ok === false && result.message) notify(result.message);
      return result;
    } catch (_) {
      notify('操作没有完成，请稍后重试。');
      return {ok:false};
    }
  }
  async function refresh(automatic = false) {
    if (!apiReady || state.scan.state === 'scanning' || refreshPending) return;
    refreshPending = true;
    nextAutoScan = Date.now() + 4500;
    dom.refresh.disabled = true;
    document.body.classList.add('is-scanning');
    try {
      const result = automatic ? await window.pywebview.api.scan() : await invoke('scan');
      if (result?.ok === false) { document.body.classList.remove('is-scanning'); nextAutoScan=Date.now()+15000; }
      await fetchState(true);
    } catch (_) { nextAutoScan=Date.now()+15000; }
    finally { refreshPending=false; }
  }
  function setIcon(element, id) { element.querySelector('use')?.setAttribute('href', `#${id}`); }
  function chooseAction(item) {
    const saved = item.status === 'done';
    return {saved, busy:!saved && (downloadActive() || actionPending), label:saved?'打开':item.status==='downloading'?'下载中':item.status==='error'?'重试':'下载'};
  }
  async function downloadItem(id) {
    const current = state.items.find(entry => entry.id === id);
    const item = current || (detailId===id ? detailItem : null);
    if (!item) { notify('视频已不在列表，请刷新后重试。');return; }
    if (item.status === 'done') { await invoke('open_video', id); return; }
    if (!current) { notify('请在微信重新播放此视频，再刷新后下载。');return; }
    if (downloadActive() || actionPending) return;
    actionPending = true;
    render();
    try { await invoke('download', id); await fetchState(true); }
    finally { actionPending = false; render(); }
  }
  function makeCard(item) {
    const node = $('video-card-template').content.firstElementChild.cloneNode(true);
    node.dataset.videoId = item.id;
    const card = {node,cover:node.querySelector('.cover-image'),backdrop:node.querySelector('.cover-backdrop'),placeholder:node.querySelector('.cover-placeholder'),title:node.querySelector('.card-title'),author:node.querySelector('.card-author'),duration:node.querySelector('.duration-badge'),download:node.querySelector('.card-download'),remove:node.querySelector('.card-remove'),saved:node.querySelector('.saved-badge'),fresh:node.querySelector('.new-badge'),error:node.querySelector('.card-error'),thumbnail:undefined};
    node.addEventListener('click', event => { if (!event.target.closest('.card-download,.card-remove')) openDetails(item.id); });
    node.addEventListener('pointerenter', () => { hoveredId=item.id; });
    node.addEventListener('pointerleave', () => { if(hoveredId===item.id)hoveredId=null; });
    node.addEventListener('focusin', () => { selectedId=item.id; });
    node.addEventListener('keydown', event => { if(event.key==='Enter' && event.target===node) { event.preventDefault();openDetails(item.id); } });
    card.download.addEventListener('click', event => { event.stopPropagation();downloadItem(item.id); });
    card.remove.addEventListener('click', event => { event.stopPropagation();removeItem(item.id); });
    card.cover.addEventListener('load', () => { card.placeholder.hidden = true; });
    card.cover.addEventListener('error', () => {
      card.cover.hidden = card.backdrop.hidden = true;
      card.placeholder.hidden = false;
      card.placeholder.querySelector('span').textContent = '暂无封面';
    });
    cards.set(item.id, card);
    return card;
  }
  function updateCard(card, item) {
    card.title.textContent = title(item);
    card.title.title = title(item);
    card.node.setAttribute('aria-label', '播放：' + title(item));
    card.fresh.hidden = !((newUntil.get(item.id)||0)>Date.now());
    card.author.textContent = text(item.author).trim() || '作者暂未读取';
    card.author.title = card.author.textContent;
    card.duration.textContent = duration(item.duration);
    updateStats(card.node.querySelector('.card-stats'),item.engagement);
    const image = safeThumbnail(item.thumbnail);
    if (card.thumbnail !== image) {
      card.thumbnail = image;
      card.cover.hidden = card.backdrop.hidden = !image;
      card.placeholder.hidden = false;
      if (image) { card.cover.src = image; card.backdrop.src = image; }
      else { card.cover.removeAttribute('src'); card.backdrop.removeAttribute('src'); }
    }
    card.placeholder.querySelector('span').textContent = item.thumbnail_state === 'loading' ? '加载封面…' : '暂无封面';
    if (image && card.cover.complete && card.cover.naturalWidth > 0) card.placeholder.hidden = true;
    const action = chooseAction(item);
    card.download.disabled = action.busy;
    card.remove.disabled = libraryBusy;
    card.remove.setAttribute('aria-label','从列表删除：'+title(item));
    card.download.classList.toggle('is-busy', item.status === 'downloading');
    card.download.querySelector('span').textContent = action.label;
    card.download.setAttribute('aria-label', `${action.saved?'打开':'下载'}视频：${title(item)}`);
    setIcon(card.download, action.saved ? 'i-play' : 'i-download');
    card.saved.hidden = !action.saved;
    card.error.hidden = item.status !== 'error';
  }
  function showSkeletons() {
    if (dom['video-grid'].firstElementChild?.classList.contains('skeleton')) return;
    const skeletons = [];
    for (let i = 0; i < 4; i++) {
      const node = document.createElement('div');
      node.className = 'video-card skeleton';
      node.setAttribute('aria-hidden','true');
      for (const name of ['skeleton-cover','skeleton-line','skeleton-line short']) {
        const part = document.createElement('div'); part.className = name; node.append(part);
      }
      skeletons.push(node);
    }
    dom['video-grid'].replaceChildren(...skeletons);
  }
  function render() {
    const scanning = state.scan.state === 'scanning';
    document.body.classList.toggle('is-scanning', scanning);
    dom.refresh.disabled = !apiReady || scanning;
    dom['empty-refresh'].disabled = !apiReady || scanning;
    dom.refresh.querySelector('span').textContent = '刷新';
    dom['auto-refresh'].setAttribute('aria-checked',String(autoRefresh));
    dom['clear-library'].disabled = !apiReady || libraryBusy || !state.items.length;
    dom['clear-description'].textContent = '将从列表移除全部 '+state.items.length+' 个视频，包括搜索或筛选后隐藏的条目。';
    dom['confirm-clear'].disabled = libraryBusy;
    dom['cancel-clear'].disabled = libraryBusy;
    dom['folder-path'].textContent = state.folder || '正在读取保存位置…';
    dom['folder-path'].title = state.folder || '';
    const pageButton = state.page_button || {state:'disabled',message:'在微信页面内显示下载按钮'};
    pageToggle.setAttribute('aria-checked', String(pageButton.state === 'enabled'));
    pageToggle.disabled = !apiReady || ['starting','stopping'].includes(pageButton.state);
    pageStatus.textContent = pageButton.message || '';
    const certificate = state.page_certificate || {keep:false,installed:false};
    $('page-certificate-mode').textContent = certificate.scope === 'machine'
      ? '管理员模式 · 本机用户均信任。启动时可能需要管理员授权。'
      : '证书范围：当前 Windows 用户';
    $('page-certificate-hint').textContent = certificate.scope === 'machine'
      ? '未勾选时关闭即移除。勾选后证书持续受信任，直到你移除。'
      : '下次开启免重复确认。证书会持续受信任，直到移除；首次安装可能需要 Windows 确认。';
    certificateKeep.checked = !!certificate.keep;
    certificateKeep.disabled = !apiReady || ['starting','stopping'].includes(pageButton.state);
    certificateRemove.disabled = certificateKeep.disabled || !certificate.installed;
    $('page-certificate-status').textContent = certificate.installed ? (certificate.keep ? '已保留' : '临时信任') : '未安装';
    const recognized = state.items.filter(item => item.recognized);
    if (!manualFilter && state.scan.state === 'done') filter = recognized.length || !state.items.length ? 'recognized' : 'all';
    const query = dom.search.value.trim().toLocaleLowerCase();
    dom['clear-search'].hidden = !query;
    const eligible = (filter === 'all' ? state.items.slice() : recognized).sort((a,b)=>(discoveryOrder.get(b.id)||0)-(discoveryOrder.get(a.id)||0));
    const visible = eligible.filter(item => !query || `${title(item)} ${text(item.author)}`.toLocaleLowerCase().includes(query));
    dom['video-count'].textContent = visible.length;
    dom['filter-recognized'].querySelector('span').textContent = recognized.length;
    dom['filter-all'].querySelector('span').textContent = state.items.length;
    for (const mode of ['recognized','all']) {
      dom[`filter-${mode}`].classList.toggle('active', filter === mode);
      dom[`filter-${mode}`].setAttribute('aria-pressed', String(filter === mode));
    }
    const thumbLoading = visible.some(item => item.thumbnail_state === 'loading');
    dom['scan-status'].classList.toggle('busy', scanning || thumbLoading);
    dom['scan-status'].classList.toggle('error', state.scan.state === 'error');
    dom['scan-message'].textContent = !apiReady ? '正在连接' : scanning ? '同步中' : state.scan.state === 'error' ? '等待微信播放' : thumbLoading ? '加载封面' : state.scan.state === 'done' ? '已更新' : '';
    dom['error-banner'].hidden = state.scan.state !== 'error';
    dom['error-banner'].textContent = state.scan.state === 'error' ? text(state.scan.message) || '请在对应的微信窗口播放视频后重试。' : '';
    const liveIds = new Set(state.items.map(item => item.id));
    for (const [id, card] of cards) if (!liveIds.has(id)) { card.node.remove(); cards.delete(id); }
    if (scanning && !state.items.length) {
      showSkeletons();
      dom['empty-state'].hidden = true;
    } else {
      for (const child of [...dom['video-grid'].children]) if (!visible.some(item => item.id === child.dataset.videoId)) child.remove();
      visible.forEach((item, index) => {
        const card = cards.get(item.id) || makeCard(item);
        updateCard(card, item);
        if (dom['video-grid'].children[index] !== card.node) dom['video-grid'].insertBefore(card.node, dom['video-grid'].children[index] || null);
      });
      dom['empty-state'].hidden = visible.length > 0;
      dom['empty-title'].textContent = query ? '没有找到匹配的视频' : clearedLibrary && !state.items.length ? '列表已清空' : state.scan.state === 'error' ? '暂时无法读取视频' : state.scan.state === 'done' ? '还没有找到视频' : '先在微信里播放一条视频';
      dom['empty-message'].textContent = query ? '试试其他标题或作者关键词。' : clearedLibrary && !state.items.length ? '新发现的视频会自动加入，已移除的条目不会反复出现。' : state.scan.state === 'error' ? '在微信主界面或独立窗口中播放视频后重试。' : '视频加载后会自动出现在这里。';
      dom['empty-refresh'].hidden = Boolean(query);
    }
    renderDownload();
    if (detailId) updateDetails();
    renderedRevision = state.revision;
  }
  function renderDownload() {
    const task = state.download;
    dom['download-panel'].hidden = !task.state || task.state === 'idle';
    dom['download-panel'].classList.toggle('has-error', task.state === 'error');
    const labels = {downloading:'正在下载',cancelling:'正在取消',done:'下载完成',error:'下载未完成',cancelled:'已取消'};
    dom['download-state'].textContent = labels[task.state] || '';
    dom['download-title'].textContent = text(task.title);
    dom['download-message'].textContent = text(task.message);
    const amount = Number(task.received || 0) / 1048576, total = Number(task.total || 0) / 1048576;
    dom['download-amount'].textContent = total ? `${amount.toFixed(1)} / ${total.toFixed(1)} MB` : amount ? `${amount.toFixed(1)} MB` : '';
    dom['download-progress'].value = Math.max(0,Math.min(100,Number(task.percent)||0));
    dom['cancel-download'].hidden = !downloadActive();
    dom['cancel-download'].disabled = task.state === 'cancelling';
    dom['cancel-download'].textContent = task.state === 'cancelling' ? '取消中' : '取消';
    dom['open-download'].hidden = task.state !== 'done';
    setIcon($('download-symbol'), task.state === 'done' ? 'i-check' : 'i-download');
  }
  async function openDetails(id) {
    const item = state.items.find(entry=>entry.id===id);
    if (!item) return;
    if (detailId === id && dom['details-dialog'].open) { togglePlayback(); return; }
    const request = ++previewRequest;
    releaseMedia();
    detailId = id; detailItem = {...item}; selectedId=id; desiredPlaying=true; mediaError=false;
    closeMenus();
    updateDetails();
    if (detailId && !dom['details-dialog'].open) dom['details-dialog'].showModal();
    const result = await invoke('prepare_preview', id);
    if (request !== previewRequest || detailId !== id) return;
    if (result?.ok === false) {
      mediaError=true;dom['preview-message'].textContent=result.message||'暂时无法播放，请刷新后重试。';
      dom['preview-retry'].hidden=false;dom['preview-loading'].classList.add('has-error');
    }
    await fetchState(true);
  }
  function updateDetails() {
    const item = state.items.find(item => item.id === detailId);
    if (!item) { dom['details-dialog'].close();return; }
    detailItem={...item};
    dom['details-title'].textContent = title(item);
    dom['details-author'].textContent = `${text(item.author).trim() || '作者暂未读取'} · ${duration(item.duration)}`;
    updateStats($('detail-stats'),item.engagement);
    dom['detail-duration'].textContent = duration(item.duration);
    const image = safeThumbnail(item.thumbnail);
    dom['detail-image'].hidden = !image;
    dom['detail-placeholder'].hidden = Boolean(image);
    if (image && dom['detail-image'].getAttribute('src') !== image) dom['detail-image'].src = image;
    if (!image) dom['detail-image'].removeAttribute('src');
    const action = chooseAction(item);
    const unavailable = !action.saved && !state.items.some(entry=>entry.id===detailId);
    dom['details-action'].disabled = action.busy || unavailable;
    dom['details-action'].querySelector('span').textContent = unavailable ? '请刷新后下载' : action.saved ? '打开视频' : action.busy ? '正在下载' : '下载视频';
    setIcon(dom['details-action'], action.saved ? 'i-play' : 'i-download');
    renderPreview();
  }
  function releaseMedia() {
    const video=dom['preview-video'];
    video.pause();video.removeAttribute('src');video.load();video.hidden=true;
    previewUrl=null;mediaStarting=false;
  }
  function validPreviewUrl(value) {
    try { const url=new URL(value);return url.protocol==='http:' && url.hostname==='127.0.0.1' && url.port && !url.username && !url.password ? url.href : null; }
    catch (_) { return null; }
  }
  function renderPreview() {
    const preview=state.preview || {}, video=dom['preview-video'];
    const matching=preview.id===detailId, url=matching && preview.state==='ready' ? validPreviewUrl(preview.url) : null;
    if(url && previewUrl!==url) {
      previewUrl=url;mediaError=false;mediaStarting=true;video.src=url;video.hidden=false;
      if(desiredPlaying) video.play().catch(()=>{ if(previewUrl===url && !video.error) {mediaStarting=false;dom['preview-loading'].hidden=true;} });
    }
    const error=mediaError || (matching && ['error','cancelled'].includes(preview.state));
    dom['preview-loading'].hidden=Boolean(url && !error && !mediaStarting);
    dom['preview-loading'].classList.toggle('has-error',error);
    dom['preview-retry'].hidden=!error;
    dom['preview-progress'].hidden=Boolean(url || error);
    dom['preview-progress'].value=Math.max(0,Math.min(100,Number(preview.percent)||0));
    if (!mediaError) dom['preview-message'].textContent = error ? text(preview.message)||'暂时无法播放' : url ? '正在载入画面…' : matching ? (text(preview.message)||'正在准备播放…') : '正在准备播放…';
    if(url) { dom['detail-image'].hidden=true;dom['detail-placeholder'].hidden=true; }
  }
  function togglePlayback() {
    const video=dom['preview-video'];
    if (!previewUrl) {desiredPlaying=!desiredPlaying;notify(desiredPlaying?'准备完成后自动播放':'准备完成后暂停');return;}
    if (video.paused) {desiredPlaying=true;video.play().catch(()=>notify('点击视频中的播放按钮继续。'));}
    else {desiredPlaying=false;video.pause();}
  }
  async function removeItem(id) {
    if(libraryBusy)return;
    libraryBusy=true;render();
    try {
      const result=await invoke('remove_video',id);
      if(result?.ok) {
        if(detailId===id)dom['details-dialog'].close();
        if(selectedId===id)selectedId=null;
        if(hoveredId===id)hoveredId=null;
        discoveryOrder.delete(id);newUntil.delete(id);
        await fetchState(true);
        notify('已从列表移除，下载文件保留');
      }
    } finally {libraryBusy=false;render();}
  }
  async function clearLibrary() {
    if(libraryBusy)return;
    libraryBusy=true;render();
    try {
      const result=await invoke('clear_videos');
      if(result?.ok) {
        if(dom['details-dialog'].open)dom['details-dialog'].close();
        dom['clear-dialog'].close();dom.search.value='';
        selectedId=hoveredId=null;clearedLibrary=true;
        discoveryOrder.clear();newUntil.clear();
        await fetchState(true);
        notify('列表已清空，下载文件保留');
      }
    } finally {libraryBusy=false;render();}
  }
  function closeMenus() {
    dom['settings-menu'].hidden=true;dom['settings-trigger'].setAttribute('aria-expanded','false');
  }
  function toggleMenu(name) {
    const open=dom[name+'-menu'].hidden;
    closeMenus();
    dom[name+'-menu'].hidden=!open;dom[name+'-trigger'].setAttribute('aria-expanded',String(open));
  }
  async function fetchState(force = false) {
    if (!apiReady || polling) return;
    polling = true;
    try {
      const next = await window.pywebview.api.get_state(force ? null : state.revision);
      if (next && Array.isArray(next.items) && (force || next.revision !== state.revision)) {
        if (next.auto_refresh_enabled === false) autoRefresh=false;
        const rank=++discoverySequence;
        for(const item of next.items) if(!discoveryOrder.has(item.id))discoveryOrder.set(item.id,rank);
        if(discoveryOrder.size>2000) {
          const liveIds=new Set(next.items.map(item=>item.id));
          for(const id of discoveryOrder.keys())if(!liveIds.has(id)){discoveryOrder.delete(id);newUntil.delete(id);}
        }
        if(state.scan.state==='scanning' && next.scan.state!=='scanning') nextAutoScan=Date.now()+(next.scan.state==='error'?15000:4000);
        if(state.items.length) {
          const existing=new Set(state.items.map(item=>item.id));
          for(const item of next.items) if(!existing.has(item.id)) newUntil.set(item.id,Date.now()+10000);
        }
        state = next;
        render();
      }
    } catch (_) {
      dom['scan-message'].textContent = '连接暂时中断，正在重试…';
    } finally { polling = false; }
  }
  async function start() {
    if (booted || !window.pywebview?.api) return;
    booted = apiReady = true;
    await fetchState(true);
    if (state.scan.state === 'idle') await refresh();
    const poll = async () => {
      await fetchState();
      if(autoRefresh && Date.now()>=nextAutoScan && state.scan.state!=='scanning') await refresh(true);
      setTimeout(poll, 500);
    };
    setTimeout(poll, 500);
  }
  dom.refresh.addEventListener('click', () => refresh());
  dom['empty-refresh'].addEventListener('click', () => refresh());
  dom.search.addEventListener('input', render);
  dom['clear-search'].addEventListener('click', () => { dom.search.value = ''; dom.search.focus(); render(); });
  for (const mode of ['recognized','all']) dom[`filter-${mode}`].addEventListener('click', () => { filter=mode;manualFilter=true;render(); });
  dom['choose-folder'].addEventListener('click', async () => { dom['choose-folder'].disabled=true;try{await invoke('choose_folder');await fetchState(true);}finally{dom['choose-folder'].disabled=false;} });
  dom['open-folder'].addEventListener('click', () => invoke('open_folder'));
  dom['cancel-download'].addEventListener('click', async () => { await invoke('cancel_download'); await fetchState(true); });
  dom['open-download'].addEventListener('click', () => invoke('open_video', state.download.id));
  dom['close-details'].addEventListener('click', () => dom['details-dialog'].close());
  dom['details-dialog'].addEventListener('close', () => { detailId=null;detailItem=null;previewRequest++;releaseMedia();invoke('close_preview'); });
  dom['details-dialog'].addEventListener('click', event => { if(event.target===dom['details-dialog']) { const box=event.target.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)event.target.close(); } });
  dom['details-action'].addEventListener('click', () => { if(detailId)downloadItem(detailId); });
  dom['preview-video'].addEventListener('loadeddata',()=>{mediaStarting=false;if(detailId)renderPreview();});
  dom['preview-video'].addEventListener('error',()=>{if(previewUrl){mediaStarting=false;mediaError=true;dom['preview-message'].textContent='此视频暂时无法在窗口内播放，可下载后打开。';renderPreview();}});
  dom['preview-retry'].addEventListener('click',()=>{const id=detailId;detailId=null;if(id)openDetails(id);});
  dom['settings-trigger'].addEventListener('click',()=>toggleMenu('settings'));
  pageToggle.addEventListener('click', async()=>{
    const enabled = state.page_button?.state !== 'enabled';
    pageToggle.disabled=true;
    const result=await invoke('set_page_button',enabled);
    if(result?.ok===false)notify(result.message);
    await fetchState(true);
  });
  certificateKeep.addEventListener('change', async()=>{
    certificateKeep.disabled=true;
    const result=await invoke('set_page_certificate_keep',certificateKeep.checked);
    if(result?.message)notify(result.message);
    await fetchState(true);
  });
  certificateRemove.addEventListener('click', async()=>{
    certificateRemove.disabled=true;
    const result=await invoke('remove_page_certificate');
    if(result?.ok===false)notify(result.message);
    await fetchState(true);
  });
  dom['clear-library'].addEventListener('click',()=>{closeMenus();render();dom['clear-dialog'].showModal();});
  dom['confirm-clear'].addEventListener('click',clearLibrary);
  dom['cancel-clear'].addEventListener('click',()=>dom['clear-dialog'].close());
  dom['auto-refresh'].addEventListener('click',()=>{autoRefresh=!autoRefresh;nextAutoScan=Date.now();try{localStorage.setItem('clipgo.autoRefresh',String(autoRefresh));}catch(_){}render();});
  document.addEventListener('click',event=>{if(!event.target.closest('.settings-menu,.settings-trigger'))closeMenus();});
  document.addEventListener('keydown', event => {
    if(dom['clear-dialog'].open)return;
    if(event.ctrlKey && event.key.toLowerCase()==='f'){event.preventDefault();dom.search.focus();}
    if(event.ctrlKey && event.key.toLowerCase()==='r'){event.preventDefault();refresh();}
    if(event.key==='Escape'){closeMenus();return;}
    if(event.code==='Space' && !event.repeat && !event.ctrlKey && !event.altKey && !event.metaKey && !event.target.closest('input,textarea,[contenteditable="true"],.popover,.card-remove,.card-download,#clear-library')){
      if(dom['details-dialog'].open){event.preventDefault();togglePlayback();}
      else {const id=hoveredId||event.target.closest('[data-video-id]')?.dataset.videoId||(event.target===document.body?selectedId:null);if(id){event.preventDefault();openDetails(id);}}
    }
  });
  window.addEventListener('error', () => { frontendErrors++; });
  window.addEventListener('unhandledrejection', () => { frontendErrors++; });
  window.clipgoDiagnostics = () => {
    const rows=[...dom['video-grid'].querySelectorAll('[data-video-id]')];
    const countKnown=row=>[...row.querySelectorAll('[data-stat]')].filter(number=>number.textContent!=='—').length;
    return {ready:apiReady,cards:rows.length,
      titled:rows.filter(node=>{const label=text(state.items.find(item=>item.id===node.dataset.videoId)?.title).trim();return label&&label!=='暂未读取到标题';}).length,
      thumbnails:[...dom['video-grid'].querySelectorAll('img[data-cover]')].filter(img=>!img.hidden&&img.complete&&img.naturalWidth>0).length,
      engagementRows:rows.filter(row=>countKnown(row)>0).length,
      completeEngagementRows:rows.filter(row=>countKnown(row)===4).length,
      renderedRevision,horizontalOverflow:document.documentElement.scrollWidth>window.innerWidth,frontendErrors};
  };
  window.addEventListener('pywebviewready', start, {once:true});
  render();
  if(window.pywebview?.api) start();
})();
