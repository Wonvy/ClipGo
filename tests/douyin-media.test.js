const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const mediaSource = fs.readFileSync(path.join(__dirname, "../extension/douyin-media.js"), "utf8");
const pageSource = fs.readFileSync(path.join(__dirname, "../extension/douyin-page.js"), "utf8");
const context = vm.createContext({ URL });
vm.runInContext(mediaSource, context);
const parse = (data) => JSON.parse(JSON.stringify(context.CLIPGO_DOUYIN.parse(data)));
const ID = "7658580633765904561";
const ORIGIN = "https://www.douyin.com";
const API = ORIGIN + "/aweme/v1/web/aweme/detail/?aweme_id=" + ID;
const URL_A = "https://v26-web.douyinvod.com/path/video.mp4?signature=a%2Bb&mime_type=video_mp4";
const URL_B = "https://v95-zjwz2tc-web.douyinvod.com/backup/video.mp4?signature=c";
const note = (id = ID, url = URL_A) => ({ awemeId: id, desc: "中文标题 🎬", video: { playAddr: [{ src: url }] } });

test("Parses encoded RENDER_DATA and prefers the original H264 play address", () => {
  const detail = note();
  detail.video.playAddr.push({ src: URL_B });
  detail.video.playAddrH265 = [{ src: "https://v26-web.douyinvod.com/hevc.mp4" }];
  detail.video.bitRateList = [
    { width: 720, height: 1280, bitRate: 1000000, isH265: 0, playAddr: [{ src: URL_B }], playerAccessKey: "" },
    { width: 1080, height: 1920, bitRate: 2559724, isH265: 0, playAddr: [{ src: URL_A }], playerAccessKey: "" },
    { width: 2160, height: 3840, bitRate: 4000000, isH265: 1, playAddr: detail.video.playAddrH265 },
  ];
  const data = { app: { videoDetail: detail } };
  const [result] = parse(encodeURIComponent(JSON.stringify(data)));
  assert.equal(result.id, ID);
  assert.equal(result.title, "中文标题 🎬");
  assert.equal(result.url, URL_A);
  assert.deepEqual(result.formats, [
    { url: URL_A, width: 1080, height: 1920, bitrate: 2559724 },
    { url: URL_B, width: 720, height: 1280, bitrate: 1000000 },
  ]);
});

test("Parses snake-case API responses and URL lists", () => {
  const data = { aweme_detail: {
    aweme_id: ID, title: "蛇形字段", video: {
      play_addr: { url_list: [URL_A, URL_B] },
      bit_rate: [{ is_h265: 0, bit_rate: 123456, width: 640, height: 360, play_addr: { url_list: [URL_A] } }],
    },
  } };
  assert.deepEqual(parse(JSON.stringify(data)), [{ id: ID, title: "蛇形字段", url: URL_A,
    formats: [{ url: URL_A, width: 640, height: 360, bitrate: 123456 }] }]);
});

test("Only plain HTTPS douyinvod hosts with no credentials or explicit ports are accepted", () => {
  for (const url of [
    "http://v26-web.douyinvod.com/video.mp4", "https://v26-web.douyinvod.com:443/video.mp4",
    "https://v26-web.douyinvod.com:8443/video.mp4", "https://user:password@v26-web.douyinvod.com/video.mp4",
    "https://v26-web.douyinvod.com.evil.example/video.mp4", "https://notdouyinvod.com/video.mp4",
    "https://evil.example/douyinvod.com/video.mp4", "javascript:alert(1)", "blob:https://www.douyin.com/id",
    "//v26-web.douyinvod.com/video.mp4", "https://v26-web.douyinvod.com/video file.mp4",
  ]) assert.deepEqual(parse(note(ID, url)), [], url);
  assert.equal(parse(note(ID, "https://douyinvod.com/video.mp4"))[0].url, "https://douyinvod.com/video.mp4");
});

test("Skips protected videos, formats and address objects without returning protection metadata", () => {
  for (const field of ["playerAccessKey", "player_access_key", "encrypted", "isEncrypted", "is_encrypted", "drmInfo", "encrypt_key"]) {
    const data = note();
    data.video[field] = "private-value";
    assert.deepEqual(parse(data), [], field);
  }
  const data = note();
  data.video.playAddr = [{ src: URL_A, encrypted: false }];
  data.video.bitRateList = [{ isH265: 0, playerAccessKey: "private-value", playAddr: [{ src: URL_B }] }];
  assert.deepEqual(parse(data), []);
  assert.deepEqual(parse({ ...note(), encrypted: true }), []);
});

test("H265-only and unspecified bitrate codecs do not become fallback formats", () => {
  assert.deepEqual(parse({ awemeId: ID, video: { playAddrH265: [{ src: URL_A }] } }), []);
  assert.deepEqual(parse({ awemeId: ID, video: { isH265: 1, playAddr: [{ src: URL_A }] } }), []);
  assert.deepEqual(parse({ awemeId: ID, video: { bitRateList: [{ playAddr: [{ src: URL_A }] }] } }), []);
  const [fallback] = parse({ awemeId: ID, video: { bitRateList: [{ isH265: 0, playAddr: [{ src: URL_B }] }] } });
  assert.equal(fallback.url, URL_B);
});

