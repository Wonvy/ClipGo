/* global CLIPGO */
importScripts("common.js");

const MAX_TASKS = 30;
const ACTIVE = new Set(["queued", "running", "merging"]);

let tasks = [];          // newest first
let loaded = null;       // Promise resolved once tasks are restored
let port = null;         // long-lived connection to the native host while downloading
let saveTimer = null;
const preparing = new Set(); // Tasks whose source/cookies have not reached the host yet.

// ------------------------------------------------------------------ state

function load() {
  if (!loaded) {
    loaded = chrome.storage.local.get({ tasks: [] }).then(async ({ tasks: saved }) => {
      // A service-worker restart means the native port (and its downloads) died.
      tasks = saved.map((t) => (ACTIVE.has(t.status)
        ? { ...t, status: "error", error: CLIPGO.t("bg_interrupted", "任务被中断（浏览器或扩展重启），请重试") }
        : t));
      const brokenPaths = tasks.flatMap((t) => t.files || []).filter((p) => typeof p === "string" && p.includes("\uFFFD")).slice(0, MAX_TASKS);
      if (brokenPaths.length) {
        const repaired = await hostRequest({ action: "repairPaths", paths: brokenPaths });
        if (repaired.type === "repairedPaths" && repaired.paths) {
          for (const task of tasks) task.files = (task.files || []).map((p) => repaired.paths[p] || p);
        }
      }
      for (const task of tasks) task.title = CLIPGO.readableTitle(task.title) || CLIPGO.titleFromFiles(task.files);
      persist(true);
    });
  }
  return loaded;
}

function persist(now) {
  clearTimeout(saveTimer);
  const write = () => {
    chrome.storage.local.set({ tasks });
    updateBadge();
  };
  if (now) write();
  else saveTimer = setTimeout(write, 300);
}

function findTask(id) {
  return tasks.find((t) => t.id === id);
}

function patch(id, changes, now) {
  const t = findTask(id);
  if (!t) return null;
  Object.assign(t, changes);
  persist(now);
  return t;
}

function updateBadge() {
  const n = tasks.filter((t) => ACTIVE.has(t.status)).length;
  chrome.action.setBadgeBackgroundColor({ color: "#E8364F" });
  chrome.action.setBadgeText({ text: n ? String(n) : "" });
}

async function getSettings() {
  const { settings } = await chrome.storage.local.get({ settings: {} });
  return { ...CLIPGO.DEFAULT_SETTINGS, ...settings };
}

// ------------------------------------------------------------------ native host

function hostErrorText(raw) {
  const msg = String(raw || "");
  if (/not found/i.test(msg)) return CLIPGO.t("bg_host_not_found", "未检测到 ClipGo 本地助手，请先运行安装脚本");
  if (/forbidden/i.test(msg)) return CLIPGO.t("bg_host_forbidden", "本地助手拒绝了此扩展（扩展 ID 不匹配），请重新运行安装脚本");
  if (/exited|communicating/i.test(msg)) return CLIPGO.t("bg_host_start_failed", "本地助手启动失败，请重新运行安装脚本");
  return CLIPGO.localizeMessage(msg) || CLIPGO.t("bg_host_disconnected", "与本地助手的连接已断开");
}

