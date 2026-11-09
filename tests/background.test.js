const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const extensionDir = path.join(__dirname, "..", "extension");
const clone = (value) => structuredClone(value);
const translations = JSON.parse(readFileSync(path.join(__dirname, "../i18n/background.json"), "utf8"));

function event() {
  const listeners = [];
  return {
    addListener(listener) { listeners.push(listener); },
    removeListener(listener) { const index = listeners.indexOf(listener); if (index >= 0) listeners.splice(index, 1); },
    emit(...args) { return listeners.map((listener) => listener(...args)); },
    listeners,
  };
}

async function background({ settings = {}, cookies = {}, cookieError = null, savedTasks = [],
  nativeReply = { type: "repairedPaths", paths: {} }, currentVideo = null, currentVideoError = null, openTabs = [], helperPage = null, locale = null } = {}) {
  const state = { settings: clone(settings), tasks: clone(savedTasks) };
  const writes = [];
  const cookieQueries = [];
  const cookieFilters = [];
  const ports = [];
  const menus = [];
  const nativeRequests = [];
  const tabRequests = [];
  const createdTabs = [];
  const closedTabs = [];
  const mutedTabs = [];
  const helpers = new Map();
  const notifications = [];
  const timers = new Map();
  let nextId = 0;
  let nextTimer = 0;
  const chrome = {
    ...(locale ? { i18n: { getMessage(key, values) {
      return (translations[key]?.[locale] || "").replace(/\$([1-9]\d*)/g, (match, index) => values[Number(index) - 1] ?? match);
    } } } : {}),
    storage: {
      local: {
        async get(defaults) { return clone({ ...defaults, ...state }); },
        async set(values) {
          writes.push(clone(values));
          Object.assign(state, clone(values));
        },
      },
    },
    action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
    cookies: {
      async getAll(filter) {
        cookieFilters.push(clone(filter));
        const key = filter.domain || filter.url;
        cookieQueries.push(key);
        if (cookieError) throw cookieError;
        return clone(cookies[key] || []);
      },
    },
    runtime: {
      onMessage: event(),
      onInstalled: event(),
      sendNativeMessage(name, message, callback) {
        assert.equal(name, "com.clipgo.host");
        nativeRequests.push(clone(message));
        callback(clone(nativeReply));
      },
      connectNative(name) {
        assert.equal(name, "com.clipgo.host");
        const port = {
          onMessage: event(), onDisconnect: event(), messages: [], disconnected: false,
          postMessage(message) { this.messages.push(clone(message)); },
          disconnect() { this.disconnected = true; },
        };
        ports.push(port);
        return port;
      },
    },
    tabs: {
      onActivated: event(), onDetached: event(), onUpdated: event(),
      async query() { return clone(openTabs); },
      async create(options) {
        if (!helperPage) throw new Error("No browser helper fixture");
        const index = createdTabs.length;
        createdTabs.push(clone(options));
        const page = typeof helperPage === "function" ? await helperPage(options) : helperPage;
        const id = 88 + index;
        helpers.set(id, { page, options });
        return { id, ...options };
      },
      async update(id, options) { mutedTabs.push({ id, ...clone(options) }); },
      async get(id) {
        const helper = helpers.get(id);
        if (!helper) throw new Error("Tab closed");
        return { id, active: !!helper.page.active, url: helper.page.tabUrl ?? helper.page.url ?? helper.options.url,
          ...(helper.page.pendingUrl ? { pendingUrl: helper.page.pendingUrl } : {}) };
      },
      async remove(id) { closedTabs.push(id); helpers.delete(id); },
      async sendMessage(id, message) {
        tabRequests.push({ id, message: clone(message) });
        const helper = helpers.get(id);
        if (helper) return clone(helper.page.response ?? helper.page);
        if (currentVideoError) throw currentVideoError;
        return clone(currentVideo);
      },
    },
    notifications: { onClicked: event(), create(...args) { notifications.push(clone(args)); }, clear() {} },
    contextMenus: {
      onClicked: event(),
      removeAll(callback) { menus.length = 0; callback(); },
      create(menu) { menus.push(clone(menu)); },
    },
  };
  const context = vm.createContext({
    chrome, URL, console,
    crypto: { randomUUID: () => "test-task-" + ++nextId },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    importScripts(filename) {
      vm.runInContext(readFileSync(path.join(extensionDir, filename), "utf8"), context, { filename });
    },
  });
  vm.runInContext(readFileSync(path.join(extensionDir, "background.js"), "utf8"), context, { filename: "background.js" });
  await vm.runInContext("load()", context);
  return {
    state, writes, cookieQueries, cookieFilters, ports, menus, chrome, nativeRequests, tabRequests, notifications, createdTabs, closedTabs,
    mutedTabs,
    common: context.CLIPGO,
    async send(message) {
      return new Promise((resolve) => {
        const asyncReply = chrome.runtime.onMessage.listeners[0](message, {}, (reply) => resolve(clone(reply)));
        assert.equal(asyncReply, true);
      });
    },
    native(message, port = ports.at(-1)) { port.onMessage.emit(message); },
    flush() {
      const pending = [...timers.values()];
      timers.clear();
      for (const callback of pending) callback();
    },
    async clickContext(info, tab) {
      chrome.contextMenus.onClicked.emit(info, tab);
      await new Promise(setImmediate);
    },
  };
}