test("Preserves large string IDs and rejects already rounded numeric IDs", () => {
  assert.equal(parse(note())[0].id, ID);
  assert.deepEqual(parse(note(Number(ID))), []);
  assert.deepEqual(parse(note("0")), []);
  assert.deepEqual(parse(note("not-an-id")), []);
  assert.equal(parse(note(123))[0].id, "123");
});

test("Returns bounded deduplicated records and at most sixteen formats", () => {
  const feed = Array.from({ length: 150 }, (_, i) => note(String(i + 1)));
  assert.equal(parse({ feed }).length, 100);
  assert.equal(parse({ feed: [note(), note()] }).length, 1);
  const data = note();
  data.video.bitRateList = Array.from({ length: 50 }, (_, i) => ({
    isH265: 0, width: 100 + i, height: 200 + i, bitRate: i + 1,
    playAddr: [{ src: "https://v26-web.douyinvod.com/format-" + i + ".mp4" }],
  }));
  const [result] = parse(data);
  assert.equal(result.formats.length, 16);
  assert.ok(result.formats.some((format) => format.url === URL_A));
  assert.equal(result.formats[0].width, 149);
});

test("Depth, node and input limits handle cyclic and oversized payloads", () => {
  const cyclic = { detail: note() };
  cyclic.self = cyclic;
  assert.equal(parse(cyclic).length, 1);
  let deep = note();
  for (let i = 0; i < 20; i++) deep = { nested: deep };
  assert.deepEqual(parse(deep), []);
  const wide = Object.fromEntries(Array.from({ length: 10001 }, (_, i) => ["key" + i, null]));
  wide.detail = note();
  assert.deepEqual(parse(wide), []);
  assert.deepEqual(parse(" ".repeat(2 * 1024 * 1024 + 1)), []);
  assert.deepEqual(parse("%invalid-encoded-json"), []);
  assert.deepEqual(parse(null), []);
});

test("Output contains only playback records, preserves input and bounds the title", () => {
  const data = note();
  data.cookie = "not-playback-data";
  data.author = { privateProfile: "ignore" };
  data.desc = "  第一行\n第二行 🎥 " + "字".repeat(400);
  const before = JSON.stringify(data);
  const [result] = parse(data);
  assert.deepEqual(Object.keys(result), ["id", "title", "url", "formats"]);
  assert.deepEqual(Object.keys(result.formats[0]), ["url", "width", "height", "bitrate"]);
  assert.ok(result.title.startsWith("第一行 第二行 🎥"));
  assert.equal(result.title.length, 300);
  assert.equal(JSON.stringify(data), before);
  assert.ok(!JSON.stringify(result).includes("not-playback-data"));
});

function bridge() {
  const posted = [];
  const handlers = {};
  const fetchCalls = [];
  const pending = [];
  class XHR {
    constructor() { this.events = {}; this.responseType = ""; this.headers = { "content-type": "application/json" }; }
    open(...args) { this.openArgs = args; return "opened"; }
    send(...args) { this.sendArgs = args; return "sent"; }
    addEventListener(type, callback) { this.events[type] = callback; }
    getResponseHeader(name) { return this.headers[name] || null; }
    respond(data, url = API) {
      this.responseURL = url;
      this.response = data;
      this.responseText = JSON.stringify(data);
      this.events.load?.();
    }
  }
  const window = {
    fetch(...args) { fetchCalls.push({ receiver: this, args }); return pending.shift(); },
    XMLHttpRequest: XHR,
    postMessage(message, target) { posted.push({ message: JSON.parse(JSON.stringify(message)), target }); },
    addEventListener(type, callback) { handlers[type] = callback; },
  };
  const ctx = vm.createContext({ window, location: new URL(ORIGIN + "/"), URL, TextDecoder, Promise });
  vm.runInContext(mediaSource, ctx);
  vm.runInContext(pageSource, ctx);
  return { window, posted, handlers, fetchCalls, pending, ctx };
}

function response(data, url = API, type = "application/json") {
  const result = new Response(JSON.stringify(data), { headers: { "content-type": type } });
  Object.defineProperty(result, "url", { value: url });
  return result;
}
async function drain() { for (let i = 0; i < 5; i++) await new Promise(setImmediate); }

test("Fetch bridge observes a clone, preserves the original promise/body and makes no requests", async () => {
  const b = bridge();
  assert.equal(b.fetchCalls.length, 0);
  const original = response({ aweme_detail: note() });
  const promise = Promise.resolve(original);
  b.pending.push(promise);
  assert.equal(b.window.fetch(API, { method: "GET" }), promise);
  const received = await promise;
  assert.equal(received, original);
  assert.deepEqual(await received.json(), { aweme_detail: note() });
  await drain();
  assert.equal(b.fetchCalls.length, 1);
  assert.equal(b.fetchCalls[0].receiver, b.window);
  assert.equal(b.posted.length, 1);
  assert.equal(b.posted[0].target, ORIGIN);
  assert.equal(b.posted[0].message.type, "clipgo:douyin-media");
  assert.equal(b.posted[0].message.records[0].id, ID);
});