function friendlyError(task, raw) {
  const msg = String(raw || "");
  const ig = task.site === "instagram";
  if (task.site === "douyin" && /fresh cookies|\b403\b|\b410\b/i.test(msg)) {
    return task.downloadSource === "page"
      ? CLIPGO.t("bg_douyin_url_expired", "抖音播放地址已失效，请刷新视频页面、播放后再点重试")
      : CLIPGO.t("bg_douyin_request_rejected", "抖音解析接口拒绝了请求，请打开这条视频并播放后重试；已打开的旧页面请先刷新");
  }
  if (task.site === "tencent" && /CLIPGO_TENCENT_PREVIEW/.test(msg)) {
    return CLIPGO.t("bg_tencent_preview", "腾讯视频只返回了试看片段，未保存为完整视频。请确认本浏览器已登录有观看权限的账号后重试；仍受限时请使用腾讯视频客户端缓存");
  }
  if (task.site === "tencent" && /CLIPGO_TENCENT_UNVERIFIED/.test(msg)) {
    return CLIPGO.t("bg_tencent_unverified", "暂时无法确认腾讯视频是否完整，已停止保存以免只下载到片段，请稍后重试");
  }
  if (task.site === "tencent" && /protected\/encrypted video formats/i.test(msg)) {
    return CLIPGO.t("bg_tencent_protected", "腾讯视频返回了受保护或暂不支持的视频格式，当前无法导出 MP4");
  }
  if (task.site === "youtube" && /sign in to confirm (you|your age)|not a bot/i.test(msg)) {
    return CLIPGO.t("bg_youtube_login", "YouTube 要求登录验证：请在本浏览器登录 YouTube，并在设置中开启「YouTube 使用登录状态」后重试");
  }
  if (ig && /login|log in|rate.?limit|not available|empty media|cookies/i.test(msg)) {
    return CLIPGO.t("bg_instagram_login", "Instagram 需要登录：请先在本浏览器登录 instagram.com 后重试");
  }
  if (task.site === "bilibili") {
    if (/login|log in|sign in|cookies|premium|membership|vip|登录|大会员|付费/i.test(msg)) {
      return CLIPGO.t("bg_bilibili_login", "哔哩哔哩需要登录或相应观看权限：请在本浏览器登录有权限的账号后重试");
    }
    if (/\b412\b|\b429\b|risk control|rate.?limit|风控|请求过于频繁/i.test(msg)) {
      return CLIPGO.t("bg_bilibili_rate_limit", "哔哩哔哩暂时限制了请求，请稍后重试，并确认本浏览器能正常播放该视频");
    }
  }
  if (task.site === "twitter") {
    if (/login|log in|sign in|authentication|authorization|protected|private|nsfw|age.restrict|not authorized|\b401\b|\b403\b/i.test(msg)) {
      return CLIPGO.t("bg_x_login", "X 需要登录或相应观看权限：请先在本浏览器登录 x.com，并确认可以播放该帖子中的视频");
    }
    if (/rate.?limit|\b429\b/i.test(msg)) return CLIPGO.t("bg_x_rate_limit", "X 暂时限制了请求，请稍后重试");
    if (/no video|does not contain|no media/i.test(msg)) return CLIPGO.t("bg_x_no_video", "这条 X 帖子里没有可下载的视频");
  }
  if (["xiaohongshu", "tiktok", "douyin", "tencent"].includes(task.site)) {
    const name = CLIPGO.siteName(task.site);
    if (/\bDRM\b|encrypted|加密/i.test(msg)) return CLIPGO.t("bg_site_protected", "$1的这个视频受加密保护，无法保存为普通 MP4", [name]);
    if (/login|log in|sign in|cookies|authentication|verification|captcha|\b403\b|登录|验证|会员|付费/i.test(msg)) {
      return CLIPGO.t("bg_site_login", "$1需要有效的登录状态或观看权限：请在本浏览器打开并播放该视频，刷新页面后重试", [name]);
    }
    if (/\b429\b|rate.?limit|频繁/i.test(msg)) return CLIPGO.t("bg_site_rate_limit", "$1暂时限制了请求，请稍后重试", [name]);
    if (/no video|no formats|image.only|not a video/i.test(msg)) return CLIPGO.t("bg_site_no_video", "$1的这个链接里没有可下载的视频（可能是图文内容）", [name]);
  }
  if (/private video|is private/i.test(msg)) return CLIPGO.t("bg_private_video", "这是私密视频，无法下载");
  if (/no video formats|there is no video/i.test(msg)) return CLIPGO.t("bg_no_video", "这个链接里没有视频（可能是图片帖子）");
  if (/video unavailable|has been removed/i.test(msg)) return CLIPGO.t("bg_video_unavailable", "视频不存在或已被删除");
  if (/members.only|join this channel/i.test(msg)) return CLIPGO.t("bg_members_only", "会员专属视频，需要登录有权限的账号");
  if (/unable to download|timed out|connection/i.test(msg)) return CLIPGO.t("bg_network_error", "网络错误，请检查网络（或代理）后重试：$1", [msg]);
  return CLIPGO.localizeMessage(msg);
}

