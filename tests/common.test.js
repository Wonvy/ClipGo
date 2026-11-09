const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const context = vm.createContext({ URL });
vm.runInContext(fs.readFileSync(path.join(__dirname, "../extension/common.js"), "utf8"), context);
const { CLIPGO } = context;
const BV = "BV1xx411c7mD";
const BILI = "https://www.bilibili.com/video/" + BV;
const STATUS = "1234567890123456789";

function video(raw, site, url) {
  assert.deepEqual({ ...CLIPGO.detect(raw) }, { site, isVideo: true, url }, raw);
}

function notVideo(raw, site) {
  assert.deepEqual({ ...CLIPGO.detect(raw) }, { site, isVideo: false }, raw);
}

test("Bilibili BV, av and mobile links become canonical video links", () => {
  for (const raw of [
    "https://www.bilibili.com/video/" + BV,
    "http://bilibili.com/video/" + BV + "/",
    "https://m.bilibili.com/video/" + BV + "?spm_id_from=333.999&vd_source=tracking#reply",
    "  https://WWW.BILIBILI.COM/video/" + BV + "  ",
  ]) video(raw, "bilibili", BILI);
  video("https://www.bilibili.com/video/av170001/?t=30", "bilibili", "https://www.bilibili.com/video/av170001");
  video("https://m.bilibili.com/video/av170001?p=3", "bilibili", "https://www.bilibili.com/video/av170001?p=3");
});

test("Bilibili preserves selected parts but deduplicates first-part URLs", () => {
  for (const query of ["", "?p=1", "?p=0001", "?p=1&spm_id_from=feed"]) {
    video(BILI + query, "bilibili", BILI);
  }
  for (const query of ["?p=2", "?p=0002&spm_id_from=feed", "?t=12&p=2#reply"]) {
    video(BILI + query, "bilibili", BILI + "?p=2");
  }
  assert.notEqual(CLIPGO.detect(BILI + "?p=2").url, CLIPGO.detect(BILI + "?p=3").url);
});

test("Bilibili malformed part numbers cannot silently select a different part", () => {
  for (const part of ["", "0", "-1", "1.5", "NaN", "Infinity", "2oops", "1e2", "9007199254740992", "%202", "%2B2"]) {
    notVideo(BILI + "?p=" + part, "bilibili");
  }
});

test("Bilibili homes, unsupported content and malformed IDs are not download targets", () => {
  for (const raw of [
    "https://www.bilibili.com/",
    "https://www.bilibili.com/video/",
    "https://www.bilibili.com/bangumi/play/ep12345",
    "https://www.bilibili.com/video/BV1",
    BILI + "extra",
    BILI + "/other",
    "https://www.bilibili.com/video/av0",
    "https://www.bilibili.com/video/av123xyz",
  ]) notVideo(raw, "bilibili");
});

test("Opaque b23 links retain redirect query data and discard fragments", () => {
  video("http://b23.tv/AbC123/", "bilibili", "https://b23.tv/AbC123");
  video("https://b23.tv/AbC123?share_source=copy&p=2&token=a%2Bb#fragment", "bilibili",
    "https://b23.tv/AbC123?share_source=copy&p=2&token=a%2Bb");
  notVideo("https://b23.tv/", "bilibili");
  notVideo("https://b23.tv/AbC123/extra", "bilibili");
  notVideo("https://b23.tv/AbC123?p=0", "bilibili");
});

test("Direct b23 BV and av aliases expand locally without losing part selection", () => {
  video("https://b23.tv/" + BV + "?p=2&share_source=copy", "bilibili", BILI + "?p=2");
  video("https://b23.tv/" + BV + "/?p=1", "bilibili", BILI);
  video("https://b23.tv/av170001?p=003&share_source=copy", "bilibili",
    "https://www.bilibili.com/video/av170001?p=3");
});

