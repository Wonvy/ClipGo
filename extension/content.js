/* global CLIPGO */
// In-page quick download button for YouTube / Instagram.
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

  let enabled = true;
  let tasks = [];
  let current = null;        // { url, el }
  let hideTimer = null;
  let mouse = { x: -1, y: -1 };
  let rafPending = false;
  let flash = null;          // { url, text, cls, until }

  // ---------------------------------------------------------------- UI

  const host = document.createElement("clipgo-quick");
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
  btn.title = "用 ClipGo 下载为 MP4";
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
  label.textContent = "MP4";
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

  /** Work out which video (if any) is under the pointer. */
  function findTarget(el, x, y) {
    // 1. A thumbnail-sized link to a video (feeds, search, sidebar, grids).
    const a = el.closest && el.closest("a[href]");
    if (a) {
      const info = CLIPGO.detect(a.href);
      if (info && info.isVideo && bigEnough(a)) return { url: info.url, el: a };
    }

    // 2. A playing <video>, possibly under overlays.
    const video = document.elementsFromPoint(x, y).find((n) => n.tagName === "VIDEO");
    if (!video || !bigEnough(video, 160, 90)) return null;

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

  function render() {
    if (!current || !enabled) {
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
        label.textContent = t.status === "merging" ? "合并中…"
          : typeof t.percent === "number" && t.status === "running" ? t.percent.toFixed(0) + "%" : "准备中…";
      } else if (t && t.status === "done") {
        btn.classList.add("ok");
        label.textContent = "已下载 · 再下";
      } else {
        label.textContent = "MP4";
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

  btn.addEventListener("mousedown", (e) => { e.preventDefault(); e.stopPropagation(); });
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!current || btn.classList.contains("busy")) return;
    const url = current.url;
    flash = { url, text: "启动中…", cls: "busy", until: Date.now() + 10000 };
    render();
    let r;
    try {
      r = await chrome.runtime.sendMessage({ cmd: "download", url });
    } catch (err) {
      r = { ok: false, error: "ClipGo 已更新，请刷新此页面后再试" };
    }
    if (r && r.ok) {
      flash = { url, text: "✓ 已开始", cls: "ok", until: Date.now() + 1500 };
      toast("已开始下载，进度可在 ClipGo 图标里查看");
    } else {
      flash = { url, text: "失败", cls: "err", until: Date.now() + 2500 };
      toast((r && r.error) || "下载失败", 5000, "ClipGo：");
    }
    render();
    setTimeout(relookup, 1600);
    setTimeout(relookup, 2600);
  });

  // ---------------------------------------------------------------- state

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
          toast(t.error || "", 6000, "下载失败：");
        }
      }
      if (current) scheduleRender();
    }
  });

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
  // Some SPAs replace <body> children; make sure we stay attached.
  setInterval(mount, 3000);
})();