function ensurePort() {
  if (port) return port;
  port = chrome.runtime.connectNative(CLIPGO.HOST);
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener(() => {
    const err = hostErrorText(chrome.runtime.lastError && chrome.runtime.lastError.message);
    port = null;
    for (const t of tasks) {
      if (ACTIVE.has(t.status) && !preparing.has(t.id)) Object.assign(t, { status: "error", error: err });
    }
    persist(true);
  });
  return port;
}

function releasePortIfIdle() {
  if (port && !tasks.some((t) => ACTIVE.has(t.status))) {
    port.disconnect();
    port = null;
  }
}

/** One-shot request to a fresh host process (ping, reveal, chooseDir…). */
function hostRequest(message, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => {
      done = true;
      resolve({ type: "error", message: CLIPGO.t("bg_host_timeout", "本地助手无响应") });
    }, timeoutMs);
    chrome.runtime.sendNativeMessage(CLIPGO.HOST, message, (resp) => {
      if (done) return;
      clearTimeout(timer);
      if (chrome.runtime.lastError) {
        resolve({ type: "error", notInstalled: true, message: hostErrorText(chrome.runtime.lastError.message) });
      } else {
        if (resp) {
          for (const key of ["message", "output"]) if (typeof resp[key] === "string") resp[key] = CLIPGO.localizeMessage(resp[key]);
        }
        resolve(resp || { type: "error", message: CLIPGO.t("bg_host_empty_response", "本地助手没有返回数据") });
      }
    });
  });
}

function onHostMessage(msg) {
  const t = msg.id && findTask(msg.id);
  if (!t) return;
  switch (msg.type) {
    case "stage":
      patch(t.id, { status: /合并|转封装/.test(msg.stage) ? "merging" : t.status, stage: CLIPGO.localizeMessage(msg.stage), title: CLIPGO.readableTitle(msg.title) || t.title });
      break;
    case "progress":
      patch(t.id, {
        status: "running",
        stage: CLIPGO.t("bg_stage_downloading", "下载中"),
        percent: msg.percent,
        speed: msg.speed,
        eta: msg.eta,
        title: CLIPGO.readableTitle(msg.title) || t.title,
      });
      break;
    case "done":
      patch(t.id, { status: "done", percent: 100, files: msg.files || [], title: CLIPGO.readableTitle(msg.title) || CLIPGO.readableTitle(t.title) || CLIPGO.titleFromFiles(msg.files), note: CLIPGO.localizeMessage(msg.note || ""), finishedAt: Date.now() }, true);
      notifyDone(t);
      break;
    case "cancelled":
      patch(t.id, { status: "cancelled" }, true);
      break;
    case "error":
      patch(t.id, { status: "error", error: friendlyError(t, msg.message), rawError: msg.message }, true);
      break;
    default:
      return;
  }
  if (!ACTIVE.has(t.status)) releasePortIfIdle();
}

async function notifyDone(task) {
  const settings = await getSettings();
  if (!settings.notify) return;
  chrome.notifications.create("done:" + task.id, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: CLIPGO.t("bg_download_complete", "ClipGo 下载完成"),
    message: task.note || CLIPGO.taskTitle(task),
  });
}

// ------------------------------------------------------------------ actions

async function collectCookies(site, settings) {
  const domains = site === "instagram" ? ["instagram.com"]
    : site === "bilibili" ? ["bilibili.com"]
    : site === "twitter" ? ["x.com", "twitter.com"]
    : site === "xiaohongshu" ? ["xiaohongshu.com"]
    : site === "tiktok" ? ["tiktok.com"]
    : site === "douyin" ? ["douyin.com"]
    : site === "youtube" && settings.ytCookies ? ["youtube.com"] : [];
  const out = [];
  const filters = site === "tencent" ? [{ url: "https://v.qq.com/" }] : domains.map((domain) => ({ domain }));
  for (const filter of filters) {
    const list = await chrome.cookies.getAll(filter);
    for (const c of list) {
      out.push({
        domain: c.domain,
        hostOnly: c.hostOnly,
        path: c.path,
        secure: c.secure,
        expirationDate: c.expirationDate || 0,
        name: c.name,
        value: c.value,
      });
    }
  }
  return out;
}