test("X and Twitter status aliases, including mobile hosts, share canonical links", () => {
  for (const host of ["x.com", "www.x.com", "m.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "m.twitter.com", "mobile.twitter.com"]) {
    video("https://" + host + "/Example_User/status/" + STATUS + "/?s=20&t=tracking#reply", "twitter",
      "https://x.com/example_user/status/" + STATUS);
  }
  video("http://twitter.com/Example_User/status/" + STATUS, "twitter", "https://x.com/example_user/status/" + STATUS);
});

test("X i/web status URLs are recognized without an author name", () => {
  for (const host of ["x.com", "twitter.com", "mobile.twitter.com"]) {
    video("https://" + host + "/i/web/status/" + STATUS + "?s=20", "twitter",
      "https://x.com/i/web/status/" + STATUS);
  }
});

test("X attachment indices survive normalization and stay distinct from the whole post", () => {
  const post = "https://x.com/example/status/" + STATUS;
  for (const media of ["video", "photo"]) {
    video("https://twitter.com/Example/status/" + STATUS + "/" + media + "/2/?s=20", "twitter",
      post + "/" + media + "/2");
    video("https://x.com/i/web/status/" + STATUS + "/" + media + "/3", "twitter",
      "https://x.com/i/web/status/" + STATUS + "/" + media + "/3");
    assert.notEqual(CLIPGO.detect(post + "/" + media + "/2").url, CLIPGO.detect(post).url);
  }
});

test("X home, profile and malformed status links are not download targets", () => {
  for (const suffix of [
    "/", "/home", "/example", "/example/media", "/example/status/", "/example/status/abc",
    "/example/status/0", "/example/status/123garbage", "/example/status/-123",
    "/example/status/123/video/0", "/example/status/123/photo/0",
    "/example/status/123/video/2/extra", "/example/status/123/videos/2",
    "/username_way_too_long/status/123", "/bad-name/status/123",
  ]) notVideo("https://x.com" + suffix, "twitter");
  notVideo("https://mobile.twitter.com/home", "twitter");
});

test("Lookalike hosts and non-web protocols are not accepted", () => {
  for (const raw of [
    "https://www.bilibili.com.evil.example/video/" + BV,
    "https://notbilibili.com/video/" + BV,
    "https://b23.tv.evil.example/AbC123",
    "https://b23tv.example/AbC123",
    "https://x.com.evil.example/example/status/" + STATUS,
    "https://notx.com/example/status/" + STATUS,
    "https://twitter.com.evil.example/example/status/" + STATUS,
    "https://twitter.com@evil.example/example/status/" + STATUS,
    "https://bilibili.com@evil.example/video/" + BV,
    "ftp://www.bilibili.com/video/" + BV,
    "javascript:alert(1)", "file:///video/" + BV, "data:text/plain,https://x.com/example/status/123",
    "not a URL", "", null, undefined,
  ]) assert.equal(CLIPGO.detect(raw), null, String(raw));
});

test("YouTube watch, Shorts, mobile and short links keep existing behavior", () => {
  const id = "jNQXAC9IVRw";
  for (const raw of [
    "https://www.youtube.com/watch?v=" + id + "&list=playlist&pp=tracking",
    "https://m.youtube.com/watch?v=" + id,
    "https://music.youtube.com/watch?v=" + id,
    "https://youtu.be/" + id + "?si=tracking",
    "https://www.youtube.com/live/" + id,
    "https://www.youtube.com/embed/" + id,
  ]) video(raw, "youtube", "https://www.youtube.com/watch?v=" + id);
  video("https://www.youtube.com/shorts/" + id + "?feature=share", "youtube", "https://www.youtube.com/shorts/" + id);
  notVideo("https://www.youtube.com/", "youtube");
  notVideo("https://www.youtube.com/watch", "youtube");
});

test("Instagram posts, Reels and stories keep existing behavior", () => {
  video("https://www.instagram.com/p/AbC_123/?igsh=tracking", "instagram", "https://www.instagram.com/p/AbC_123/");
  for (const raw of [
    "https://www.instagram.com/reel/AbC_123/",
    "https://www.instagram.com/reels/AbC_123/?igsh=tracking",
    "https://www.instagram.com/example.user/reel/AbC_123/",
  ]) video(raw, "instagram", "https://www.instagram.com/reel/AbC_123/");
  video("https://www.instagram.com/tv/AbC_123/", "instagram", "https://www.instagram.com/tv/AbC_123/");
  video("https://www.instagram.com/stories/example.user/123456/?igsh=tracking", "instagram",
    "https://www.instagram.com/stories/example.user/123456/");
  notVideo("https://www.instagram.com/", "instagram");
  notVideo("https://www.instagram.com/example.user/", "instagram");
});

test("Platform labels include all supported download sources", () => {
  assert.equal(CLIPGO.siteName("bilibili"), "哔哩哔哩");
  assert.equal(CLIPGO.siteName("twitter"), "X");
  assert.equal(CLIPGO.siteName("youtube"), "YouTube");
  assert.equal(CLIPGO.siteName("instagram"), "Instagram");
  assert.equal(CLIPGO.siteName("xiaohongshu"), "小红书");
  assert.equal(CLIPGO.siteName("tiktok"), "TikTok");
  assert.equal(CLIPGO.siteName("douyin"), "抖音");
  assert.equal(CLIPGO.siteName("tencent"), "腾讯视频");
  assert.equal(CLIPGO.siteName("unknown"), "");
});

test("Xiaohongshu notes retain access tokens and their original route", () => {
  const id = "674051740000000007027a15";
  const query = "?xsec_token=a%2Bb%2Fc%3D&xsec_source=pc_feed&source=share";
  for (const route of ["explore", "discovery/item"]) {
    for (const host of ["xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com"]) {
      video("http://" + host + "/" + route + "/" + id + "/" + query + "#comments", "xiaohongshu",
        "https://www.xiaohongshu.com/" + route + "/" + id + query);
    }
  }
  video("https://www.xiaohongshu.com/explore/" + id, "xiaohongshu", "https://www.xiaohongshu.com/explore/" + id);
});

test("Xiaohongshu short links preserve opaque codes and query values", () => {
  for (const code of ["AbCd123", "a/AbCd123", "o/AbCd123", "m/AbCd123"]) {
    video("http://www.xhslink.com/" + code + "/?xsec_token=a%2B%3D&source=share#note", "xiaohongshu",
      "https://xhslink.com/" + code + "?xsec_token=a%2B%3D&source=share");
  }
  notVideo("https://xhslink.com/", "xiaohongshu");
  notVideo("https://xhslink.com/a/", "xiaohongshu");
  notVideo("https://xhslink.com/m/", "xiaohongshu");
  notVideo("https://xhslink.com/a/AbCd123/extra", "xiaohongshu");
  notVideo("https://xhslink.com/m/AbCd123/extra", "xiaohongshu");
});

test("Xiaohongshu feeds, user profiles and malformed notes cannot start downloads", () => {
  for (const route of ["/", "/explore", "/explore/", "/explore/not-a-note", "/explore/abc/extra",
    "/user/profile/674051740000000007027a15", "/discovery/item/", "/search_result?keyword=video"]) {
    notVideo("https://www.xiaohongshu.com" + route, "xiaohongshu");
  }
});

test("TikTok video, mobile, share and embed links normalize to concrete videos", () => {
  const id = "6748451240264420610";
  for (const host of ["tiktok.com", "www.tiktok.com", "m.tiktok.com"]) {
    video("http://" + host + "/@Creator.name_1/video/" + id + "/?is_from_webapp=1#comments", "tiktok",
      "https://www.tiktok.com/@Creator.name_1/video/" + id);
  }
  for (const route of ["share/video", "embed"]) {
    video("https://www.tiktok.com/" + route + "/" + id + "?lang=en", "tiktok",
      "https://www.tiktok.com/@_/video/" + id);
  }
});

test("TikTok vm, vt and t short links retain redirect query values", () => {
  for (const host of ["vm.tiktok.com", "vt.tiktok.com"]) {
    video("http://" + host + "/ZTR45GpSF/?token=a%2Bb&source=share#video", "tiktok",
      "https://" + host + "/ZTR45GpSF?token=a%2Bb&source=share");
    notVideo("https://" + host + "/", "tiktok");
    notVideo("https://" + host + "/ZTR45GpSF/extra", "tiktok");
  }
  video("https://www.tiktok.com/t/ZTRC5xgJp/?_t=abc", "tiktok", "https://www.tiktok.com/t/ZTRC5xgJp?_t=abc");
});

test("TikTok profiles, live streams, collections and non-video links are excluded", () => {
  for (const route of ["/", "/foryou", "/@creator", "/@creator/live", "/@creator/collection/123",
    "/@creator/photo/123", "/@creator/video/0", "/@creator/video/123x", "/@creator/video/123/extra",
    "/share/live/123", "/t/"]) {
    notVideo("https://www.tiktok.com" + route, "tiktok");
  }
});

test("Douyin direct video and selected modal IDs preserve full numeric precision", () => {
  const id = "6961737553342991651";
  for (const host of ["douyin.com", "www.douyin.com", "m.douyin.com"]) {
    video("http://" + host + "/video/" + id + "/?source=share#comments", "douyin", "https://www.douyin.com/video/" + id);
  }
  for (const route of ["/", "/user/someone", "/recommend", "/video/123"]) {
    video("https://www.douyin.com" + route + "?source=share&modal_id=" + id, "douyin",
      "https://www.douyin.com/video/" + id);
  }
});

test("Douyin short links retain redirect queries", () => {
  video("http://v.douyin.com/AbCd_123-/?source=share&token=a%2B#video", "douyin",
    "https://v.douyin.com/AbCd_123-?source=share&token=a%2B");
  notVideo("https://v.douyin.com/", "douyin");
  notVideo("https://v.douyin.com/AbCd123/extra", "douyin");
});

test("Douyin feeds and profiles require a valid selected video ID", () => {
  for (const route of ["/", "/recommend", "/user/someone", "/live/123", "/video/", "/video/0", "/video/123x",
    "/video/123/extra", "/?modal_id=", "/?modal_id=0", "/?modal_id=1.5", "/?modal_id=123abc", "/video/123?modal_id=invalid"]) {
    notVideo("https://www.douyin.com" + route, "douyin");
  }
});

test("Tencent video pages and episodes retain the series route and selected video", () => {
  for (const route of ["page/q326831cny0", "cover/mzc00200p29k31e/s0043cwsgj0"]) {
    for (const suffix of ["", ".html", ".html/"]) {
      video("http://v.qq.com/x/" + route + suffix + "?ptag=share#comments", "tencent",
        "https://v.qq.com/x/" + route + ".html");
    }
  }
});

test("Tencent series, home and malformed paths do not trigger bulk downloads", () => {
  for (const route of ["/", "/channel/tv", "/x/cover/mzc00200p29k31e.html", "/x/cover/mzc00200p29k31e",
    "/x/page/", "/x/page/q326831cny0.html/extra", "/x/page/q326831cny0.php", "/x/cover/cid/vid/extra"]) {
    notVideo("https://v.qq.com" + route, "tencent");
  }
  assert.equal(CLIPGO.detect("https://qq.com/x/page/q326831cny0.html"), null);
  assert.equal(CLIPGO.detect("https://news.qq.com/x/page/q326831cny0.html"), null);
});

test("Copied share text accepts one URL without losing access tokens", () => {
  video("小红书视频分享 http://xhslink.com/a/AbCd123?xsec_token=a%2B%3D，复制后打开小红书", "xiaohongshu",
    "https://xhslink.com/a/AbCd123?xsec_token=a%2B%3D");
  video("复制打开小红书 https://xhslink.com/m/AbCd123?xsec_token=a%2B%3D，查看视频", "xiaohongshu",
    "https://xhslink.com/m/AbCd123?xsec_token=a%2B%3D");
  video("7.83 复制打开抖音，看看视频 https://v.douyin.com/AbCd123/ 11/09", "douyin", "https://v.douyin.com/AbCd123");
  video("https://v.douyin.com/AbCd123/ 复制后打开抖音", "douyin", "https://v.douyin.com/AbCd123");
  video("Watch this: https://www.tiktok.com/@creator/video/6748451240264420610.", "tiktok",
    "https://www.tiktok.com/@creator/video/6748451240264420610");
  assert.equal(CLIPGO.detect("视频 https://v.douyin.com/AbCd123/ 另一个 https://xhslink.com/a/EfGh456"), null);
  assert.equal(CLIPGO.detect("https://v.douyin.com/AbCd123/ https://xhslink.com/a/EfGh456"), null);
});

test("New sites reject lookalike hosts and unrelated subdomains", () => {
  for (const raw of [
    "https://www.xiaohongshu.com.evil.example/explore/674051740000000007027a15",
    "https://notxiaohongshu.com/explore/674051740000000007027a15",
    "https://xhslink.com.evil.example/a/AbCd123",
    "https://www.tiktok.com.evil.example/@creator/video/123",
    "https://api.tiktok.com/@creator/video/123",
    "https://v.douyin.com.evil.example/AbCd123",
    "https://notdouyin.com/video/123",
    "https://live.douyin.com/123",
    "https://v.qq.com.evil.example/x/page/q326831cny0.html",
    "https://v.qq.com@evil.example/x/page/q326831cny0.html",
  ]) assert.equal(CLIPGO.detect(raw), null, raw);
});

test("Manifest enables new sites without requesting all QQ domains", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../extension/manifest.json"), "utf8"));
  for (const domain of ["*.xiaohongshu.com", "*.xhslink.com", "*.tiktok.com", "*.douyin.com", "v.qq.com"]) {
    assert.ok(manifest.host_permissions.includes("*://" + domain + "/*"), domain);
  }
  const matches = manifest.content_scripts.flatMap((script) => script.matches);
  for (const domain of ["*.xiaohongshu.com", "*.tiktok.com", "*.douyin.com", "v.qq.com"]) {
    assert.ok(matches.includes("*://" + domain + "/*"), domain);
  }
  assert.ok(!manifest.host_permissions.includes("*://*.qq.com/*"));
  assert.ok(!manifest.host_permissions.includes("<all_urls>"));
});

