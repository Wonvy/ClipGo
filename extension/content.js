/* global CLIPGO, CLIPGO_DOUYIN */
// In-page quick download button for supported video sites.
// One floating button lives in a closed shadow root on <body>; when the mouse is
// over a video thumbnail or player, it's positioned over that element's top-left
// corner. Living outside the page's DOM tree means it never triggers the
// thumbnail's own link, and it sits above YouTube's hover preview.
(() => {
  if (window.__clipgoQuick) return;
  window.__clipgoQuick = true;

  const MIN_W = 100;
  const MIN_H = 56;
  const ACTIVE = new Set(["queued", "running", "merging"]);
  const isYouTube = /(^|\.)youtube\.com$/.test(location.hostname);
  const isBilibili = /(^|\.)bilibili\.com$/.test(location.hostname);
  const isTwitter = /(^|\.)(x|twitter)\.com$/.test(location.hostname);
  const domSite = /(^|\.)xiaohongshu\.com$/.test(location.hostname) ? "xiaohongshu"
    : /(^|\.)douyin\.com$/.test(location.hostname) ? "douyin"
    : /(^|\.)tiktok\.com$/.test(location.hostname) ? "tiktok"
    : location.hostname === "v.qq.com" ? "tencent" : null;
  const BILI_PLAYER = ".bpx-player-container, .bilibili-player-video-wrap, #bilibili-player";
  const TWITTER_MEDIA = 'video, [data-testid="videoPlayer"], [data-testid="videoComponent"]';
  const TWITTER_QUOTE = '[data-testid="quoteTweet"], div[role="link"]';
  const DOM_MEDIA = 'video, .xgplayer, xg-video-container, .tiktok-web-player, [data-e2e="browse-video"], #player-component, #main-player';

  let enabled = true;
  let tasks = [];
  let current = null;        // { url, el, pageBound?, twitterBound?, domBound? }
  let hideTimer = null;
  let mouse = { x: -1, y: -1 };
  let rafPending = false;
  let flash = null;          // { url, text, cls, until }
  const douyinRecords = new Map();
  let lastDouyinRenderData = null;

  // ---------------------------------------------------------------- UI

  const host = document.createElement("clipgo-quick");
  host.setAttribute("data-clipgo-version", "1.2.3");
  if (domSite === "douyin") host.setAttribute("data-clipgo-media-parser", typeof CLIPGO_DOUYIN === "undefined" ? "missing" : "ready");
  host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";
  const root = host.attachShadow({ mode: "closed" });
  // Built with DOM calls, not innerHTML: YouTube enforces Trusted Types.
  const style = document.createElement("style");
  style.textContent = `
      :host { all: initial; }
      .btn {
        position: fixed; display: none; align-items: center; gap: 5px;
        padding: 5px 10px 5px 8px; border: 0; border-radius: 999px;
        font: 600 12px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        color: #fff; background: linear-gradient(135deg, #e8364f, #8b5cf6);
        box-shadow: 0 2px 10px rgba(0,0,0,.35); cursor: pointer;
        opacity: 0; transform: translateY(-2px);
        transition: opacity .15s ease, transform .15s ease, filter .15s;
        white-space: nowrap; user-select: none;
      }
      .btn.show { display: inline-flex; opacity: 1; transform: none; }
      .btn:hover { filter: brightness(1.1); }
      .btn.busy { background: rgba(20,20,24,.85); cursor: default; }
      .btn.ok { background: #16a34a; }
      .btn.err { background: #dc2626; }
      .btn svg { width: 14px; height: 14px; flex: none; }
      .toast {
        position: fixed; left: 50%; bottom: 28px; transform: translate(-50%, 8px);
        max-width: min(520px, calc(100vw - 32px)); padding: 10px 14px; border-radius: 10px;
        background: rgba(20,20,24,.94); color: #fff; opacity: 0; pointer-events: none;
        font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        box-shadow: 0 6px 24px rgba(0,0,0,.35); transition: opacity .2s, transform .2s;
      }
      .toast.show { opacity: 1; transform: translate(-50%, 0); }
      .toast b { color: #fda4af; font-weight: 600; }
  `;
  const btn = document.createElement("button");
  btn.className = "btn";
  btn.type = "button";
  btn.title = CLIPGO.t("content_download_title", "用 ClipGo 下载为 MP4", []);
  const SVG = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  for (const [k, v] of Object.entries({ fill: "none", stroke: "currentColor", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" })) {
    svg.setAttribute(k, v);
  }
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", "M12 4v11M7 10.5l5 5 5-5M5 20h14");
  svg.appendChild(path);
  const label = document.createElement("span");
  label.textContent = CLIPGO.t("content_mp4", "MP4", []);
  btn.append(svg, label);
  const toastEl = document.createElement("div");
  toastEl.className = "toast";
  toastEl.setAttribute("role", "status");
  root.append(style, btn, toastEl);
  let toastTimer = null;

  function mount() {
    if (!host.isConnected && document.body) document.body.appendChild(host);
  }

  function toast(text, ms = 3500, prefix = "") {
    toastEl.textContent = "";
    if (prefix) {
      const b = document.createElement("b");
      b.textContent = prefix;
      toastEl.append(b);
    }
    toastEl.append(text);
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), ms);
  }

  // ---------------------------------------------------------------- targets

  function bigEnough(el, minW = MIN_W, minH = MIN_H) {
    const r = el.getBoundingClientRect();
    return r.width >= minW && r.height >= minH;
  }

  function videoLinkIn(container) {
    for (const a of container.querySelectorAll("a[href]")) {
      const info = CLIPGO.detect(a.href);
      if (info && info.isVideo) return info.url;
    }
    return null;
  }

  function pageVideoUrl() {
    const info = CLIPGO.detect(location.href);
    return info && info.isVideo ? info.url : null;
  }

  function siteVideoUrl(raw) {
    let absolute;
    try { absolute = new URL(raw, location.href).href; } catch (_) { return null; }
    const info = CLIPGO.detect(absolute);
    return info && info.isVideo && info.site === domSite ? info.url : null;
  }

  function visibleArea(el) {
    if (!el || !el.isConnected || el.closest('[hidden], [aria-hidden="true"]')) return 0;
    const r = el.getBoundingClientRect();
    const width = Math.max(0, Math.min(r.left + r.width, window.innerWidth) - Math.max(r.left, 0));
    const height = Math.max(0, Math.min(r.top + r.height, window.innerHeight) - Math.max(r.top, 0));
    if (!width || !height) return 0;
    // Opacity is not inherited: an invisible modal can retain full child rects.
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || Number(style.opacity) === 0) return 0;
    }
    return width * height;
  }

  function visibleNoteModals() {
    return [...document.querySelectorAll(".note-detail-mask")].filter((node) => visibleArea(node));
  }

  function xhsCardLink(card) {
    const noteId = card.getAttribute("data-note-id");
    if (!noteId) return null;
    const candidates = [];
    for (const link of card.querySelectorAll("a[href]")) {
      if (!visibleArea(link)) continue;
      const url = siteVideoUrl(link.href);
      if (!url || new URL(url).pathname.split("/").filter(Boolean).pop() !== noteId) continue;
      const token = new URL(url).searchParams.has("xsec_token");
      const score = (token ? 4 : 0) + (link.classList.contains("cover") ? 2 : link.classList.contains("title") ? 1 : 0);
      candidates.push({ url, el: link, score });
    }
    // Feed cards also contain a hidden tokenless link. The visible signed cover
    // is the usable share URL; keep xsec_token through normalization.
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0] || null;
  }

  function douyinCardUrl(el) {
    const card = el.closest("[data-aweme-id]");
    if (card) {
      const id = card.getAttribute("data-aweme-id");
      return /^[1-9]\d*$/.test(id || "") ? siteVideoUrl("https://www.douyin.com/video/" + id) : null;
    }
    // The site's preview card uses a DIV[href], not an anchor element.
    const preview = el.closest(".waterfall-videoCardContainer");
    return preview ? siteVideoUrl(preview.getAttribute("href")) : null;
  }

  function douyinMediaOwner(video) {
    const owners = new Set();
    for (let node = video; node; node = node.parentElement) {
      const id = node.getAttribute("data-aweme-id");
      if (id !== null) {
        if (!/^[1-9]\d*$/.test(id)) return null;
        owners.add("https://www.douyin.com/video/" + id);
      }
      // Both feed previews and the active slider expose video_<aweme_id>.
      for (const name of String(node.className || "").split(/\s+/)) {
        const match = name.match(/^video_([1-9]\d*)$/);
        if (match) owners.add("https://www.douyin.com/video/" + match[1]);
      }
      if (node.tagName === "A" || node.classList.contains("waterfall-videoCardContainer")) {
        const linked = siteVideoUrl(node.getAttribute("href"));
        if (linked) owners.add(linked);
      }
    }
    // A changed modal URL alone cannot prove ownership of an old/preloaded VIDEO.
    return owners.size === 1 ? [...owners][0] : null;
  }

  function validDouyinMediaUrl(raw) {
    if (typeof raw !== "string" || raw.length > 8192 || /\s/.test(raw)) return null;
    const authority = raw.match(/^https:\/\/([^/?#]+)/i);
    if (!authority || authority[1].includes(":")) return null;
    let url;
    try { url = new URL(raw); } catch (_) { return null; }
    if (url.protocol !== "https:" || url.username || url.password || url.port
      || !(url.hostname === "douyinvod.com" || url.hostname.endsWith(".douyinvod.com"))) return null;
    url.hash = "";
    return url.href;
  }

  function cacheDouyinRecords(records, overwrite = true) {
    if (!Array.isArray(records)) return;
    for (const record of records.slice(0, 100)) {
      if (!record || typeof record.id !== "string" || !/^[1-9]\d{0,29}$/.test(record.id)) continue;
      if (!overwrite && douyinRecords.has(record.id)) continue;
      const url = validDouyinMediaUrl(record.url);
      if (!url) continue;
      const formats = [];
      if (Array.isArray(record.formats)) for (const format of record.formats.slice(0, 16)) {
        const source = format && validDouyinMediaUrl(format.url);
        if (!source) continue;
        const item = { url: source };
        for (const field of ["width", "height", "bitrate"]) {
          if (Number.isFinite(format[field]) && format[field] > 0 && format[field] <= 1e12) item[field] = format[field];
        }
        formats.push(item);
      }
      const title = typeof record.title === "string" ? record.title.replace(/\s+/g, " ").trim().slice(0, 200) : "";
      douyinRecords.delete(record.id);
      douyinRecords.set(record.id, { id: record.id, url, title, ...(formats.length ? { formats } : {}) });
      while (douyinRecords.size > 100) douyinRecords.delete(douyinRecords.keys().next().value);
    }
    host.setAttribute("data-clipgo-media-count", String(douyinRecords.size));
  }

  function readDouyinRenderData() {
    if (typeof CLIPGO_DOUYIN === "undefined") return;
    const script = document.getElementById("RENDER_DATA");
    const raw = script && script.textContent;
    if (!raw || raw.length > 2 * 1024 * 1024 || raw === lastDouyinRenderData) return;
    lastDouyinRenderData = raw;
    // Captured responses are newer than the initial page snapshot.
    try { cacheDouyinRecords(CLIPGO_DOUYIN.parse(raw), false); } catch (_) { /* Incomplete page data is optional. */ }
  }

  function douyinRecordFor(videoUrl) {
    const match = videoUrl && videoUrl.match(/^https:\/\/www\.douyin\.com\/video\/([1-9]\d*)$/);
    return match ? douyinRecords.get(match[1]) : null;
  }

  function douyinTitle(videoUrl, fallback) {
    const record = douyinRecordFor(videoUrl);
    return record && record.title && (!fallback || /^(抖音|精选|发现|推荐|Douyin)(\s|[-_|·]|$)/i.test(fallback)) ? record.title : fallback;
  }

  function douyinMediaFor(videoUrl, expectedUrl, scope = document) {
    if (domSite !== "douyin" || !videoUrl) return null;
    const canonical = siteVideoUrl(videoUrl);
    if (!canonical || !/^https:\/\/www\.douyin\.com\/video\/[1-9]\d*$/.test(canonical)) return null;
    if (expectedUrl !== undefined && siteVideoUrl(expectedUrl) !== canonical) return null;
    const modal = new URL(location.href).searchParams.get("modal_id");
    if (modal !== null && siteVideoUrl(location.href) !== canonical) return null;
    const videos = scope.tagName === "VIDEO" ? [scope] : [...scope.querySelectorAll("video")];
    const sources = new Set();
    let hasLoadedOwner = false;
    for (const video of videos) {
      if (!bigEnough(video, 160, 90) || !visibleArea(video)
        || !Number.isFinite(video.duration) || video.duration <= 0
        || douyinMediaOwner(video) !== canonical) continue;
      hasLoadedOwner = true;
      // Pausing a loaded player is fine. Blob/MSE URLs and unrelated hosts cannot
      // be passed to the local helper as downloadable media.
      const source = validDouyinMediaUrl(video.currentSrc);
      if (source) sources.add(source);
    }
    if (!hasLoadedOwner) return null;
    readDouyinRenderData();
    const record = douyinRecordFor(canonical);
    const source = sources.size === 1 ? [...sources][0] : record && record.url;
    if (!source) return null;
    return { url: source, videoUrl: canonical, ...(record && record.formats ? { formats: record.formats } : {}) };
  }

  function domOwnerUrl(el) {
    if (domSite === "xiaohongshu") {
      const modals = visibleNoteModals();
      if (modals.length && !modals.some((modal) => modal.contains(el))) return null;
      const card = el.closest(".note-item");
      if (card) return xhsCardLink(card)?.url || null;
      const modal = el.closest(".note-detail-mask");
      const note = el.closest("#noteContainer");
      const page = siteVideoUrl(location.href);
      if (!modal || !note || note.getAttribute("data-type") !== "video" || !page) return null;
      const id = new URL(page).pathname.split("/").filter(Boolean).pop();
      return modal.getAttribute("note-id") === id ? page : null;
    }
    if (domSite === "douyin") {
      const card = el.closest("[data-aweme-id], .waterfall-videoCardContainer");
      if (card) return douyinCardUrl(el);
      const owner = douyinMediaOwner(el);
      if (owner) return owner;
    }
    const link = el.closest("a[href]");
    const linked = link && siteVideoUrl(link.href);
    if (linked) return linked;
    // A specific watch page owns its standalone player. Cards above take
    // precedence so a related preview cannot silently become the page's video.
    if (el.closest(DOM_MEDIA)) return siteVideoUrl(location.href);
    return null;
  }

  function domTarget(el, x, y) {
    if (domSite === "xiaohongshu") {
      const modals = visibleNoteModals();
      if (modals.length && !modals.some((modal) => modal.contains(el))) return null;
      const card = el.closest(".note-item");
      if (card) {
        if (!card.querySelector(".play-icon, video")) return null;
        const link = xhsCardLink(card);
        if (link && bigEnough(link.el)) return { url: link.url, el: link.el, domBound: true };
        return null;
      }
    }
    if (domSite === "douyin") {
      const card = el.closest(".waterfall-videoCardContainer, [data-aweme-id]");
      const url = card && douyinCardUrl(card);
      if (url && bigEnough(card)) return { url, el: card, domBound: true };
    }
    const anchor = el.closest("a[href]");
    const linked = anchor && siteVideoUrl(anchor.href);
    if (linked && bigEnough(anchor)) return { url: linked, el: anchor, domBound: true };
    for (const node of [el, ...document.elementsFromPoint(x, y)]) {
      const media = node.closest && node.closest(DOM_MEDIA);
      if (!media || !bigEnough(media, 160, 90)) continue;
      const url = domOwnerUrl(media);
      if (url) return { url, el: media, domBound: true };
    }
    return null;
  }

  function currentTitle(el, explicitPage = false) {
    let title = null;
    if (domSite === "xiaohongshu") {
      const modal = el && el.closest(".note-detail-mask");
      const card = el && el.closest(".note-item");
      title = modal && modal.querySelector("#detail-title") || card && card.querySelector(".title");
    }
    const text = title && title.textContent || el && (el.getAttribute("aria-label") || el.getAttribute("title"))
      || explicitPage && document.title || "";
    return String(text).replace(/\s+/g, " ").trim().slice(0, 200);
  }

  function currentVideoInfo() {
    const page = pageVideoUrl();
    if (!domSite) return { url: page, title: page ? currentTitle(null, true) : "" };
    const modals = domSite === "xiaohongshu" ? visibleNoteModals() : [];
    const videos = [...document.querySelectorAll("video")].filter((video) => (
      bigEnough(video, 160, 90) && visibleArea(video) && (!modals.length || modals.some((modal) => modal.contains(video)))
    ));
    if (modals.length && !videos.length) return { url: null, title: "" };
    if (page && !modals.length) return { url: page, title: currentTitle(null, true) };
    const candidates = videos.map((video) => ({ video, url: domOwnerUrl(video) })).filter((item) => item.url);
    const playing = candidates.filter(({ video }) => video.paused === false && !video.ended);
    const pool = playing.length ? playing : candidates;
    const urls = new Set(pool.map((item) => item.url));
    if (urls.size !== 1) return { url: null, title: "" };
    const selected = pool[0];
    return { url: selected.url, title: currentTitle(selected.video, !!page) };
  }

  function resolveCurrentVideo(expectedUrl) {
    const result = currentVideoInfo();
    const media = douyinMediaFor(result.url, expectedUrl);
    return media ? { ...result, title: douyinTitle(result.url, result.title), media } : result;
  }

  function resolvePageVideo(expectedUrl) {
    const empty = { url: null, title: "" };
    if (domSite !== "douyin" || typeof expectedUrl !== "string") return empty;
    const page = new URL(location.href);
    const match = page.pathname.match(/^\/video\/([1-9]\d*)\/?$/);
    if (!match || page.searchParams.has("modal_id")) return empty;
    const expected = siteVideoUrl(expectedUrl);
    const canonical = "https://www.douyin.com/video/" + match[1];
    if (expected !== canonical || siteVideoUrl(location.href) !== canonical) return empty;

    // A dedicated watch page fetched for this exact ID can expose its own SSR
    // metadata before background-tab playback loads. Never borrow a feed record.
    readDouyinRenderData();
    const record = douyinRecordFor(canonical);
    const result = { url: canonical, title: douyinTitle(canonical, currentTitle(null, true)) };
    if (!record) return result;
    return { ...result, media: { url: record.url, videoUrl: canonical, ...(record.formats ? { formats: record.formats } : {}) } };
  }

  function twitterLink(raw) {
    const info = CLIPGO.detect(raw);
    if (!info || info.site !== "twitter" || !info.isVideo) return null;
    const match = info.url.match(/\/status\/(\d+)(\/(?:video|photo)\/\d+)?$/);
    return match ? { url: info.url, statusId: match[1], attachment: !!match[2] } : null;
  }

  function twitterVideoUrl(media) {
    // An attachment link enclosing this player identifies the selected media
    // directly, including quoted videos and multi-attachment timeline posts.
    const enclosingLink = media.closest("a[href]");
    const direct = enclosingLink && twitterLink(enclosingLink.href);
    if (direct && direct.attachment) return direct.url;

    const article = media.closest("article");
    const quote = media.closest(TWITTER_QUOTE);
    const container = quote || article;
    if (container) {
      const links = [...container.querySelectorAll("a[href]")].filter((link) => (
        link.closest("article") === article && link.closest(TWITTER_QUOTE) === quote
      ));
      // The timestamp is the owning post's permalink. Ignore links to posts in
      // the text and links belonging to a different quoted/nested tweet.
      for (const link of links.filter((link) => link.querySelector("time"))) {
        const owner = twitterLink(link.href);
        if (!owner) continue;
        const page = twitterLink(location.href);
        // The open media viewer may retain the article and its bare timestamp.
        // Keep its selection only when it belongs to this same post, not a reply.
        return page && page.attachment && page.statusId === owner.statusId ? page.url : owner.url;
      }
      return null; // A timeline/reply video must never use the page's other post.
    }
    return pageVideoUrl(); // Standalone media viewer on a /status/ URL.
  }

  function twitterTarget(el, x, y) {
    for (const node of [el, ...document.elementsFromPoint(x, y)]) {
      const media = node.closest && node.closest(TWITTER_MEDIA);
      if (!media || !bigEnough(media, 160, 90)) continue;
      const url = twitterVideoUrl(media);
      if (url) return { url, el: media, twitterBound: true };
    }
    return null;
  }

  /** Work out which video (if any) is under the pointer. */
  function findTarget(el, x, y) {
    if (isTwitter) return twitterTarget(el, x, y);
    if (domSite) return domTarget(el, x, y);
    // 1. A thumbnail-sized link to a video (feeds, search, sidebar, grids).
    const a = el.closest && el.closest("a[href]");
    if (a) {
      const info = CLIPGO.detect(a.href);
      if (info && info.isVideo && bigEnough(a)) return { url: info.url, el: a };
    }

    const underPointer = document.elementsFromPoint(x, y);
    if (isBilibili) {
      // Hover previews can sit beside (rather than inside) the thumbnail link.
      const card = el.closest && el.closest(".bili-video-card, .video-card, .video-page-card-small, .small-item");
      if (card) {
        for (const link of card.querySelectorAll("a[href]")) {
          const info = CLIPGO.detect(link.href);
          if (info && info.site === "bilibili" && info.isVideo && bigEnough(link)) {
            return { url: info.url, el: link };
          }
        }
      }
      // The player's controls/danmaku may cover the media entirely. HEVC software
      // playback uses a custom <bwp-video>, so don't require a native <video> hit.
      for (const node of [el, ...underPointer]) {
        const player = node.closest && node.closest(BILI_PLAYER);
        if (player && bigEnough(player, 160, 90)) {
          const url = pageVideoUrl();
          if (url) return { url, el: player, pageBound: true };
        }
      }
    }

    // 2. A playing media element, possibly under overlays.
    const video = underPointer.find((n) => n.tagName === "VIDEO" || (isBilibili && n.tagName === "BWP-VIDEO"));
    if (!video || !bigEnough(video, 160, 90)) return null;

    if (isBilibili) {
      const url = pageVideoUrl();
      return url ? { url, el: video, pageBound: true } : null;
    }

    if (isYouTube) {
      const player = video.closest("#movie_player, .html5-video-player");
      if (player && player.id === "movie_player") {
        const url = pageVideoUrl();
        if (url) return { url, el: player };
      }
      // Inline hover preview on home / search.
      const preview = video.closest("ytd-video-preview, #video-preview");
      const url = preview && videoLinkIn(preview);
      if (url) return { url, el: video };
      return null;
    }

    // Instagram: feed posts are <article>s whose timestamp links to the post.
    const article = video.closest("article");
    if (article) {
      const url = videoLinkIn(article);
      if (url) return { url, el: video };
    }
    const url = pageVideoUrl(); // /reel/…, /p/…, /reels/… viewer
    if (url) return { url, el: video };
    // Otherwise look a few levels up for a link to the post.
    let n = video.parentElement;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const u = videoLinkIn(n);
      if (u) return { url: u, el: video };
    }
    return null;
  }

  // ---------------------------------------------------------------- render

  function taskFor(url) {
    return tasks.find((t) => t.url === url);
  }

  // Watching a video fullscreen: never overlay the quick-download button.
  function inFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement);
  }

  function render() {
    // Bilibili reuses its player while switching parts/videos with pushState.
    // Read the current address instead of retaining the previous part's URL.
    if (current && current.pageBound) {
      const url = pageVideoUrl();
      if (url) current.url = url;
      else current = null;
    }
    // X virtualizes its timeline and can reuse a player's DOM for another post.
    if (current && current.twitterBound) {
      const url = twitterVideoUrl(current.el);
      if (url) current.url = url;
      else current = null;
    }
    if (current && current.domBound) {
      const url = domOwnerUrl(current.el);
      if (url) current.url = url;
      else current = null;
    }
    if (!current || !enabled || inFullscreen()) {
      btn.classList.remove("show");
      return;
    }
    const r = current.el.getBoundingClientRect();
    if (!current.el.isConnected || r.width === 0) {
      // The page swapped the element out (e.g. YouTube's hover preview): look again.
      current = null;
      btn.classList.remove("show");
      relookup();
      return;
    }
    btn.style.left = Math.round(r.left + 8) + "px";
    btn.style.top = Math.round(Math.max(r.top, 0) + 8) + "px";

    btn.classList.remove("busy", "ok", "err");
    if (flash && flash.url === current.url && Date.now() < flash.until) {
      btn.classList.add(flash.cls);
      label.textContent = flash.text;
    } else {
      const t = taskFor(current.url);
      if (t && ACTIVE.has(t.status)) {
        btn.classList.add("busy");
        label.textContent = t.status === "merging" ? CLIPGO.t("content_merging", "合并中…", [])
          : typeof t.percent === "number" && t.status === "running" ? CLIPGO.t("content_percent", "$1%", [t.percent.toFixed(0)])
            : CLIPGO.t("content_preparing", "准备中…", []);
      } else if (t && t.status === "done") {
        btn.classList.add("ok");
        label.textContent = CLIPGO.t("content_downloaded_again", "已下载 · 再下", []);
      } else {
        label.textContent = CLIPGO.t("content_mp4", "MP4", []);
      }
    }
    btn.classList.add("show");
  }

  function scheduleRender() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      render();
    });
  }

  function setCurrent(target) {
    clearTimeout(hideTimer);
    if (target) {
      current = target;
      scheduleRender();
    } else if (current) {
      // Small grace period so the pointer can travel from thumbnail to button.
      hideTimer = setTimeout(() => {
        current = null;
        render();
      }, 250);
    }
  }

  // ---------------------------------------------------------------- events

  let lookupPending = false;
  function relookup() {
    if (!enabled || lookupPending || mouse.x < 0) return;
    lookupPending = true;
    requestAnimationFrame(() => {
      lookupPending = false;
      const el = document.elementFromPoint(mouse.x, mouse.y);
      if (!el) return setCurrent(null);
      if (el === host) return setCurrent(current); // over our own button
      setCurrent(findTarget(el, mouse.x, mouse.y));
    });
  }

  function onPointer(e) {
    mouse = { x: e.clientX, y: e.clientY };
    relookup();
  }

  document.addEventListener("mousemove", onPointer, { passive: true, capture: true });
  document.documentElement.addEventListener("mouseleave", () => {
    mouse = { x: -1, y: -1 };
    setCurrent(null);
  }, { passive: true });
  // Content moves under a still pointer when scrolling (page or inner panes).
  window.addEventListener("scroll", relookup, { passive: true, capture: true });
  for (const name of ["fullscreenchange", "webkitfullscreenchange"]) {
    document.addEventListener(name, () => (inFullscreen() ? render() : relookup()), { passive: true });
  }

  btn.addEventListener("mousedown", (e) => { e.preventDefault(); e.stopPropagation(); });
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    render(); // Refresh a reused player's URL and busy state before downloading.
    if (!current || btn.classList.contains("busy")) return;
    const url = current.url;
    let title = domSite ? currentTitle(current.el, url === siteVideoUrl(location.href)) : "";
    const media = douyinMediaFor(url, url, current.el);
    if (media) title = douyinTitle(url, title);
    flash = { url, text: CLIPGO.t("content_starting", "启动中…", []), cls: "busy", until: Date.now() + 10000 };
    render();
    let r;
    try {
      r = await chrome.runtime.sendMessage({ cmd: "download", url, ...(title ? { title } : {}), ...(media ? { media } : {}) });
    } catch (err) {
      r = { ok: false, error: CLIPGO.t("content_extension_updated", "ClipGo 已更新，请刷新此页面后再试", []) };
    }
    if (r && r.ok) {
      flash = { url, text: CLIPGO.t("content_started", "✓ 已开始", []), cls: "ok", until: Date.now() + 1500 };
      toast(CLIPGO.t("content_download_started", "已开始下载，进度可在 ClipGo 图标里查看", []));
    } else {
      flash = { url, text: CLIPGO.t("content_failed", "失败", []), cls: "err", until: Date.now() + 2500 };
      toast((r && r.error) || CLIPGO.t("content_download_failed", "下载失败", []), 5000, CLIPGO.t("content_error_prefix", "ClipGo：", []));
    }
    render();
    setTimeout(relookup, 1600);
    setTimeout(relookup, 2600);
  });

  // ---------------------------------------------------------------- state

  if (domSite === "douyin") {
    window.addEventListener("message", (event) => {
      if (event.source !== window || event.origin !== location.origin || !event.data
        || event.data.type !== "clipgo:douyin-media") return;
      cacheDouyinRecords(event.data.records);
    });
    window.postMessage({ type: "clipgo:douyin-media-request" }, location.origin);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message && message.cmd === "currentVideo") sendResponse(resolveCurrentVideo(message.url));
    else if (message && message.cmd === "pageVideo") sendResponse(resolvePageVideo(message.url));
  });

  chrome.storage.local.get({ settings: {}, tasks: [] }).then((s) => {
    enabled = { ...CLIPGO.DEFAULT_SETTINGS, ...s.settings }.quickButton;
    tasks = s.tasks;
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.settings) {
      enabled = { ...CLIPGO.DEFAULT_SETTINGS, ...changes.settings.newValue }.quickButton;
      if (!enabled) setCurrent(null);
    }
    if (changes.tasks) {
      const before = new Map(tasks.map((t) => [t.id, t.status]));
      tasks = changes.tasks.newValue || [];
      for (const t of tasks) {
        const was = before.get(t.id);
        if (was && was !== t.status && t.status === "error" && document.visibilityState === "visible") {
          toast(t.error || "", 6000, CLIPGO.t("content_download_failed_prefix", "下载失败：", []));
        }
      }
      if (current) scheduleRender();
    }
  });

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
  // Some SPAs replace <body> children; make sure we stay attached.
  setInterval(() => {
    mount();
    if (current && (current.pageBound || current.twitterBound || current.domBound)) scheduleRender();
  }, 3000);
})();