function douyinMedia(info, media) {
  if (info.site !== "douyin" || !media || media.videoUrl !== info.url || !/^https:\/\/www\.douyin\.com\/video\/\d+$/.test(info.url)) return null;
  const valid = (raw) => {
    try {
      if (typeof raw !== "string" || /\s/.test(raw) || !/^https:\/\/[^/?#:@]+(?:[/?#]|$)/i.test(raw)) return null;
      const u = new URL(raw);
      return u.protocol === "https:" && !u.username && !u.password && !u.port &&
        (u.hostname === "douyinvod.com" || u.hostname.endsWith(".douyinvod.com")) && u.href.length < 16000 ? u.href : null;
    } catch (_) { return null; }
  };
  const url = valid(media.url);
  if (!url) return null;
  const formats = (Array.isArray(media.formats) ? media.formats : []).slice(0, 16).flatMap((f) => {
    const source = f && valid(f.url);
    if (!source) return [];
    const number = (v, max) => Number.isFinite(v) && v > 0 && v <= max ? v : 0;
    return [{ url: source, width: number(f.width, 20000), height: number(f.height, 20000), bitrate: number(f.bitrate, 1000000000) }];
  });
  return { url, videoUrl: info.url, ...(formats.length ? { formats } : {}) };
}

async function findDouyinMedia(info, provided, tabId, isCancelled = () => false) {
  const direct = douyinMedia(info, provided);
  if (isCancelled() || direct || info.site !== "douyin") return { media: direct };
  try {
    const tabs = tabId != null ? [{ id: tabId }] : await chrome.tabs.query({ url: ["*://*.douyin.com/*"] });
    for (const tab of tabs.slice(0, 12)) {
      if (isCancelled()) return { media: null };
      try {
        const current = await chrome.tabs.sendMessage(tab.id, { cmd: "currentVideo", url: info.url });
        if (current && current.url === info.url) {
          const media = douyinMedia(info, current.media);
          if (media) return { media, title: CLIPGO.readableTitle(current.title) };
        }
      } catch (_) { /* A pre-update tab may not have the content script yet. */ }
    }
  } catch (_) { /* Link-only downloads can still use the standard extractor. */ }
  // A retry may refer to an older video, while the feed has moved on. Open the
  // exact public watch page normally so its own loaded metadata supplies a fresh
  // signed source. No private API requests or guessed CDN/video associations.
  if (/^https:\/\/www\.douyin\.com\/video\/[1-9]\d*$/.test(info.url)) {
    if (isCancelled()) return { media: null };
    let created = null;
    // Keep a user-owned tab even if it becomes inactive again before cleanup.
    // Register before create() resolves so early activation is remembered.
    const activated = new Set();
    const detached = new Set();
    const navigated = new Set();
    const onActivated = ({ tabId }) => activated.add(tabId);
    const onDetached = (id) => detached.add(id);
    const onUpdated = (id, changes) => {
      if (changes.url && (changes.url !== "about:blank" || created?.id === id)
          && CLIPGO.detect(changes.url)?.url !== info.url) navigated.add(id);
    };
    chrome.tabs.onActivated.addListener(onActivated);
    chrome.tabs.onDetached.addListener(onDetached);
    chrome.tabs.onUpdated.addListener(onUpdated);
    try {
      created = await chrome.tabs.create({ url: info.url, active: false });
      if (isCancelled()) return { media: null };
      await chrome.tabs.update(created.id, { muted: true });
      for (let attempt = 0; attempt < 24; attempt++) {
        if (isCancelled()) return { media: null };
        try {
          const current = await chrome.tabs.sendMessage(created.id, { cmd: "pageVideo", url: info.url });
          if (current && current.url === info.url) {
            const media = douyinMedia(info, current.media);
            if (media) return { media, title: CLIPGO.readableTitle(current.title) };
          }
        } catch (_) { /* Wait for this page's content script and initial data. */ }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    } catch (_) { /* A failed page load can still report the extractor's error. */ }
    finally {
      if (created) {
        try {
          const tab = await chrome.tabs.get(created.id);
          if (!tab.active && !activated.has(tab.id) && !detached.has(tab.id) && !navigated.has(tab.id)
              && CLIPGO.detect(tab.pendingUrl || tab.url)?.url === info.url) await chrome.tabs.remove(created.id);
        } catch (_) { /* It may have been closed or taken over by the user. */ }
      }
      chrome.tabs.onActivated.removeListener(onActivated);
      chrome.tabs.onDetached.removeListener(onDetached);
      chrome.tabs.onUpdated.removeListener(onUpdated);
    }
  }
  return { media: null };
}

async function startDownload(rawUrl, quality, title = "", media = null, tabId = null) {
  await load();
  const info = CLIPGO.detect(rawUrl);
  if (!info) return { ok: false, error: CLIPGO.t("bg_supported_sites", "支持 YouTube、Instagram、B站、X、小红书、TikTok、抖音和腾讯视频链接") };
  if (!info.isVideo) return { ok: false, error: CLIPGO.t("bg_not_video_page", "这不是视频页面，请打开具体视频或粘贴视频链接") };

  const settings = await getSettings();
  quality = quality || settings.quality;
  const running = tasks.find((t) => t.url === info.url && ACTIVE.has(t.status));
  if (running) return { ok: false, error: CLIPGO.t("bg_already_downloading", "这个视频正在下载中") };

  const task = {
    id: (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random().toString(16).slice(2),
    url: info.url,
    site: info.site,
    quality,
    status: "queued",
    stage: CLIPGO.t("bg_stage_starting", "启动本地助手…"),
    percent: 0,
    title: CLIPGO.readableTitle(title),
    createdAt: Date.now(),
  };
  tasks.unshift(task);
  preparing.add(task.id);
  // Keep active tasks, trim old finished ones.
  let finished = 0;
  tasks = tasks.filter((t) => ACTIVE.has(t.status) || ++finished <= MAX_TASKS);
  persist(true);

  const isCancelled = () => !ACTIVE.has(findTask(task.id)?.status);
  try {
    if (info.site === "douyin") patch(task.id, { stage: CLIPGO.t("bg_stage_douyin_page", "读取抖音视频页面…") });
    const source = await findDouyinMedia(info, media, tabId, isCancelled);
    if (isCancelled()) return { ok: true, id: task.id };
    if (source.media) patch(task.id, { downloadSource: "page", title: source.title || task.title, stage: CLIPGO.t("bg_stage_page_video", "读取网页视频…") }, true);
    const cookies = source.media ? [] : await collectCookies(info.site, settings);
    if (isCancelled()) return { ok: true, id: task.id };
    ensurePort().postMessage({ action: "download", id: task.id, url: info.url, quality, cookies,
      ...(source.media ? { media: source.media, title: task.title } : {}) });
  } catch (e) {
    if (!isCancelled()) patch(task.id, { status: "error", error: hostErrorText(e && e.message) }, true);
  } finally {
    preparing.delete(task.id);
  }
  return { ok: true, id: task.id };
}

function cancelTask(id) {
  const t = findTask(id);
  if (!t || !ACTIVE.has(t.status)) return;
  if (preparing.has(id)) {
    patch(id, { status: "cancelled" }, true);
    releasePortIfIdle();
  } else if (port) port.postMessage({ action: "cancel", id });
  else patch(id, { status: "cancelled" }, true);
}

// ------------------------------------------------------------------ wiring

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    await load();
    switch (msg.cmd) {
      case "download":
        return startDownload(msg.url, msg.quality, msg.title, msg.media, _sender.tab && _sender.tab.id);
      case "retry": {
        const t = findTask(msg.id);
        if (!t) return { ok: false };
        tasks = tasks.filter((x) => x.id !== msg.id);
        return startDownload(t.url, t.quality, t.title);
      }
      case "cancel":
        cancelTask(msg.id);
        return { ok: true };
      case "remove":
        tasks = tasks.filter((t) => t.id !== msg.id || ACTIVE.has(t.status));
        persist(true);
        return { ok: true };
      case "clearFinished":
        tasks = tasks.filter((t) => ACTIVE.has(t.status));
        persist(true);
        return { ok: true };
      case "host":
        // ping / reveal / openDir / chooseDir / update
        return hostRequest(msg.payload, msg.payload.action === "update" ? 180000 : msg.payload.action === "chooseDir" ? 600000 : 20000);
      default:
        return { ok: false, error: CLIPGO.t("bg_unknown_command", "未知操作") };
    }
  })().then(sendResponse, (e) => sendResponse({ ok: false, error: CLIPGO.t("bg_unexpected_error", "发生错误：$1", [String(e)]) }));
  return true; // async response
});

