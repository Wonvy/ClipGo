const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Small DOM fixtures exercise the public mouse/click handlers without a browser
// dependency. They cover hit testing and navigation, not the live site's markup.
class Element {
  constructor(tagName, attributes = {}) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.rect = { left: 0, top: 0, width: 300, height: 180 };
    this.isConnected = true;
    Object.assign(this, attributes);
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter((name) => !names.includes(name)).join(" "); },
    };
  }
  append(...nodes) { for (const node of nodes) if (typeof node !== "string") this.appendChild(node); }
  appendChild(node) { node.parentElement = this; this.children.push(node); return node; }
  attachShadow() { this.shadow = new Element("shadow"); return this.shadow; }
  setAttribute(name, value) { this[name] = value; }
  getAttribute(name) { return this[name] ?? null; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  getBoundingClientRect() { return this.rect; }
  contains(other) { return this === other || this.children.some((child) => child.contains(other)); }
  matches(selector) {
    return selector.split(",").some((part) => {
      part = part.trim();
      if (part.startsWith(".")) return this.classList.contains(part.slice(1));
      if (part.startsWith("#")) return this.id === part.slice(1);
      if (part === "a[href]") return this.tagName === "A" && !!this.href;
      const present = part.match(/^(\w*)\[([^=]+)\]$/);
      if (present) return (!present[1] || this.tagName === present[1].toUpperCase()) && this[present[2]] !== undefined;
      const attribute = part.match(/^(\w*)\[([^=]+)="([^"]+)"\]$/);
      if (attribute) return (!attribute[1] || this.tagName === attribute[1].toUpperCase()) && this[attribute[2]] === attribute[3];
      return this.tagName === part.toUpperCase();
    });
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  querySelectorAll(selector) {
    return this.children.flatMap((node) => [...(node.matches(selector) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

async function page(url, tasks = []) {
  const document = new Element("document");
  document.body = new Element("body");
  document.documentElement = new Element("html");
  document.appendChild(document.documentElement);
  document.documentElement.appendChild(document.body);
  document.createElement = (tag) => new Element(tag, { isConnected: false });
  document.createElementNS = (_ns, tag) => new Element(tag);
  document.getElementById = (id) => document.querySelector("#" + id);
  let pointed = null;
  let stack = [];
  document.elementFromPoint = () => pointed;
  document.elementsFromPoint = () => stack;
  const frames = [];
  const messages = [];
  let contentMessage = null;
  const context = vm.createContext({
    document,
    window: new Element("window", { innerWidth: 1200, innerHeight: 900, postMessage() {} }),
    location: new URL(url),
    URL,
    getComputedStyle: (node) => ({ display: "block", visibility: "visible", opacity: "1", ...node.style }),
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    requestAnimationFrame: (fn) => frames.push(fn),
    chrome: {
      storage: { local: { get: async () => ({ settings: {}, tasks }) }, onChanged: { addListener() {} } },
      runtime: {
        sendMessage: async (message) => { messages.push(message); return { ok: true }; },
        onMessage: { addListener(listener) { contentMessage = listener; } },
      },
    },
  });
  for (const file of ["common.js", "douyin-media.js", "content.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../extension", file), "utf8"), context);
  }
  await Promise.resolve();
  const button = document.body.children[0].shadow.children.find((node) => node.tagName === "BUTTON");
  return {
    context, button, messages, body: document.body,
    bridge(records, overrides = {}) {
      context.window.listeners.message({ source: context.window, origin: context.location.origin, data: { type: "clipgo:douyin-media", records }, ...overrides });
    },
    currentVideo(expectedUrl) {
      let response;
      contentMessage({ cmd: "currentVideo", ...(expectedUrl !== undefined ? { url: expectedUrl } : {}) }, {}, (value) => { response = value; });
      return response;
    },
    pageVideo(expectedUrl) {
      let response;
      contentMessage({ cmd: "pageVideo", url: expectedUrl }, {}, (value) => { response = value; });
      return response;
    },
    hover(node, layers = [node]) {
      pointed = node;
      stack = layers;
      document.listeners.mousemove({ clientX: 50, clientY: 50 });
      while (frames.length) frames.shift()();
    },
    async click() { await button.listeners.click({ preventDefault() {}, stopPropagation() {} }); },
  };
}

const first = "https://www.bilibili.com/video/BV1xx411c7mD";

test("Bilibili thumbnail links retain the selected part", async () => {
  const p = await page("https://www.bilibili.com/");
  const link = new Element("a", { href: first + "?p=2&spm_id_from=feed" });
  p.hover(link.appendChild(new Element("img")));
  await p.click();
  assert.equal(p.messages[0].url, first + "?p=2");
});

test("a card preview overlay downloads its card, not the current video", async () => {
  const p = await page(first);
  const card = new Element("div", { className: "bili-video-card" });
  card.appendChild(new Element("a", { href: "https://www.bilibili.com/video/av170001" }));
  const preview = card.appendChild(new Element("video"));
  p.hover(preview);
  await p.click();
  assert.equal(p.messages[0].url, "https://www.bilibili.com/video/av170001");
});

test("player overlays work when no native video is exposed under the pointer", async () => {
  const p = await page(first + "?p=2");
  const player = new Element("div", { className: "bpx-player-container" });
  player.appendChild(new Element("bwp-video"));
  const overlay = player.appendChild(new Element("div"));
  p.hover(overlay);
  await p.click();
  assert.equal(p.messages[0].url, first + "?p=2");
});

test("custom bwp-video playback is detected outside the known player wrappers", async () => {
  const p = await page(first);
  p.hover(new Element("bwp-video"));
  await p.click();
  assert.equal(p.messages[0].url, first);
});

test("switching parts before clicking refreshes the URL and previous busy state", async () => {
  const p = await page(first, [{ id: "p1", url: first, status: "running", percent: 20 }]);
  p.hover(new Element("div", { id: "bilibili-player" }));
  assert.equal(p.button.classList.contains("busy"), true);
  p.context.location.href = first + "?p=2";
  await p.click();
  assert.equal(p.messages[0].url, first + "?p=2");
});

test("leaving the video page prevents an old player button from downloading", async () => {
  const p = await page(first);
  p.hover(new Element("div", { className: "bpx-player-container" }));
  p.context.location.href = "https://www.bilibili.com/";
  await p.click();
  assert.equal(p.messages.length, 0);
  assert.equal(p.button.classList.contains("show"), false);
});

test("YouTube player targeting stays intact", async () => {
  const p = await page("https://www.youtube.com/watch?v=jNQXAC9IVRw");
  const player = new Element("div", { id: "movie_player" });
  const video = player.appendChild(new Element("video"));
  p.hover(video);
  await p.click();
  assert.equal(p.messages[0].url, "https://www.youtube.com/watch?v=jNQXAC9IVRw");
});

test("Instagram feed videos still use their article permalink", async () => {
  const p = await page("https://www.instagram.com/");
  const article = new Element("article");
  article.appendChild(new Element("a", { href: "https://www.instagram.com/reel/Example123/" }));
  const video = article.appendChild(new Element("video"));
  p.hover(video);
  await p.click();
  assert.equal(p.messages[0].url, "https://www.instagram.com/reel/Example123/");
});

function permalink(container, url) {
  const link = container.appendChild(new Element("a", { href: url }));
  link.appendChild(new Element("time"));
  return link;
}

const tweet = "https://x.com/clipgo/status/1234567890123456789";
const quotedTweet = "https://x.com/other/status/9876543210987654321";

test("X timeline player overlays use the owning timestamp, excluding quoted links", async () => {
  const p = await page("https://x.com/home");
  const article = new Element("article");
  const quote = article.appendChild(new Element("div", { "data-testid": "quoteTweet" }));
  permalink(quote, quotedTweet);
  article.appendChild(new Element("a", { href: "https://x.com/Another/status/1111111111111111111" }));
  permalink(article, tweet);
  const player = article.appendChild(new Element("div", { "data-testid": "videoPlayer" }));
  p.hover(player.appendChild(new Element("div")));
  await p.click();
  assert.equal(p.messages[0].url, tweet);
});

test("a video inside a quoted tweet uses the quoted permalink", async () => {
  const p = await page("https://x.com/home");
  const article = new Element("article");
  permalink(article, tweet);
  const quote = article.appendChild(new Element("div", { "data-testid": "quoteTweet" }));
  permalink(quote, quotedTweet);
  p.hover(quote.appendChild(new Element("video")));
  await p.click();
  assert.equal(p.messages[0].url, quotedTweet);
});

test("quoted tweet cards using role=link stay separate from the outer tweet", async () => {
  const p = await page("https://twitter.com/home");
  const article = new Element("article");
  permalink(article, tweet);
  const quote = article.appendChild(new Element("div", { role: "link" }));
  permalink(quote, quotedTweet);
  p.hover(quote.appendChild(new Element("video")));
  await p.click();
  assert.equal(p.messages[0].url, quotedTweet);
});

test("an unresolved reply video never downloads the page's unrelated post", async () => {
  const p = await page(tweet);
  const article = new Element("article");
  p.hover(article.appendChild(new Element("video")));
  await p.click();
  assert.equal(p.messages.length, 0);
});

test("a standalone X media viewer uses the status page URL", async () => {
  const p = await page(tweet);
  p.hover(new Element("video"));
  await p.click();
  assert.equal(p.messages[0].url, tweet);
});

test("recycled X timeline players refresh their permalink before downloading", async () => {
  const p = await page("https://x.com/home");
  const article = new Element("article");
  const link = permalink(article, tweet);
  p.hover(article.appendChild(new Element("video")));
  link.href = quotedTweet;
  await p.click();
  assert.equal(p.messages[0].url, quotedTweet);
});

test("X status links without a video do not get a quick-download button", async () => {
  const p = await page("https://x.com/home");
  p.hover(new Element("a", { href: tweet }));
  await p.click();
  assert.equal(p.messages.length, 0);
});

test("X attachment selection survives a bare article timestamp for the same status", async () => {
  for (const kind of ["video", "photo"]) {
    const selected = tweet + "/" + kind + "/2";
    const p = await page(selected);
    const article = new Element("article");
    // Match by status ID even when the viewer and timestamp use different aliases.
    permalink(article, "https://twitter.com/i/web/status/1234567890123456789");
    p.hover(article.appendChild(new Element("video")));
    await p.click();
    assert.equal(p.messages[0].url, selected);
  }
});

test("X body links never substitute for a missing owning timestamp", async () => {
  for (const quoted of [false, true]) {
    const p = await page(tweet + "/video/2");
    const article = new Element("article");
    const container = quoted
      ? article.appendChild(new Element("div", { "data-testid": "quoteTweet" }))
      : article;
    container.appendChild(new Element("a", { href: quotedTweet }));
    p.hover(container.appendChild(new Element("video")));
    await p.click();
    assert.equal(p.messages.length, 0);
  }
});

test("a direct X media link takes priority over the article and current page selection", async () => {
  const p = await page(tweet + "/video/2");
  const article = new Element("article");
  permalink(article, tweet);
  const link = article.appendChild(new Element("a", { href: tweet + "/video/3" }));
  p.hover(link.appendChild(new Element("video")));
  await p.click();
  assert.equal(p.messages[0].url, tweet + "/video/3");
});

test("the current page's X attachment index is not transferred to a reply video", async () => {
  const p = await page(tweet + "/video/2");
  const reply = new Element("article");
  permalink(reply, quotedTweet);
  p.hover(reply.appendChild(new Element("video")));
  await p.click();
  assert.equal(p.messages[0].url, quotedTweet);
});

const noteId = "67d45a9b000000001c0174ba";
const noteUrl = "https://www.xiaohongshu.com/explore/" + noteId;
const signedNote = noteUrl + "?xsec_token=token%2Bvalue&xsec_source=pc_feed";
const douyinVideo = "https://www.douyin.com/video/7695347795917573386";
const tiktokVideo = "https://www.tiktok.com/@buckppt/video/7685208008271449364";

function noteCard(url = signedNote, video = true) {
  const card = new Element("section", { className: "note-item", "data-note-id": noteId });
  card.appendChild(new Element("a", { href: noteUrl, rect: { left: 0, top: 0, width: 0, height: 0 } }));
  const cover = card.appendChild(new Element("a", { href: url, className: "cover mask" }));
  if (video) cover.appendChild(new Element("span", { className: "play-icon" }));
  card.appendChild(new Element("a", { href: url, className: "title", textContent: "示例笔记标题" }));
  return { card, cover };
}

test("Xiaohongshu cards use the signed visible cover, not the hidden tokenless link", async () => {
  const p = await page("https://www.xiaohongshu.com/explore");
  const { card, cover } = noteCard();
  p.body.appendChild(card);
  p.hover(cover.appendChild(new Element("img")));
  await p.click();
  assert.equal(p.messages[0].url, signedNote);
  assert.equal(p.messages[0].title, "示例笔记标题");
});

test("Xiaohongshu photo cards without a play icon do not get video buttons", async () => {
  const p = await page("https://www.xiaohongshu.com/explore");
  const { card, cover } = noteCard(signedNote, false);
  p.body.appendChild(card);
  p.hover(cover);
  await p.click();
  assert.equal(p.messages.length, 0);
});

test("Xiaohongshu modal videos preserve token and title while excluding background playback", async () => {
  const p = await page(signedNote);
  const { card, cover } = noteCard();
  p.body.appendChild(card);
  cover.appendChild(new Element("video", { paused: false }));
  const modal = p.body.appendChild(new Element("div", { className: "note-detail-mask", "note-id": noteId }));
  const detail = modal.appendChild(new Element("div", { id: "noteContainer", "data-type": "video" }));
  detail.appendChild(new Element("h1", { id: "detail-title", textContent: " 当前视频标题 " }));
  const player = detail.appendChild(new Element("div", { className: "player-el xgplayer" }));
  player.appendChild(new Element("video", { paused: true }));
  p.hover(player.appendChild(new Element("div")));
  await p.click();
  assert.equal(p.messages[0].url, signedNote);
  assert.equal(p.messages[0].title, "当前视频标题");
  assert.deepEqual({ ...p.currentVideo() }, { url: signedNote, title: "当前视频标题" });
});

test("a Xiaohongshu photo modal cannot select a background feed video", async () => {
  const p = await page(signedNote);
  const { card, cover } = noteCard();
  p.body.appendChild(card);
  const backgroundVideo = cover.appendChild(new Element("video", { paused: false }));
  p.hover(backgroundVideo);
  const modal = p.body.appendChild(new Element("div", { className: "note-detail-mask", "note-id": noteId }));
  modal.appendChild(new Element("div", { id: "noteContainer", "data-type": "normal" }));
  assert.equal(p.currentVideo().url, null);
  await p.click(); // A button left over from before the modal opened is stale.
  assert.equal(p.messages.length, 0);
  p.hover(modal, [modal, backgroundVideo]);
  await p.click(); // A photo/modal overlay must not hit a video underneath it.
  assert.equal(p.messages.length, 0);
});

test("an opacity-hidden Xiaohongshu modal and its videos do not mask the current feed preview", async () => {
  const p = await page("https://www.xiaohongshu.com/explore");
  const { card, cover } = noteCard();
  p.body.appendChild(card);
  cover.appendChild(new Element("video", { paused: false }));
  const hiddenModal = p.body.appendChild(new Element("div", {
    className: "note-detail-mask", "note-id": noteId, style: { opacity: "0" },
  }));
  const detail = hiddenModal.appendChild(new Element("div", { id: "noteContainer", "data-type": "video" }));
  detail.appendChild(new Element("video", { paused: false }));
  assert.equal(p.currentVideo().url, signedNote);
});

test("a reused Xiaohongshu modal cannot download a previous note URL", async () => {
  const p = await page(signedNote);
  const modal = p.body.appendChild(new Element("div", { className: "note-detail-mask", "note-id": "67d45a9b000000001c0174bb" }));
  const detail = modal.appendChild(new Element("div", { id: "noteContainer", "data-type": "video" }));
  const video = detail.appendChild(new Element("video", { paused: false }));
  p.hover(video);
  await p.click();
  assert.equal(p.messages.length, 0);
  assert.equal(p.currentVideo().url, null);
});

function douyinCard(id = "7695347795917573386", extra = {}) {
  const card = new Element("div", { className: "discover-video-card-item", "data-aweme-id": id });
  const preview = card.appendChild(new Element("div", { className: "waterfall-videoCardContainer jingxuanVideoCard", href: "//www.douyin.com/video/" + id }));
  const player = preview.appendChild(new Element("div", { className: "xgplayer xgplayer-playing" }));
  const video = player.appendChild(new Element("video", { paused: false, ...extra }));
  return { card, player, video };
}

test("Douyin non-anchor cards identify the preview by data-aweme-id", async () => {
  const p = await page("https://www.douyin.com/jingxuan");
  const { card, player } = douyinCard();
  p.body.appendChild(card);
  p.hover(player);
  await p.click();
  assert.equal(p.messages[0].url, douyinVideo);
  assert.equal(p.currentVideo().url, douyinVideo);
});

test("Douyin DIV[href] cards work without a data-aweme-id ancestor", async () => {
  const p = await page("https://www.douyin.com/jingxuan");
  const preview = p.body.appendChild(new Element("div", { className: "waterfall-videoCardContainer", href: "//www.douyin.com/video/7695347795917573386" }));
  const video = preview.appendChild(new Element("video", { paused: false }));
  p.hover(video);
  await p.click();
  assert.equal(p.messages[0].url, douyinVideo);
});

test("currentVideo excludes hidden and offscreen feed players", async () => {
  const p = await page("https://www.douyin.com/jingxuan");
  const visible = douyinCard();
  const offscreen = douyinCard("7695347795917573387", { rect: { left: 0, top: 1000, width: 300, height: 180 } });
  const hidden = douyinCard("7695347795917573388");
  hidden.card.hidden = true;
  p.body.append(visible.card, offscreen.card, hidden.card);
  assert.equal(p.currentVideo().url, douyinVideo);
});

test("currentVideo refuses ambiguous feeds and selects a single playing video", async () => {
  const p = await page("https://www.douyin.com/jingxuan");
  const one = douyinCard();
  const two = douyinCard("7695347795917573387");
  p.body.append(one.card, two.card);
  assert.equal(p.currentVideo().url, null);
  two.video.paused = true;
  assert.equal(p.currentVideo().url, douyinVideo);
});

test("Douyin current modal ID overrides a previous watch page path", async () => {
  const p = await page(douyinVideo + "?modal_id=7695347795917573387");
  const player = p.body.appendChild(new Element("div", { className: "xgplayer" }));
  player.appendChild(new Element("video"));
  p.hover(player);
  await p.click();
  assert.equal(p.messages[0].url, "https://www.douyin.com/video/7695347795917573387");
  assert.equal(p.currentVideo().url, "https://www.douyin.com/video/7695347795917573387");
});

test("TikTok search preview links identify the video without hashed card classes", async () => {
  const p = await page("https://www.tiktok.com/search?q=buckppt");
  const link = p.body.appendChild(new Element("a", { href: tiktokVideo }));
  const video = link.appendChild(new Element("video", { paused: false }));
  p.hover(video);
  await p.click();
  assert.equal(p.messages[0].url, tiktokVideo);
  assert.equal(p.currentVideo().url, tiktokVideo);
});

test("TikTok detail overlays and reused players follow the current watch URL", async () => {
  const p = await page(tiktokVideo + "?q=buckppt&t=123");
  const player = p.body.appendChild(new Element("div", { className: "xgplayer-container tiktok-web-player" }));
  const overlay = player.appendChild(new Element("div"));
  p.hover(overlay);
  p.context.location.href = "https://www.tiktok.com/@buckppt/video/7685208008271449365";
  await p.click();
  assert.equal(p.messages[0].url, p.context.location.href);
});

test("Tencent series-only links are not treated as individual videos", async () => {
  const p = await page("https://v.qq.com/channel/cartoon");
  const link = p.body.appendChild(new Element("a", { href: "https://v.qq.com/x/cover/series123.html" }));
  p.hover(link);
  await p.click();
  assert.equal(p.messages.length, 0);
  assert.equal(p.currentVideo().url, null);
});

test("Tencent specific video links and standalone players keep the episode URL", async () => {
  const episode = "https://v.qq.com/x/cover/series123/v1234567890.html";
  const p = await page("https://v.qq.com/channel/cartoon");
  const link = p.body.appendChild(new Element("a", { href: episode }));
  p.hover(link);
  await p.click();
  assert.equal(p.messages[0].url, episode);
  const watch = await page(episode);
  watch.hover(watch.body.appendChild(new Element("video")));
  await watch.click();
  assert.equal(watch.messages[0].url, episode);
  assert.equal(watch.currentVideo().url, episode);
});

test("Tencent player wrappers support shadow playback and follow the actual resumed episode", async () => {
  const p = await page("https://v.qq.com/x/cover/mzc0020027yzd9e/a4101lu4omv.html");
  const player = p.body.appendChild(new Element("div", { id: "main-player", className: "main-player-container" }));
  const component = player.appendChild(new Element("div", { id: "player-component", className: "_mod_thumbplayer_container_" }));
  const controls = component.appendChild(new Element("div"));
  p.hover(controls);
  p.context.location.href = "https://v.qq.com/x/cover/mzc0020027yzd9e/l4102ber3ei.html";
  p.context.document.title = "斗破苍穹年番 第218话";
  await p.click();
  assert.equal(p.messages[0].url, p.context.location.href);
  assert.equal(p.messages[0].title, "斗破苍穹年番 第218话");
  assert.equal(p.currentVideo().url, p.context.location.href);
});

test("Tencent banner IDs alone do not infer an episode that may differ from resume selection", async () => {
  const p = await page("https://v.qq.com/channel/cartoon");
  const card = p.body.appendChild(new Element("div", {
    className: "banner-card",
    id: "banner-card-container__mzc0020027yzd9e_a4101lu4omv_2",
    "data-ckey": "mzc0020027yzd9e",
  }));
  p.hover(card);
  await p.click();
  assert.equal(p.messages.length, 0);
});

test("currentVideo keeps existing Bilibili selected-part URLs", async () => {
  const p = await page(first + "?p=2");
  p.context.document.title = "测试视频 - 哔哩哔哩";
  assert.deepEqual({ ...p.currentVideo() }, { url: first + "?p=2", title: "测试视频 - 哔哩哔哩" });
});

const douyinSource = "https://v26-web.douyinvod.com/example.mp4?token=opaque";

function loadedDouyinSlider(id = "7695347795917573386", attributes = {}) {
  const slider = new Element("div", { id: "sliderVideo", className: "sliderVideo video_" + id });
  const player = slider.appendChild(new Element("div", { className: "xgplayer" }));
  const video = player.appendChild(new Element("video", { paused: true, duration: 61.742, currentSrc: douyinSource, ...attributes }));
  return { slider, player, video };
}

test("a visible loaded Douyin player supplies its HTTPS media even when paused", async () => {
  const p = await page(douyinVideo);
  const { slider, player } = loadedDouyinSlider();
  p.body.appendChild(slider);
  const result = p.currentVideo(douyinVideo);
  assert.deepEqual({ ...result.media }, { url: douyinSource, videoUrl: douyinVideo });
  p.hover(player);
  await p.click();
  assert.deepEqual({ ...p.messages[0].media }, { url: douyinSource, videoUrl: douyinVideo });
});

test("currentVideo retry requests never attach media for a different expected video", async () => {
  const p = await page("https://www.douyin.com/jingxuan?modal_id=7695347795917573386");
  p.body.appendChild(loadedDouyinSlider().slider);
  for (const expected of ["https://www.douyin.com/video/7695347795917573387", "https://example.com/", "not a URL", null]) {
    const result = p.currentVideo(expected);
    assert.equal(result.url, douyinVideo);
    assert.equal(result.media, undefined);
  }
  assert.equal(p.currentVideo("https://www.douyin.com/jingxuan?modal_id=7695347795917573386").media.videoUrl, douyinVideo);
});

test("a page URL alone cannot assign a loaded Douyin stream to an unknown or conflicting DOM owner", async () => {
  const p = await page(douyinVideo);
  const { slider, video } = loadedDouyinSlider();
  slider.className = "sliderVideo";
  p.body.appendChild(slider);
  assert.equal(p.currentVideo().media, undefined);
  slider.className = "sliderVideo video_7695347795917573386";
  slider["data-aweme-id"] = "7695347795917573387";
  assert.equal(p.currentVideo().media, undefined);
  delete slider["data-aweme-id"];
  video.isConnected = false;
  assert.equal(p.currentVideo().media, undefined);
});

test("offscreen current and visible next Douyin videos cannot be mixed", async () => {
  const p = await page("https://www.douyin.com/jingxuan?modal_id=7695347795917573386");
  const current = loadedDouyinSlider("7695347795917573386", { rect: { left: 0, top: -1000, width: 300, height: 180 } });
  const next = loadedDouyinSlider("7695347795917573387", { currentSrc: "https://v26-web.douyinvod.com/next.mp4" });
  p.body.append(current.slider, next.slider);
  assert.equal(p.currentVideo().media, undefined);
  current.video.rect.top = 0;
  next.video.rect.top = 904;
  assert.equal(p.currentVideo().media.url, douyinSource);
});

test("Douyin direct media rejects blob/data, lookalike hosts, credentials and nonstandard ports", async () => {
  const p = await page(douyinVideo);
  const { slider, video } = loadedDouyinSlider();
  p.body.appendChild(slider);
  for (const source of [
    "blob:https://www.douyin.com/id", "data:video/mp4;base64,AAAA", "http://v26-web.douyinvod.com/file.mp4",
    "https://douyinvod.com.evil.example/file.mp4", "https://notdouyinvod.com/file.mp4",
    "https://user:pass@v26-web.douyinvod.com/file.mp4", "https://v26-web.douyinvod.com:444/file.mp4",
    "https://v26-web.douyinvod.com:443/file.mp4", "", undefined,
  ]) {
    video.currentSrc = source;
    assert.equal(p.currentVideo().media, undefined, String(source));
  }
});

test("Douyin direct media requires a finite positive loaded duration and visible ancestors", async () => {
  const p = await page(douyinVideo);
  const { slider, video } = loadedDouyinSlider();
  p.body.appendChild(slider);
  for (const duration of [NaN, Infinity, 0, -1, undefined, "61.742"]) {
    video.duration = duration;
    assert.equal(p.currentVideo().media, undefined);
  }
  video.duration = 61.742;
  slider.style.opacity = "0";
  assert.equal(p.currentVideo().media, undefined);
});

test("a same-ID bridge record resolves blob playback and supplies title and quality variants", async () => {
  const p = await page(douyinVideo);
  p.context.document.title = "抖音 - 记录美好生活";
  const { slider, player } = loadedDouyinSlider("7695347795917573386", { currentSrc: "blob:https://www.douyin.com/current" });
  p.body.appendChild(slider);
  p.bridge([{
    id: "7695347795917573386", title: "实际视频标题", url: douyinSource,
    formats: [{ url: douyinSource, width: 1920, height: 1080, bitrate: 2000000 }, { url: "https://evil.example/file.mp4", height: 720 }],
  }]);
  const result = p.currentVideo(douyinVideo);
  assert.equal(result.title, "实际视频标题");
  assert.equal(result.media.url, douyinSource);
  assert.equal(result.media.formats.length, 1);
  assert.equal(result.media.formats[0].height, 1080);
  p.hover(player);
  await p.click();
  assert.equal(p.messages[0].title, "实际视频标题");
  assert.equal(p.messages[0].media.url, douyinSource);
});

test("Douyin bridge records require same-window origin and matching loaded video ID", async () => {
  const p = await page(douyinVideo);
  p.body.appendChild(loadedDouyinSlider("7695347795917573386", { currentSrc: "blob:https://www.douyin.com/current" }).slider);
  const record = { id: "7695347795917573386", title: "视频", url: douyinSource };
  p.bridge([record], { origin: "https://evil.example" });
  p.bridge([record], { source: {} });
  p.bridge([{ ...record, id: "7695347795917573387" }]);
  p.bridge([{ ...record, url: "https://evil.example/file.mp4" }]);
  assert.equal(p.currentVideo().media, undefined);
  p.bridge([record]);
  assert.equal(p.currentVideo().media.url, douyinSource);
});

test("cached Douyin records alone never authorize media for a hidden or uninitialized player", async () => {
  const p = await page(douyinVideo);
  const { slider, video } = loadedDouyinSlider("7695347795917573386", { duration: NaN, currentSrc: "blob:https://www.douyin.com/current" });
  p.body.appendChild(slider);
  p.bridge([{ id: "7695347795917573386", title: "视频", url: douyinSource }]);
  assert.equal(p.currentVideo().media, undefined);
  video.duration = 61.742;
  slider.hidden = true;
  assert.equal(p.currentVideo().media, undefined);
});

test("Douyin quick download reads a refreshed media URL at click time", async () => {
  const p = await page(douyinVideo);
  const { slider, player, video } = loadedDouyinSlider();
  p.body.appendChild(slider);
  p.hover(player);
  video.currentSrc = "https://v26-web.douyinvod.com/example.mp4?token=refreshed";
  await p.click();
  assert.equal(p.messages[0].media.url, video.currentSrc);
});

test("encoded RENDER_DATA resolves the visible Douyin blob player by exact aweme ID", async () => {
  const p = await page(douyinVideo);
  p.context.document.title = "抖音";
  p.body.appendChild(loadedDouyinSlider("7695347795917573386", { currentSrc: "blob:https://www.douyin.com/current" }).slider);
  p.body.appendChild(new Element("script", { id: "RENDER_DATA", textContent: encodeURIComponent(JSON.stringify({ loader: { list: [
    { aweme_id: "7695347795917573387", desc: "下一条", video: { playAddr: [{ src: "https://v26-web.douyinvod.com/next.mp4" }] } },
    { aweme_id: "7695347795917573386", desc: "页面中的当前视频", video: {
      width: 1920, height: 1080, playAddr: [{ src: douyinSource }],
      bitRateList: [{ width: 1280, height: 720, isH265: 0, bitRate: 1500000, playerAccessKey: "", playAddr: [{ src: "https://v26-web.douyinvod.com/720.mp4" }] }],
    } },
  ] } })) }));
  const result = p.currentVideo(douyinVideo);
  assert.equal(result.media.url, douyinSource);
  assert.equal(result.media.videoUrl, douyinVideo);
  assert.equal(result.title, "页面中的当前视频");
  assert.equal(result.media.formats.some((format) => format.height === 720), true);
});

test("initial Douyin RENDER_DATA cannot overwrite a newer captured media address", async () => {
  const p = await page(douyinVideo);
  p.body.appendChild(loadedDouyinSlider("7695347795917573386", { currentSrc: "blob:https://www.douyin.com/current" }).slider);
  p.body.appendChild(new Element("script", { id: "RENDER_DATA", textContent: JSON.stringify({
    aweme_id: "7695347795917573386", desc: "视频", video: { playAddr: [{ src: douyinSource }] },
  }) }));
  const fresh = "https://v26-web.douyinvod.com/example.mp4?token=new";
  p.bridge([{ id: "7695347795917573386", title: "视频", url: fresh }]);
  assert.equal(p.currentVideo().media.url, fresh);
});

test("pageVideo resolves only the exact dedicated Douyin page record without requiring playback", async () => {
  const p = await page(douyinVideo);
  p.context.document.title = "抖音";
  p.body.appendChild(new Element("script", { id: "RENDER_DATA", textContent: encodeURIComponent(JSON.stringify({ loader: { list: [
    { aweme_id: "7695347795917573387", desc: "推荐视频", video: { playAddr: [{ src: "https://v26-web.douyinvod.com/next.mp4" }] } },
    { aweme_id: "7695347795917573386", desc: "目标视频", video: { width: 1920, height: 1080, playAddr: [{ src: douyinSource }] } },
  ] } })) }));
  const result = p.pageVideo(douyinVideo);
  assert.equal(result.url, douyinVideo);
  assert.equal(result.title, "目标视频");
  assert.equal(result.media.url, douyinSource);
  assert.equal(result.media.videoUrl, douyinVideo);
  assert.equal(result.media.formats[0].height, 1080);
  assert.equal(p.currentVideo(douyinVideo).media, undefined);
});

test("pageVideo rejects a mismatched requested ID even if it exists in the page's recommendation cache", async () => {
  const p = await page(douyinVideo);
  p.bridge([{ id: "7695347795917573387", title: "另一条", url: douyinSource }]);
  for (const expected of ["https://www.douyin.com/video/7695347795917573387", undefined, null, "invalid"]) {
    assert.deepEqual({ ...p.pageVideo(expected) }, { url: null, title: "" });
  }
  const result = p.pageVideo(douyinVideo);
  assert.equal(result.url, douyinVideo);
  assert.equal(result.media, undefined);
});

test("pageVideo refuses feed/modal locations and other platforms despite matching requested IDs", async () => {
  for (const url of [
    "https://www.douyin.com/jingxuan?modal_id=7695347795917573386",
    douyinVideo + "?modal_id=7695347795917573386",
    douyinVideo + "?modal_id=7695347795917573387",
    "https://www.douyin.com/", "https://www.tiktok.com/@example/video/7695347795917573386",
  ]) {
    const p = await page(url);
    if (p.context.location.hostname.includes("douyin")) p.bridge([{ id: "7695347795917573386", title: "目标视频", url: douyinSource }]);
    assert.deepEqual({ ...p.pageVideo(douyinVideo) }, { url: null, title: "" }, url);
  }
});

test("pageVideo can wait for exact-ID SSR or bridge data without selecting an arbitrary record", async () => {
  const p = await page(douyinVideo + "/?source=share");
  let result = p.pageVideo(douyinVideo);
  assert.equal(result.url, douyinVideo);
  assert.equal(result.media, undefined);
  p.bridge([{ id: "7695347795917573387", title: "不匹配", url: douyinSource }]);
  assert.equal(p.pageVideo(douyinVideo).media, undefined);
  p.bridge([{ id: "7695347795917573386", title: "目标视频", url: douyinSource }]);
  result = p.pageVideo(douyinVideo);
  assert.equal(result.media.url, douyinSource);
  assert.equal(result.title, "目标视频");
  assert.equal(p.currentVideo(douyinVideo).media, undefined);
});

test("pageVideo polls newly inserted exact-ID RENDER_DATA after the dedicated page initially has none", async () => {
  const p = await page(douyinVideo);
  assert.equal(p.pageVideo(douyinVideo).media, undefined);
  const script = p.body.appendChild(new Element("script", { id: "RENDER_DATA", textContent: "{" }));
  assert.equal(p.pageVideo(douyinVideo).media, undefined);
  script.textContent = JSON.stringify({ aweme_id: "7695347795917573386", video: { playAddr: [{ src: douyinSource }] } });
  assert.equal(p.pageVideo(douyinVideo).media.url, douyinSource);
});
