/* global CLIPGO */
const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["queued", "running", "merging"]);
let settings = { ...CLIPGO.DEFAULT_SETTINGS };
let helperOk = false;

const send = (msg) => chrome.runtime.sendMessage(msg);
const host = (payload) => send({ cmd: "host", payload });

// ------------------------------------------------------------------ helper status

async function checkHelper() {
  const el = $("helperStatus");
  el.className = "status";
  el.querySelector("span").textContent = "检测助手…";
  const r = await host({ action: "ping" });
  helperOk = r && r.type === "pong" && !!r.ytdlp;
  if (helperOk) {
    el.classList.add("ok");
    el.querySelector("span").textContent = "助手已就绪";
    el.title = "yt-dlp " + r.ytdlp;
    $("setup").hidden = true;
    $("dir").textContent = r.downloadDir;
    const extras = [r.ffmpeg ? "ffmpeg ✓" : "ffmpeg ✗", r.deno ? "deno ✓" : "deno ✗"];
    $("engine").textContent = "yt-dlp " + r.ytdlp + " · " + extras.join(" · ");
  } else {
    el.classList.add("bad");
    el.querySelector("span").textContent = "未连接助手";
    $("setup").hidden = false;
    $("setupDetail").textContent = (r && r.message) || "";
    $("engine").textContent = "未安装";
  }
  refreshButton();
}

// ------------------------------------------------------------------ form

function refreshButton() {
  const info = CLIPGO.detect($("url").value);
  const badge = $("siteBadge");
  badge.className = "badge" + (info ? " " + info.site : "");
  badge.textContent = info ? CLIPGO.siteName(info.site) : "—";
  $("download").disabled = !(info && info.isVideo && helperOk);
}

function showError(text) {
  $("formError").hidden = !text;
  $("formError").textContent = text || "";
}

async function initForm() {
  for (const q of CLIPGO.QUALITIES) {
    const o = document.createElement("option");
    o.value = q.value;
    o.textContent = q.label;
    $("quality").appendChild(o);
  }
  $("quality").value = settings.quality;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const info = tab && tab.url ? CLIPGO.detect(tab.url) : null;
  if (info && info.isVideo) {
    $("url").value = info.url;
    $("pageHint").textContent = tab.title || "当前页面";
  } else if (info) {
    $("pageHint").textContent = info.site === "instagram"
      ? "在帖子上右键 →「用 ClipGo 下载」，或粘贴链接"
      : "打开具体视频，或粘贴视频链接";
  }
  refreshButton();
}

$("url").addEventListener("input", () => { showError(""); refreshButton(); });
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("download").disabled) $("download").click(); });

$("quality").addEventListener("change", () => saveSettings({ quality: $("quality").value }));

$("download").addEventListener("click", async () => {
  showError("");
  $("download").disabled = true;
  const r = await send({ cmd: "download", url: $("url").value, quality: $("quality").value });
  if (!r || !r.ok) showError((r && r.error) || "启动失败");
  refreshButton();
});

// ------------------------------------------------------------------ tasks

function fmtMeta(t) {
  switch (t.status) {
    case "queued": return [t.stage || "准备中…", ""];
    case "merging": return [t.stage || "合并为 MP4…", ""];
    case "running": {
      const pct = typeof t.percent === "number" ? t.percent.toFixed(1) + "%" : "";
      const right = [t.speed, t.eta && t.eta !== "Unknown" ? "剩余 " + t.eta : ""].filter(Boolean).join(" · ");
      return [pct, right];
    }
    case "done": return [t.note || "✓ 已保存为 MP4", ""];
    case "cancelled": return ["已取消", ""];
    case "error": return ["✕ " + (t.error || "下载失败"), ""];
    default: return ["", ""];
  }
}