test("Bridge ignores cross-origin, account endpoints and non-JSON responses", async () => {
  const b = bridge();
  for (const [url, type] of [
    ["https://evil.example/aweme/v1/web/aweme/detail/", "application/json"],
    ["https://other.douyin.com/aweme/v1/web/aweme/detail/", "application/json"],
    [ORIGIN + "/aweme/v1/web/user/profile/", "application/json"],
    [API, "text/html"],
  ]) {
    const original = response(note(), url, type);
    b.pending.push(Promise.resolve(original));
    await b.window.fetch(url);
    await original.text();
  }
  await drain();
  assert.equal(b.posted.length, 0);
});

test("XHR observation preserves calls and rejects unrelated request or response URLs", () => {
  const b = bridge();
  const xhr = new b.window.XMLHttpRequest();
  assert.equal(xhr.open("GET", "/aweme/v1/web/aweme/detail/", true), "opened");
  assert.equal(xhr.send("unchanged-body"), "sent");
  xhr.responseType = "json";
  xhr.respond({ aweme_detail: note() });
  assert.deepEqual(xhr.openArgs, ["GET", "/aweme/v1/web/aweme/detail/", true]);
  assert.deepEqual(xhr.sendArgs, ["unchanged-body"]);
  assert.equal(b.posted.length, 1);
  xhr.open("GET", "/aweme/v1/web/user/profile/");
  xhr.send();
  xhr.respond(note());
  xhr.open("GET", API);
  xhr.send();
  xhr.respond(note(), "https://evil.example/aweme/v1/web/aweme/detail/");
  assert.equal(b.posted.length, 1);
});

test("Bridge captures module and channel video feeds under the same-origin aweme API", () => {
  const b = bridge();
  const xhr = new b.window.XMLHttpRequest();
  for (const route of [
    "/aweme/v1/web/module/feed/",
    "/aweme/v2/web/channel/recommend/feed/",
    "/aweme/v1/web/module/channel/detail/",
    "/aweme/v1/web/mix/aweme/",
    "/aweme/v1/feed/",
  ]) {
    xhr.open("GET", route);
    xhr.send();
    xhr.respond({ aweme_list: [note("7682626334308289828")] }, ORIGIN + route + "?module=selected");
  }
  assert.equal(b.posted.length, 5);
  assert.ok(b.posted.every(({ message }) => message.records[0].id === "7682626334308289828"));
});

test("Broader feed routing still rejects non-video JSON paths and other API origins", () => {
  const b = bridge();
  const xhr = new b.window.XMLHttpRequest();
  for (const url of [
    ORIGIN + "/aweme/v1/web/user/profile/",
    ORIGIN + "/aweme/v1/web/module/feed/settings/",
    ORIGIN + "/aweme/v1/web/module/feed.js",
    ORIGIN + "/api/module/feed/",
    ORIGIN + "/other/aweme/v1/web/module/feed/",
    "https://other.douyin.com/aweme/v1/web/module/feed/",
    "https://evil.example/aweme/v1/web/module/feed/",
  ]) {
    xhr.open("GET", url);
    xhr.send();
    // Even a matching video payload cannot override endpoint restrictions.
    xhr.respond({ aweme_list: [note()] }, url);
  }
  assert.equal(b.posted.length, 0);
});

test("Bridge replay requires matching window/origin and retains only the latest hundred IDs", () => {
  const b = bridge();
  const xhr = new b.window.XMLHttpRequest();
  xhr.open("GET", API);
  xhr.send();
  xhr.respond({ aweme_list: Array.from({ length: 100 }, (_, i) => note(String(i + 1))) });
  xhr.respond({ aweme_list: Array.from({ length: 5 }, (_, i) => note(String(i + 101))) });
  const count = b.posted.length;
  const data = { type: "clipgo:douyin-media-request" };
  b.handlers.message({ source: {}, origin: ORIGIN, data });
  b.handlers.message({ source: b.window, origin: "https://evil.example", data });
  assert.equal(b.posted.length, count);
  b.handlers.message({ source: b.window, origin: ORIGIN, data });
  const replay = b.posted.at(-1).message.records;
  assert.equal(replay.length, 100);
  assert.equal(replay[0].id, "6");
  assert.equal(replay.at(-1).id, "105");
  assert.equal(b.fetchCalls.length, 0);
});

test("Bridge rejects oversized response bodies and does not install twice", async () => {
  const b = bridge();
  const installedFetch = b.window.fetch;
  vm.runInContext(pageSource, b.ctx);
  assert.equal(b.window.fetch, installedFetch);
  const original = response({ aweme_detail: note(), padding: "x".repeat(2 * 1024 * 1024) });
  b.pending.push(Promise.resolve(original));
  await b.window.fetch(API);
  await original.text();
  await drain();
  assert.equal(b.posted.length, 0);
  const xhr = new b.window.XMLHttpRequest();
  xhr.open("GET", API);
  xhr.send();
  xhr.headers["content-length"] = String(3 * 1024 * 1024);
  xhr.respond(note());
  assert.equal(b.posted.length, 0);
});
