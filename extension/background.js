/* global CLIPGO */
importScripts("common.js");

const MAX_TASKS = 30;
const ACTIVE = new Set(["queued", "running", "merging"]);

let tasks = [];          // newest first
let loaded = null;       // Promise resolved once tasks are restored
let port = null;         // long-lived connection to the native host while downloading
let saveTimer = null;

// ------------------------------------------------------------------ state

function load() {
  if (!loaded) {
    loaded = chrome.storage.local.get({ tasks: [] }).then(({ tasks: saved }) => {
      // A service-worker restart means the native port (and its downloads) died.
      tasks = saved.map((t) => (ACTIVE.has(t.status)
        ? { ...t, status: "error", error: "任务被中断（浏览器或扩展重启），请重试" }
        : t));
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
  if (/not found/i.test(msg)) return "未检测到 ClipGo 本地助手，请先运行安装脚本";
  if (/forbidden/i.test(msg)) return "本地助手拒绝了此扩展（扩展 ID 不匹配），请重新运行安装脚本";
  if (/exited|communicating/i.test(msg)) return "本地助手启动失败，请重新运行安装脚本";
  return msg || "与本地助手的连接已断开";
}

function friendlyError(task, raw) {
  const msg = String(raw || "");
  const ig = task.site === "instagram";
  if (/sign in to confirm (you|your age)|not a bot/i.test(msg)) {
    return "YouTube 要求登录验证：请在本浏览器登录 YouTube，并在设置中开启「YouTube 使用登录状态」后重试";
  }
  if (ig && /login|log in|rate.?limit|not available|empty media|cookies/i.test(msg)) {
    return "Instagram 需要登录：请先在本浏览器登录 instagram.com 后重试";
  }
  if (/private video|is private/i.test(msg)) return "这是私密视频，无法下载";
  if (/no video formats|there is no video/i.test(msg)) return "这个链接里没有视频（可能是图片帖子）";
  if (/video unavailable|has been removed/i.test(msg)) return "视频不存在或已被删除";
  if (/members.only|join this channel/i.test(msg)) return "会员专属视频，需要登录有权限的账号";
  if (/unable to download|timed out|connection/i.test(msg)) return "网络错误，请检查网络（或代理）后重试：" + msg;
  return msg;
}

function ensurePort() {
  if (port) return port;
  port = chrome.runtime.connectNative(CLIPGO.HOST);
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener(() => {
    const err = hostErrorText(chrome.runtime.lastError && chrome.runtime.lastError.message);
    port = null;
    for (const t of tasks) {
      if (ACTIVE.has(t.status)) Object.assign(t, { status: "error", error: err });
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
      resolve({ type: "error", message: "本地助手无响应" });
    }, timeoutMs);
    chrome.runtime.sendNativeMessage(CLIPGO.HOST, message, (resp) => {
      if (done) return;
      clearTimeout(timer);
      if (chrome.runtime.lastError) {
        resolve({ type: "error", notInstalled: true, message: hostErrorText(chrome.runtime.lastError.message) });
      } else {
        resolve(resp || { type: "error", message: "本地助手没有返回数据" });
      }
    });
  });
}

function onHostMessage(msg) {
  const t = msg.id && findTask(msg.id);
  if (!t) return;
  switch (msg.type) {
    case "stage":
      patch(t.id, { status: /合并|转封装/.test(msg.stage) ? "merging" : t.status, stage: msg.stage });
      break;
    case "progress":
      patch(t.id, {
        status: "running",
        stage: "下载中",
        percent: msg.percent,
        speed: msg.speed,
        eta: msg.eta,
        title: msg.title || t.title,
      });
      break;
    case "done":
      patch(t.id, { status: "done", percent: 100, files: msg.files || [], note: msg.note || "", finishedAt: Date.now() }, true);
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
    title: "ClipGo 下载完成",
    message: task.note || task.title || task.url,
  });
}

// ------------------------------------------------------------------ actions

async function collectCookies(site, settings) {
  const domains = site === "instagram" ? ["instagram.com"]
    : site === "youtube" && settings.ytCookies ? ["youtube.com"] : [];
  const out = [];
  for (const domain of domains) {
    const list = await chrome.cookies.getAll({ domain });
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

async function startDownload(rawUrl, quality) {
  await load();
  const info = CLIPGO.detect(rawUrl);
  if (!info) return { ok: false, error: "只支持 YouTube 和 Instagram 链接" };
  if (!info.isVideo) return { ok: false, error: "这不是视频页面，请打开具体视频或粘贴视频链接" };

  const settings = await getSettings();
  quality = quality || settings.quality;
  const running = tasks.find((t) => t.url === info.url && ACTIVE.has(t.status));
  if (running) return { ok: false, error: "这个视频正在下载中" };

  const task = {
    id: (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random().toString(16).slice(2),
    url: info.url,
    site: info.site,
    quality,
    status: "queued",
    stage: "启动本地助手…",
    percent: 0,
    title: "",
    createdAt: Date.now(),
  };
  tasks.unshift(task);
  // Keep active tasks, trim old finished ones.
  let finished = 0;
  tasks = tasks.filter((t) => ACTIVE.has(t.status) || ++finished <= MAX_TASKS);
  persist(true);

  try {
    const cookies = await collectCookies(info.site, settings);
    ensurePort().postMessage({ action: "download", id: task.id, url: info.url, quality, cookies });
  } catch (e) {
    patch(task.id, { status: "error", error: hostErrorText(e && e.message) }, true);
  }
  return { ok: true, id: task.id };
}

function cancelTask(id) {
  const t = findTask(id);
  if (!t || !ACTIVE.has(t.status)) return;
  if (port) port.postMessage({ action: "cancel", id });
  else patch(id, { status: "cancelled" }, true);
}

// ------------------------------------------------------------------ wiring

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    await load();
    switch (msg.cmd) {
      case "download":
        return startDownload(msg.url, msg.quality);
      case "retry": {
        const t = findTask(msg.id);
        if (!t) return { ok: false };
        tasks = tasks.filter((x) => x.id !== msg.id);
        return startDownload(t.url, t.quality);
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
        return { ok: false, error: "unknown command" };
    }
  })().then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
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
];

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "clipgo-link",
      title: "用 ClipGo 下载这个视频 (MP4)",
      contexts: ["link"],
      targetUrlPatterns: VIDEO_PATTERNS,
    });
    chrome.contextMenus.create({
      id: "clipgo-page",
      title: "用 ClipGo 下载当前视频 (MP4)",
      contexts: ["page", "video", "frame"],
      documentUrlPatterns: VIDEO_PATTERNS,
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.menuItemId === "clipgo-link" ? info.linkUrl : (info.pageUrl || (tab && tab.url));
  startDownload(url).then((r) => {
    if (!r.ok) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: "ClipGo 无法下载",
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