function cookie(domain, name, value) {
  return { domain: "." + domain, path: "/", name, value, secure: true, hostOnly: false, expirationDate: 2000000000 };
}

async function until(predicate) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (predicate()) return;
    await new Promise(setImmediate);
  }
  assert.fail("Expected asynchronous task state was not reached");
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("Bilibili selected part and browser cookies reach the native host without persisting credentials", async () => {
  const session = cookie("bilibili.com", "SESSDATA", "bili-secret");
  const app = await background({ settings: { quality: "720" }, cookies: { "bilibili.com": [session] } });
  const result = await app.send({ cmd: "download", url: "https://m.bilibili.com/video/BV1bK411W797/?p=02&spm_id_from=tracking" });
  assert.equal(result.ok, true);
  assert.deepEqual(app.cookieQueries, ["bilibili.com"]);
  assert.deepEqual(app.ports[0].messages[0], {
    action: "download", id: result.id, url: "https://www.bilibili.com/video/BV1bK411W797?p=2", quality: "720", cookies: [session],
  });
  assert.equal(app.state.tasks[0].site, "bilibili");
  assert.equal(app.state.tasks[0].url, app.ports[0].messages[0].url);
  assert.equal(app.state.tasks[0].quality, "720");
  assert.equal(app.state.tasks[0].status, "queued");
  assert.equal(JSON.stringify(app.writes).includes("bili-secret"), false);
  assert.equal(Object.hasOwn(app.state.tasks[0], "cookies"), false);
});

test("Bilibili short links use bilibili.com cookies and preserve redirect selection", async () => {
  const session = cookie("bilibili.com", "SESSDATA", "short-link-secret");
  const app = await background({ cookies: { "bilibili.com": [session] } });
  const result = await app.send({ cmd: "download", url: "https://b23.tv/yZZOjOg" });
  assert.equal(result.ok, true);
  assert.deepEqual(app.cookieQueries, ["bilibili.com"]);
  assert.equal(app.ports[0].messages[0].url, "https://b23.tv/yZZOjOg");
  assert.deepEqual(app.ports[0].messages[0].cookies, [session]);
});

test("new platforms pass canonical video URLs and only the relevant browser cookies to the helper", async (t) => {
  const cases = [
    ["xiaohongshu", "https://m.xiaohongshu.com/discovery/item/674051740000000007027a15?xsec_token=abc%2Bdef%3D&xsec_source=pc_share",
      "https://www.xiaohongshu.com/discovery/item/674051740000000007027a15?xsec_token=abc%2Bdef%3D&xsec_source=pc_share", { domain: "xiaohongshu.com" }, "xiaohongshu.com"],
    ["xiaohongshu", "https://xhslink.com/a/AbC123/", "https://xhslink.com/a/AbC123", { domain: "xiaohongshu.com" }, "xiaohongshu.com"],
    ["tiktok", "https://www.tiktok.com/@example/video/7170520270497680683?is_from_webapp=1",
      "https://www.tiktok.com/@example/video/7170520270497680683", { domain: "tiktok.com" }, "tiktok.com"],
    ["tiktok", "https://vm.tiktok.com/ZTR45GpSF/", "https://vm.tiktok.com/ZTR45GpSF", { domain: "tiktok.com" }, "tiktok.com"],
    ["douyin", "https://www.douyin.com/?recommend=1&modal_id=6961737553342991651",
      "https://www.douyin.com/video/6961737553342991651", { domain: "douyin.com" }, "douyin.com"],
    ["douyin", "https://v.douyin.com/AbCd123/", "https://v.douyin.com/AbCd123", { domain: "douyin.com" }, "douyin.com"],
    ["tencent", "https://v.qq.com/x/page/q326831cny0.html?ptag=share",
      "https://v.qq.com/x/page/q326831cny0.html", { url: "https://v.qq.com/" }, "qq.com"],
  ];
  for (const [site, input, canonical, filter, cookieDomain] of cases) {
    await t.test(input, async () => {
      const session = cookie(cookieDomain, "session", site + "-secret");
      const key = filter.domain || filter.url;
      const app = await background({ cookies: { [key]: [session], "qq.com": [cookie("mail.qq.com", "mail", "unrelated-secret")] } });
      const result = await app.send({ cmd: "download", url: input, quality: "720", title: "页面上的中文标题" });
      assert.equal(result.ok, true);
      assert.deepEqual(app.cookieFilters, [filter]);
      assert.deepEqual(app.ports[0].messages[0], {
        action: "download", id: result.id, url: canonical, quality: "720", cookies: [session],
      });
      assert.equal(app.state.tasks[0].site, site);
      assert.equal(app.state.tasks[0].title, "页面上的中文标题");
      assert.equal(JSON.stringify(app.writes).includes("secret"), false);
    });
  }
});