function actionBtn(label, onClick) {
  const b = document.createElement("button");
  b.className = "link";
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function renderTasks(tasks) {
  const list = $("taskList");
  list.textContent = "";
  $("empty").hidden = tasks.length > 0;
  $("clear").hidden = !tasks.some((t) => !ACTIVE.has(t.status));

  for (const t of tasks) {
    const li = document.createElement("li");
    li.className = "task is-" + t.status;

    const top = document.createElement("div");
    top.className = "task-top";
    const dot = document.createElement("i");
    dot.className = "task-site " + t.site;
    const title = document.createElement("div");
    title.className = "task-title";
    title.textContent = t.title || t.url;
    title.title = (t.title ? t.title + "\n" : "") + t.url;
    const actions = document.createElement("div");
    actions.className = "task-actions";

    if (ACTIVE.has(t.status)) {
      actions.append(actionBtn("取消", () => send({ cmd: "cancel", id: t.id })));
    } else {
      if (t.status === "done") {
        actions.append(actionBtn("打开位置", () => host({ action: "reveal", path: (t.files && t.files[0]) || "" })));
      }
      if (t.status === "error" || t.status === "cancelled") {
        actions.append(actionBtn("重试", () => send({ cmd: "retry", id: t.id })));
      }
      actions.append(actionBtn("移除", () => send({ cmd: "remove", id: t.id })));
    }
    top.append(dot, title, actions);
    li.append(top);

    if (ACTIVE.has(t.status)) {
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("i");
      if (t.status === "running" && typeof t.percent === "number") {
        fill.style.width = Math.max(2, t.percent) + "%";
      } else {
        bar.classList.add("indeterminate");
      }
      bar.append(fill);
      li.append(bar);
    }

    const [left, right] = fmtMeta(t);
    const meta = document.createElement("div");
    meta.className = "task-meta";
    const l = document.createElement("span");
    l.textContent = left;
    const r = document.createElement("span");
    r.textContent = right;
    meta.append(l, r);
    li.append(meta);
    list.append(li);
  }
}

$("clear").addEventListener("click", () => send({ cmd: "clearFinished" }));

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.tasks) renderTasks(changes.tasks.newValue || []);
});

// ------------------------------------------------------------------ settings

async function saveSettings(partial) {
  settings = { ...settings, ...partial };
  await chrome.storage.local.set({ settings });
}

$("ytCookies").addEventListener("change", (e) => saveSettings({ ytCookies: e.target.checked }));
$("quickButton").addEventListener("change", (e) => saveSettings({ quickButton: e.target.checked }));
$("notify").addEventListener("change", (e) => saveSettings({ notify: e.target.checked }));
$("recheck").addEventListener("click", checkHelper);
$("helperStatus").addEventListener("click", checkHelper);
$("openDir").addEventListener("click", () => host({ action: "openDir" }));
$("chooseDir").addEventListener("click", async () => {
  // The folder dialog may close this popup; the helper saves the choice anyway.
  const r = await host({ action: "chooseDir" });
  if (r && r.downloadDir) $("dir").textContent = r.downloadDir;
});
$("update").addEventListener("click", async () => {
  const b = $("update");
  b.disabled = true;
  b.textContent = "更新中…";
  const r = await host({ action: "update" });
  b.disabled = false;
  b.textContent = "更新 yt-dlp";
  if (r && r.ytdlp) $("engine").textContent = "yt-dlp " + r.ytdlp + (r.ok ? " · 已是最新" : " · 更新失败");
  else $("engine").textContent = (r && r.message) || "更新失败";
});

// ------------------------------------------------------------------ boot

(async () => {
  const stored = await chrome.storage.local.get({ settings: {}, tasks: [] });
  settings = { ...CLIPGO.DEFAULT_SETTINGS, ...stored.settings };
  $("ytCookies").checked = settings.ytCookies;
  $("quickButton").checked = settings.quickButton;
  $("notify").checked = settings.notify;
  renderTasks(stored.tasks);
  await initForm();
  checkHelper();
})();