const VIDEO_PATTERNS = [
  "*://*.youtube.com/watch*",
  "*://*.youtube.com/shorts/*",
  "*://*.youtube.com/live/*",
  "*://youtu.be/*",
  "*://*.instagram.com/p/*",
  "*://*.instagram.com/reel/*",
  "*://*.instagram.com/reels/*",
  "*://*.instagram.com/tv/*",
  "*://*.instagram.com/*/p/*",
  "*://*.instagram.com/*/reel/*",
  "*://*.instagram.com/stories/*",
  "*://*.bilibili.com/video/*",
  "*://b23.tv/*",
  "*://*.x.com/*/status/*",
  "*://*.twitter.com/*/status/*",
  "*://*.xiaohongshu.com/explore/*",
  "*://*.xiaohongshu.com/discovery/item/*",
  "*://xhslink.com/*",
  "*://www.xhslink.com/*",
  "*://*.tiktok.com/*/video/*",
  "*://*.tiktok.com/embed/*",
  "*://*.tiktok.com/t/*",
  "*://vm.tiktok.com/*",
  "*://vt.tiktok.com/*",
  "*://*.douyin.com/video/*",
  "*://*.douyin.com/*modal_id=*",
  "*://v.douyin.com/*",
  "*://v.qq.com/x/page/*",
  "*://v.qq.com/x/cover/*/*",
];

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "clipgo-link",
      title: CLIPGO.t("bg_menu_link", "用 ClipGo 下载这个视频 (MP4)"),
      contexts: ["link"],
      targetUrlPatterns: VIDEO_PATTERNS,
    });
    chrome.contextMenus.create({
      id: "clipgo-page",
      title: CLIPGO.t("bg_menu_page", "用 ClipGo 下载当前视频 (MP4)"),
      contexts: ["page", "video", "frame"],
      documentUrlPatterns: [...VIDEO_PATTERNS, "*://*.douyin.com/*", "*://*.tiktok.com/*", "*://*.xiaohongshu.com/*"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  (async () => {
    let url = info.menuItemId === "clipgo-link" ? info.linkUrl : (info.pageUrl || (tab && tab.url));
    let title = "";
    let media = null;
    if (info.menuItemId !== "clipgo-link" && tab && tab.id != null) {
      try {
        const current = await chrome.tabs.sendMessage(tab.id, { cmd: "currentVideo" });
        if (current && current.url && CLIPGO.detect(current.url)?.isVideo) { url = current.url; title = current.title || ""; media = current.media; }
      } catch (_) { /* Pages opened before reloading the extension have no listener. */ }
    }
    return startDownload(url, undefined, title, media, tab && tab.id);
  })().then((r) => {
    if (!r.ok) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: CLIPGO.t("bg_download_failed", "ClipGo 无法下载"),
        message: r.error,
      });
    }
  });
});

chrome.notifications.onClicked.addListener((nid) => {
  if (!nid.startsWith("done:")) return;
  load().then(() => {
    const t = findTask(nid.slice(5));
    hostRequest({ action: "reveal", path: (t && t.files && t.files[0]) || "" });
    chrome.notifications.clear(nid);
  });
});

load();