test("loading legacy tasks repairs matching paths and derives readable titles without inventing missing text", async () => {
  const broken = "C:\\Downloads\\ClipGo\\旧乱码\uFFFD [BV1VSxr63ENv].mp4";
  const repaired = "C:\\Downloads\\ClipGo\\正确的中文标题 🎬 [BV1VSxr63ENv].mp4";
  const unresolved = "C:\\Downloads\\ClipGo\\无法找回\uFFFD [missing].mp4";
  const fallbackUrl = "https://www.douyin.com/video/6961737553342991651";
  const app = await background({
    savedTasks: [
      { id: "legacy", site: "bilibili", status: "done", title: "乱码\uFFFD", files: [broken], url: "https://www.bilibili.com/video/BV1VSxr63ENv" },
      { id: "unresolved", site: "douyin", status: "done", title: "坏掉\uFFFD", files: [unresolved], url: fallbackUrl },
      { id: "healthy", site: "tiktok", status: "done", title: "原本正常的标题", files: [], url: "https://www.tiktok.com/@example/video/123" },
      { id: "interrupted", site: "douyin", status: "running", title: "未完成\uFFFD", files: [], url: fallbackUrl },
    ],
    nativeReply: { type: "repairedPaths", paths: { [broken]: repaired } },
  });
  assert.deepEqual(app.nativeRequests, [{ action: "repairPaths", paths: [broken, unresolved] }]);
  assert.deepEqual(app.state.tasks[0].files, [repaired]);
  assert.equal(app.state.tasks[0].title, "正确的中文标题 🎬");
  assert.equal(app.state.tasks[1].title, "");
  assert.equal(app.common.taskTitle(app.state.tasks[1]), fallbackUrl);
  assert.equal(app.state.tasks[2].title, "原本正常的标题");
  assert.equal(app.state.tasks[3].status, "error");
  assert.match(app.state.tasks[3].error, /任务被中断/);
  assert.equal(app.common.taskTitle(app.state.tasks[3]), fallbackUrl);
  await app.send({ cmd: "unknown" });
  assert.equal(app.nativeRequests.length, 1);
});

test("legacy tasks still load and fall back to their URL when the helper cannot repair old paths", async () => {
  const url = "https://v.qq.com/x/page/q326831cny0.html";
  const app = await background({
    savedTasks: [{ id: "legacy", status: "done", title: "旧标题\uFFFD", files: ["C:\\Downloads\\坏路径\uFFFD [id].mp4"], url }],
    nativeReply: { type: "error", message: "未检测到 ClipGo 本地助手" },
  });
  assert.equal(app.state.tasks.length, 1);
  assert.equal(app.common.taskTitle(app.state.tasks[0]), url);
  assert.equal(app.state.tasks[0].status, "done");
});

test("early metadata titles survive skipped downloads and corrupt later progress cannot overwrite them", async () => {
  const app = await background({ settings: { notify: false } });
  const first = await app.send({ cmd: "download", url: "https://www.douyin.com/video/6961737553342991651", title: "页面标题" });
  app.native({ type: "stage", id: first.id, stage: "准备下载…", title: "助手读取的中文标题 🎬" });
  app.flush();
  assert.equal(app.state.tasks[0].title, "助手读取的中文标题 🎬");
  app.native({ type: "done", id: first.id, files: [], note: "文件已存在，已跳过" });
  assert.equal(app.state.tasks[0].title, "助手读取的中文标题 🎬");
  assert.equal(app.state.tasks[0].status, "done");

  const second = await app.send({ cmd: "download", url: "https://www.douyin.com/video/6982497745948921092", title: "有效标题" });
  app.native({ type: "progress", id: second.id, percent: 25, title: "错误\uFFFD" });
  app.flush();
  assert.equal(app.state.tasks[0].title, "有效标题");
  assert.equal(app.state.tasks[0].percent, 25);
  app.native({ type: "done", id: second.id, files: [], title: "最终准确的标题" });
  assert.equal(app.state.tasks[0].title, "最终准确的标题");
});

test("completion can recover a readable title from the saved filename when metadata was unavailable", async () => {
  const app = await background({ settings: { notify: false } });
  const result = await app.send({ cmd: "download", url: "https://v.qq.com/x/page/q326831cny0.html" });
  app.native({ type: "done", id: result.id, title: "乱码\uFFFD", files: ["C:\\Downloads\\腾讯视频的中文标题 [q326831cny0].mp4"] });
  assert.equal(app.state.tasks[0].title, "腾讯视频的中文标题");
});