test("Readable titles retain Chinese and emoji while rejecting replacement characters", () => {
  assert.equal(CLIPGO.readableTitle("  香妃蛋糕也太香了吧🔥  "), "香妃蛋糕也太香了吧🔥");
  assert.equal(CLIPGO.readableTitle("旅行 · 第 1 P 🏞️"), "旅行 · 第 1 P 🏞️");
  assert.equal(CLIPGO.readableTitle("破损\uFFFD标题"), "");
  assert.equal(CLIPGO.readableTitle("  "), "");
  assert.equal(CLIPGO.readableTitle(null), "");
  assert.equal(CLIPGO.readableTitle(undefined), "");
  assert.equal(CLIPGO.readableTitle({ title: "对象不是标题" }), "");
});

test("File titles strip Windows or Unix directories, final extensions and download IDs", () => {
  assert.equal(CLIPGO.titleFromFiles(["C:\\Users\\名字\\Downloads\\ClipGo\\旅行 🏞️ [BV1xx411c7mD].mp4"]), "旅行 🏞️");
  assert.equal(CLIPGO.titleFromFiles(["/Users/name/Downloads/演示 [123456789].mkv"]), "演示");
  assert.equal(CLIPGO.titleFromFiles(["C:/Downloads/第一章.v2 [id_1].mp4"]), "第一章.v2");
  assert.equal(CLIPGO.titleFromFiles(["/tmp/普通标题.mp4"]), "普通标题");
  assert.equal(CLIPGO.titleFromFiles(["/tmp/带 [括号] 的标题 [id].mp4"]), "带 [括号] 的标题");
});