test("X and legacy Twitter links preserve media index and collect both cookie domains", async (t) => {
  for (const origin of ["https://x.com", "https://mobile.twitter.com"]) {
    await t.test(origin, async () => {
      const auth = cookie("x.com", "auth_token", "x-secret");
      const csrf = cookie("x.com", "ct0", "csrf-secret");
      const legacy = cookie("twitter.com", "auth_token", "legacy-secret");
      const app = await background({ cookies: { "x.com": [auth, csrf], "twitter.com": [legacy] } });
      const result = await app.send({ cmd: "download", url: origin + "/NASA/status/623160978427936768/video/2?s=20", quality: "best" });
      assert.equal(result.ok, true);
      assert.deepEqual(app.cookieQueries, ["x.com", "twitter.com"]);
      assert.deepEqual(app.ports[0].messages[0], {
        action: "download", id: result.id, url: "https://x.com/nasa/status/623160978427936768/video/2", quality: "best", cookies: [auth, csrf, legacy],
      });
      assert.equal(app.state.tasks[0].site, "twitter");
      assert.equal(app.state.tasks[0].quality, "best");
      assert.equal(JSON.stringify(app.writes).includes("secret"), false);
    });
  }
});

test("normalization prevents duplicate active downloads while allowing another part or attachment", async () => {
  const app = await background();
  assert.equal((await app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797?p=2" })).ok, true);
  assert.equal((await app.send({ cmd: "download", url: "https://m.bilibili.com/video/BV1bK411W797/?p=02&share_source=copy" })).ok, false);
  assert.equal((await app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797?p=3" })).ok, true);
  assert.equal((await app.send({ cmd: "download", url: "https://twitter.com/NASA/status/623160978427936768/video/2" })).ok, true);
  assert.equal((await app.send({ cmd: "download", url: "https://x.com/nasa/status/623160978427936768/video/2?s=20" })).ok, false);
  assert.equal((await app.send({ cmd: "download", url: "https://x.com/nasa/status/623160978427936768/video/3" })).ok, true);
  assert.equal(app.state.tasks.length, 4);
  assert.equal(app.ports[0].messages.length, 4);
});

test("home pages, nonvideo pages and external URLs cannot create download jobs", async () => {
  const app = await background();
  for (const url of [
    "https://www.bilibili.com/", "https://space.bilibili.com/123",
    "https://x.com/home", "https://twitter.com/NASA", "https://example.com/video/123",
    "https://x.com.evil.example/NASA/status/123", "https://bilibili.com.evil.example/video/BV1bK411W797",
    "https://www.xiaohongshu.com/explore", "https://www.tiktok.com/foryou", "https://www.douyin.com/",
    "https://v.qq.com/x/cover/7ce5noezvafma27.html", "https://mail.qq.com/",
  ]) {
    const result = await app.send({ cmd: "download", url });
    assert.equal(result.ok, false, url);
    assert.ok(result.error, url);
  }
  assert.deepEqual(app.state.tasks, []);
  assert.deepEqual(app.cookieQueries, []);
  assert.deepEqual(app.ports, []);
});

test("native X login failure produces a useful error and retry preserves media selection and quality", async () => {
  const app = await background();
  const first = await app.send({ cmd: "download", url: "https://twitter.com/i/web/status/623160978427936768/video/2", quality: "720" });
  app.native({ type: "error", id: first.id, message: "HTTP Error 403: Forbidden" });
  assert.equal(app.state.tasks[0].status, "error");
  assert.match(app.state.tasks[0].error, /x\.com/);
  assert.equal(app.state.tasks[0].rawError, "HTTP Error 403: Forbidden");
  assert.equal(app.ports[0].disconnected, true);
  app.state.settings.quality = "best";
  const retry = await app.send({ cmd: "retry", id: first.id });
  assert.equal(retry.ok, true);
  assert.notEqual(retry.id, first.id);
  assert.equal(app.state.tasks.length, 1);
  assert.equal(app.ports[1].messages[0].url, "https://x.com/i/web/status/623160978427936768/video/2");
  assert.equal(app.ports[1].messages[0].quality, "720");
  assert.deepEqual(app.cookieQueries, ["x.com", "twitter.com", "x.com", "twitter.com"]);
});

test("site-specific rate limits and missing X media are displayed on failed tasks", async () => {
  for (const [url, message, expected] of [
    ["https://www.bilibili.com/video/BV1bK411W797", "HTTP Error 412: Precondition Failed", /哔哩哔哩.*稍后重试/],
    ["https://x.com/nasa/status/623160978427936768", "Rate-limit exceeded (429)", /X.*稍后重试/],
    ["https://x.com/nasa/status/623160978427936768", "Sign in to confirm your age", /X.*x\.com/],
    ["https://x.com/nasa/status/623160978427936768", "No video could be found in this tweet", /没有可下载的视频/],
    ["https://www.douyin.com/video/6961737553342991651", "Fresh cookies (not necessarily logged in) are needed", /抖音.*播放后重试/],
    ["https://www.xiaohongshu.com/explore/674051740000000007027a15", "Login or verification required", /小红书.*登录状态/],
    ["https://www.xiaohongshu.com/explore/674051740000000007027a15", "No video formats found", /小红书.*没有可下载的视频/],
    ["https://www.tiktok.com/@example/video/7170520270497680683", "HTTP Error 429", /TikTok.*稍后重试/],
    ["https://v.qq.com/x/page/q326831cny0.html", "This video is DRM protected", /腾讯视频.*受加密保护/],
    ["https://v.qq.com/x/page/q326831cny0.html", "需要登录会员账号", /腾讯视频.*观看权限/],
  ]) {
    const app = await background();
    const result = await app.send({ cmd: "download", url });
    app.native({ type: "error", id: result.id, message });
    assert.equal(app.state.tasks[0].status, "error");
    assert.match(app.state.tasks[0].error, expected);
  }
});

test("a cookie access failure is visible and never starts a native download", async () => {
  const app = await background({ cookieError: new Error("Cookie access denied") });
  await app.send({ cmd: "download", url: "https://x.com/nasa/status/623160978427936768" });
  assert.equal(app.state.tasks[0].status, "error");
  assert.match(app.state.tasks[0].error, /Cookie access denied/);
  assert.deepEqual(app.ports, []);
});

// Match Chrome's scheme://host/path patterns for these registered HTTP(S) URLs.
function matches(pattern, rawUrl) {
  const [, scheme, host, pathname] = pattern.match(/^(\*|https?):\/\/([^/]+)(\/.*)$/);
  const url = new URL(rawUrl);
  const hostMatches = host.startsWith("*.")
    ? url.hostname === host.slice(2) || url.hostname.endsWith("." + host.slice(2))
    : url.hostname === host;
  const pathRegex = new RegExp("^" + pathname.split("*").map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$");
  return (scheme === "*" || url.protocol === scheme + ":") && hostMatches && pathRegex.test(url.pathname + url.search);
}

test("both context menus cover Bilibili videos and X status variants without matching home pages", async () => {
  const app = await background();
  app.chrome.runtime.onInstalled.emit();
  assert.equal(app.menus.length, 2);
  const linkMenu = app.menus.find((menu) => menu.id === "clipgo-link");
  const pageMenu = app.menus.find((menu) => menu.id === "clipgo-page");
  for (const patterns of [linkMenu.targetUrlPatterns, pageMenu.documentUrlPatterns]) {
    for (const url of [
      "https://www.bilibili.com/video/BV1bK411W797?p=2", "https://b23.tv/yZZOjOg",
      "https://x.com/NASA/status/623160978427936768/video/2",
      "https://mobile.twitter.com/NASA/status/623160978427936768",
      "https://x.com/i/web/status/623160978427936768",
    ]) assert.ok(patterns.some((pattern) => matches(pattern, url)), url);
    for (const url of ["https://www.bilibili.com/", "https://x.com/home", "https://twitter.com/NASA"]) {
      assert.equal(patterns.some((pattern) => matches(pattern, url)), false, url);
    }
  }
});

test("page context menus cover feed players while link menus remain scoped to video links", async () => {
  const app = await background();
  app.chrome.runtime.onInstalled.emit();
  const linkMenu = app.menus.find((menu) => menu.id === "clipgo-link");
  const pageMenu = app.menus.find((menu) => menu.id === "clipgo-page");
  for (const patterns of [linkMenu.targetUrlPatterns, pageMenu.documentUrlPatterns]) {
    for (const url of [
      "https://www.xiaohongshu.com/discovery/item/674051740000000007027a15?xsec_token=abc",
      "https://xhslink.com/a/AbC123", "https://www.tiktok.com/@example/video/7170520270497680683",
      "https://vm.tiktok.com/ZTR45GpSF/", "https://www.douyin.com/?modal_id=6961737553342991651",
      "https://www.douyin.com/video/6961737553342991651", "https://v.qq.com/x/page/q326831cny0.html",
      "https://v.qq.com/x/cover/mzc00200p29k31e/s0043cwsgj0.html",
    ]) assert.ok(patterns.some((pattern) => matches(pattern, url)), url);
    assert.equal(patterns.some((pattern) => matches(pattern, "https://v.qq.com/x/cover/mzc00200p29k31e.html")), false);
  }
  for (const url of ["https://www.xiaohongshu.com/explore", "https://www.tiktok.com/foryou", "https://www.douyin.com/"]) {
    assert.equal(linkMenu.targetUrlPatterns.some((pattern) => matches(pattern, url)), false, url);
    assert.ok(pageMenu.documentUrlPatterns.some((pattern) => matches(pattern, url)), url);
  }
});

test("page context download uses the currently visible video and title on a feed", async () => {
  const app = await background({ currentVideo: { url: "https://www.douyin.com/video/6961737553342991651", title: "当前正在看的视频" } });
  await app.clickContext({ menuItemId: "clipgo-page", pageUrl: "https://www.douyin.com/" }, { id: 7, url: "https://www.douyin.com/" });
  assert.deepEqual(app.tabRequests[0], { id: 7, message: { cmd: "currentVideo" } });
  assert.equal(app.ports[0].messages[0].url, "https://www.douyin.com/video/6961737553342991651");
  assert.equal(app.state.tasks[0].title, "当前正在看的视频");
});

test("context downloads preserve the clicked link or use the page URL if no content listener exists", async () => {
  const linkApp = await background({ currentVideo: { url: "https://www.douyin.com/video/999" } });
  await linkApp.clickContext({ menuItemId: "clipgo-link", linkUrl: "https://www.douyin.com/video/6961737553342991651" }, { id: 7 });
  assert.deepEqual(linkApp.tabRequests, [{ id: 7, message: { cmd: "currentVideo", url: "https://www.douyin.com/video/6961737553342991651" } }]);
  assert.equal(linkApp.ports[0].messages[0].url, "https://www.douyin.com/video/6961737553342991651");

  const pageApp = await background({ currentVideoError: new Error("No content listener") });
  await pageApp.clickContext({ menuItemId: "clipgo-page", pageUrl: "https://v.qq.com/x/page/q326831cny0.html" }, { id: 8 });
  assert.equal(pageApp.ports[0].messages[0].url, "https://v.qq.com/x/page/q326831cny0.html");
});

test("Douyin retries read a fresh matching page source without persisting signed media URLs", async () => {
  const url = "https://www.douyin.com/video/7658580633765904561";
  const current = { url, title: "中文视频", media: { videoUrl: url, url: "https://v26-web.douyinvod.com/video/?token=first" } };
  const app = await background({ currentVideo: current, openTabs: [{ id: 42 }] });
  const first = await app.send({ cmd: "download", url });
  assert.equal(app.ports[0].messages[0].media.url, current.media.url);
  assert.equal(app.state.tasks[0].downloadSource, "page");
  assert.equal(JSON.stringify(app.writes).includes("token=first"), false);
  app.native({ type: "error", id: first.id, message: "HTTP Error 403" });
  assert.match(app.state.tasks[0].error, /播放地址已失效/);
  current.media.url = "https://v26-web.douyinvod.com/video/?token=refreshed";
  await app.send({ cmd: "retry", id: first.id });
  assert.equal(app.ports[1].messages[0].media.url, current.media.url);
  assert.deepEqual(app.tabRequests.map(r => r.message.url), [url, url]);
});

test("Douyin rejects mismatched videos and external media URLs", async () => {
  const url = "https://www.douyin.com/video/7658580633765904561";
  for (const media of [
    { videoUrl: url, url: "https://v26-web.douyinvod.com.evil.example/video.mp4" },
    { videoUrl: url, url: "http://v26-web.douyinvod.com/video.mp4" },
    { videoUrl: url, url: "https://user:password@v26-web.douyinvod.com/video.mp4" },
    { videoUrl: "https://www.douyin.com/video/999", url: "https://v26-web.douyinvod.com/video.mp4" },
  ]) {
    const app = await background({ currentVideo: { url: "https://www.douyin.com/video/999", media }, openTabs: [{ id: 42 }] });
    await app.send({ cmd: "download", url, media });
    assert.equal(app.ports[0].messages[0].media, undefined);
  }
});

test("an unopened Douyin link loads a temporary background tab and downloads without playback", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const helperPage = { url, title: "未打开的视频", media: { videoUrl: url, url: "https://v26-web.douyinvod.com/fresh.mp4" } };
  const app = await background({ helperPage });
  await app.send({ cmd: "download", url });
  assert.deepEqual(app.createdTabs, [{ url, active: false }]);
  assert.deepEqual(app.tabRequests, [{ id: 88, message: { cmd: "pageVideo", url } }]);
  assert.deepEqual(app.closedTabs, [88]);
  assert.deepEqual(app.mutedTabs, [{ id: 88, muted: true }]);
  assert.equal(app.ports[0].messages[0].media.url, helperPage.media.url);
  assert.equal(app.ports[0].messages[0].title, helperPage.title);
  assert.deepEqual(app.cookieQueries, []);
  assert.equal(JSON.stringify(app.writes).includes("fresh.mp4"), false);
});

test("the helper tab is left open when the user selects it", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const app = await background({ helperPage: { url, active: true, media: { videoUrl: url, url: "https://v26-web.douyinvod.com/fresh.mp4" } } });
  await app.send({ cmd: "download", url });
  assert.deepEqual(app.closedTabs, []);
  assert.ok(app.ports[0].messages[0].media);
});

test("cancelling a task during page preparation stops the download and closes its helper tab", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const app = await background({ helperPage: { url } });
  const pending = app.send({ cmd: "download", url });
  await until(() => app.tabRequests.length === 1);
  const id = app.state.tasks[0].id;
  await app.send({ cmd: "cancel", id });
  assert.equal(app.state.tasks[0].status, "cancelled");
  app.flush();
  await pending;
  assert.deepEqual(app.ports, []);
  assert.deepEqual(app.cookieQueries, []);
  assert.deepEqual(app.closedTabs, [88]);
  for (const name of ["onActivated", "onDetached", "onUpdated"]) assert.equal(app.chrome.tabs[name].listeners.length, 0);
});

test("cancelling a preparing task never sends an unknown cancellation to another active native job", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const app = await background({ helperPage: { url } });
  const other = await app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797" });
  const native = app.ports[0];
  const pending = app.send({ cmd: "download", url });
  await until(() => app.tabRequests.length === 1);
  const id = app.state.tasks.find((task) => task.url === url).id;
  await app.send({ cmd: "cancel", id });
  app.flush();
  await pending;
  assert.equal(native.disconnected, false);
  assert.deepEqual(native.messages.map((message) => [message.action, message.id]), [["download", other.id]]);
  assert.equal(app.state.tasks.find((task) => task.id === id).status, "cancelled");
  assert.equal(app.state.tasks.find((task) => task.id === other.id).status, "queued");
});

test("cancelling while cookies are pending prevents a late native download", async () => {
  const app = await background();
  const gate = deferred();
  let requested = false;
  app.chrome.cookies.getAll = async () => { requested = true; return gate.promise; };
  const pending = app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797" });
  await until(() => requested);
  await app.send({ cmd: "cancel", id: app.state.tasks[0].id });
  gate.resolve([cookie("bilibili.com", "SESSDATA", "test-cookie")]);
  await pending;
  assert.equal(app.state.tasks[0].status, "cancelled");
  assert.deepEqual(app.ports, []);
});

test("a helper created after cancellation is still cleaned up without polling or downloading", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const gate = deferred();
  const app = await background({ helperPage: () => gate.promise });
  const pending = app.send({ cmd: "download", url });
  await until(() => app.createdTabs.length === 1);
  await app.send({ cmd: "cancel", id: app.state.tasks[0].id });
  gate.resolve({ url });
  await pending;
  assert.deepEqual(app.closedTabs, [88]);
  assert.deepEqual(app.tabRequests, []);
  assert.deepEqual(app.ports, []);
});