test("File title recovery skips broken filenames and never exposes a raw path", () => {
  assert.equal(CLIPGO.titleFromFiles(["C:\\Downloads\\破损\uFFFD [id].mp4", "/tmp/完整标题 ✅ [id2].mp4"]), "完整标题 ✅");
  assert.equal(CLIPGO.titleFromFiles(["C:\\目录\uFFFD\\完整标题 [id].mp4"]), "完整标题");
  assert.equal(CLIPGO.titleFromFiles([null, "", "C:\\Downloads\\", "/tmp/\uFFFD.mp4"]), "");
  assert.equal(CLIPGO.titleFromFiles(null), "");
  assert.equal(CLIPGO.titleFromFiles("C:\\Downloads\\标题.mp4"), "");
});

test("Task titles prefer readable metadata, then filenames, then the source URL", () => {
  const url = "https://www.bilibili.com/video/BV1xx411c7mD";
  assert.equal(CLIPGO.taskTitle({ title: "  原始标题 🎬  ", files: ["/tmp/文件标题 [id].mp4"], url }), "原始标题 🎬");
  assert.equal(CLIPGO.taskTitle({ title: "\uFFFD\uFFFD\uFFFD", files: ["C:\\Downloads\\正常中文 [id].mp4"], url }), "正常中文");
  assert.equal(CLIPGO.taskTitle({ title: "\uFFFD", files: ["C:\\Downloads\\\uFFFD [id].mp4"], url }), url);
  assert.equal(CLIPGO.taskTitle({ title: "", files: [], url }), url);
  assert.equal(CLIPGO.taskTitle(null), "");
});