test("a native disconnect does not fail another task still preparing its page source", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const page = { url };
  const app = await background({ helperPage: page });
  const other = await app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797" });
  const pending = app.send({ cmd: "download", url });
  await until(() => app.tabRequests.length === 1);
  app.ports[0].onDisconnect.emit();
  assert.equal(app.state.tasks.find((task) => task.id === other.id).status, "error");
  assert.equal(app.state.tasks.find((task) => task.url === url).status, "queued");
  page.media = { videoUrl: url, url: "https://v26-web.douyinvod.com/fresh.mp4" };
  app.flush();
  const result = await pending;
  assert.equal(app.ports.length, 2);
  assert.equal(app.ports[1].messages[0].id, result.id);
  assert.ok(app.ports[1].messages[0].media);
});

test("a timed-out helper navigation is closed using pendingUrl before its first commit", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const app = await background({ helperPage: { tabUrl: "", pendingUrl: url, response: { url } } });
  const pending = app.send({ cmd: "download", url });
  await until(() => app.tabRequests.length === 1);
  for (let attempt = 0; attempt < 24; attempt++) {
    app.flush();
    await new Promise(setImmediate);
  }
  await pending;
  assert.equal(app.tabRequests.length, 24);
  assert.deepEqual(app.closedTabs, [88]);
  assert.equal(app.ports[0].messages[0].media, undefined);
  assert.deepEqual(app.cookieQueries, ["douyin.com"]);
});

test("a helper remains user-owned after activation, navigation, or moving even when it looks unchanged later", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  for (const takeOver of [
    (chrome) => chrome.tabs.onActivated.emit({ tabId: 88, windowId: 1 }),
    (chrome) => { chrome.tabs.onUpdated.emit(88, { url: "https://www.douyin.com/video/999" }); chrome.tabs.onUpdated.emit(88, { url }); },
    (chrome) => chrome.tabs.onDetached.emit(88, { oldWindowId: 1 }),
  ]) {
    const page = { url };
    const app = await background({ helperPage: page });
    const pending = app.send({ cmd: "download", url });
    await until(() => app.tabRequests.length === 1);
    takeOver(app.chrome);
    page.media = { videoUrl: url, url: "https://v26-web.douyinvod.com/fresh.mp4" };
    app.flush();
    await pending;
    assert.deepEqual(app.closedTabs, []);
    assert.ok(app.ports[0].messages[0].media);
    for (const name of ["onActivated", "onDetached", "onUpdated"]) assert.equal(app.chrome.tabs[name].listeners.length, 0);
  }
});

test("a user navigation pending away from the original page is never closed", async () => {
  const url = "https://www.douyin.com/video/7701218238184781675";
  const app = await background({ helperPage: { url, pendingUrl: "https://www.douyin.com/video/999",
    media: { videoUrl: url, url: "https://v26-web.douyinvod.com/fresh.mp4" } } });
  await app.send({ cmd: "download", url });
  assert.deepEqual(app.closedTabs, []);
});

test("two simultaneous preparations clean up independently when only one is cancelled", async () => {
  const urls = ["https://www.douyin.com/video/7701218238184781675", "https://www.douyin.com/video/7658580633765904561"];
  const pages = new Map(urls.map((url) => [url, { url }]));
  const app = await background({ helperPage: ({ url }) => pages.get(url) });
  const first = app.send({ cmd: "download", url: urls[0] });
  const second = app.send({ cmd: "download", url: urls[1] });
  await until(() => app.tabRequests.length === 2);
  const cancelled = app.state.tasks.find((task) => task.url === urls[0]).id;
  await app.send({ cmd: "cancel", id: cancelled });
  pages.get(urls[1]).media = { videoUrl: urls[1], url: "https://v26-web.douyinvod.com/second.mp4" };
  app.flush();
  await Promise.all([first, second]);
  assert.deepEqual(app.closedTabs.sort(), [88, 89]);
  assert.equal(app.ports.length, 1);
  assert.equal(app.ports[0].messages.length, 1);
  assert.equal(app.ports[0].messages[0].url, urls[1]);
  assert.equal(app.state.tasks.find((task) => task.id === cancelled).status, "cancelled");
});

test("Tencent trial-only results never report a successful full download", async () => {
  const app = await background();
  const job = await app.send({ cmd: "download", url: "https://v.qq.com/x/page/l4102ber3ei.html" });
  app.native({ type: "error", id: job.id, message: "CLIPGO_TENCENT_PREVIEW: 209 of 1421 seconds" });
  assert.equal(app.state.tasks[0].status, "error");
  assert.match(app.state.tasks[0].error, /试看片段/);
  assert.deepEqual(app.notifications, []);
});

test("a feed without an identifiable current video reports an error instead of starting a download", async () => {
  const app = await background({ currentVideo: { url: "https://example.com/unsupported", title: "无效内容" } });
  await app.clickContext({ menuItemId: "clipgo-page", pageUrl: "https://www.douyin.com/" }, { id: 9 });
  assert.deepEqual(app.ports, []);
  assert.deepEqual(app.state.tasks, []);
  assert.equal(app.notifications.length, 1);
  assert.match(app.notifications[0][0].message, /不是视频页面/);
});

test("menus, stages, completion notes, and notifications follow all five locales without changing merge state", async () => {
  for (const locale of ["zh_CN", "zh_TW", "en", "ja", "ko"]) {
    const app = await background({ locale });
    app.chrome.runtime.onInstalled.emit();
    assert.equal(app.menus[0].title, translations.bg_menu_link[locale]);
    assert.equal(app.menus[1].title, translations.bg_menu_page[locale]);
    const result = await app.send({ cmd: "download", url: "https://www.bilibili.com/video/BV1bK411W797", title: "原始视频标题" });
    assert.equal(app.state.tasks[0].stage, translations.bg_stage_starting[locale]);
    app.native({ type: "stage", id: result.id, stage: "合并音视频为 MP4…" });
    app.flush();
    assert.equal(app.state.tasks[0].status, "merging");
    assert.equal(app.state.tasks[0].stage, translations.common_stage_merging[locale]);
    app.native({ type: "done", id: result.id, note: "文件已存在，已跳过" });
    await until(() => app.notifications.length === 1);
    assert.equal(app.state.tasks[0].title, "原始视频标题");
    assert.equal(app.state.tasks[0].note, translations.common_note_exists[locale]);
    assert.equal(app.notifications[0][1].title, translations.bg_download_complete[locale]);
    assert.equal(app.notifications[0][1].message, translations.common_note_exists[locale]);
  }
});

test("site errors translate placeholders while preserving the original diagnostic", async () => {
  const app = await background({ locale: "en" });
  const result = await app.send({ cmd: "download", url: "https://www.xiaohongshu.com/explore/674051740000000007027a15" });
  const raw = "HTTP Error 429: Too Many Requests";
  app.native({ type: "error", id: result.id, message: raw });
  assert.equal(app.state.tasks[0].error, translations.bg_site_rate_limit.en.replace("$1", translations.common_site_xiaohongshu.en));
  assert.equal(app.state.tasks[0].rawError, raw);
});

test("one-shot helper responses localize update output and known native errors", async () => {
  const app = await background({ locale: "ja", nativeReply: { type: "updated", ok: true, output: "已更新到 2026.10.26" } });
  const result = await app.send({ cmd: "host", payload: { action: "update" } });
  assert.equal(result.output, translations.common_updated.ja.replace("$1", "2026.10.26"));
  const failed = await background({ locale: "ko", nativeReply: { type: "error", message: "未找到 yt-dlp，请重新运行安装脚本" } });
  assert.equal((await failed.send({ cmd: "host", payload: { action: "ping" } })).message, translations.common_host_missing_engine.ko);
});
