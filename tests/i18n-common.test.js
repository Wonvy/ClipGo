const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../extension/common.js"), "utf8");
const messages = JSON.parse(readFileSync(path.join(__dirname, "../i18n/background.json"), "utf8"));
const locales = ["zh_CN", "zh_TW", "en", "ja", "ko"];

function common(getMessage) {
  const context = vm.createContext({ URL, ...(getMessage ? { chrome: { i18n: { getMessage } } } : {}) });
  vm.runInContext(source, context);
  return context.CLIPGO;
}

function translated(locale) {
  return common((key, values) => (messages[key]?.[locale] || "").replace(/\$([1-9]\d*)/g, (match, index) => values[Number(index) - 1] ?? match));
}

test("translation falls back without Chrome, for missing messages, or after API invalidation", () => {
  for (const api of [undefined, () => "", () => { throw new Error("Extension context invalidated"); }]) {
    const shared = common(api);
    assert.equal(shared.t("missing", "第 $1 项 / $2 / $3", [0, "$&视频"]), "第 0 项 / $&视频 / $3");
    assert.equal(shared.QUALITIES[0].label, "1080p（推荐）");
    assert.equal(shared.siteName("tencent"), "腾讯视频");
  }
});

test("translation uses Chrome's returned message without substituting user text twice", () => {
  let received;
  const shared = common((key, values) => {
    received = { key, values: [...values] };
    return "Translated value: $1";
  });
  assert.equal(shared.t("test", "中文 $1", ["$1"]), "Translated value: $1");
  assert.deepEqual(received, { key: "test", values: ["$1"] });
});

test("every common/background message has five translations with matching positional placeholders", () => {
  for (const [key, variants] of Object.entries(messages)) {
    assert.deepEqual(Object.keys(variants).sort(), [...locales].sort(), key);
    const placeholders = (value) => (value.match(/\$[1-9]\d*/g) || []).sort();
    for (const locale of locales) {
      assert.equal(typeof variants[locale], "string", `${key}/${locale}`);
      assert.ok(variants[locale].trim(), `${key}/${locale}`);
      assert.deepEqual(placeholders(variants[locale]), placeholders(variants.zh_CN), `${key}/${locale}`);
    }
  }
});

test("common and background translation calls all have catalog entries", () => {
  const background = readFileSync(path.join(__dirname, "../extension/background.js"), "utf8");
  for (const match of (source + background).matchAll(/(?:this|CLIPGO)\.t\("([^"]+)"/g)) {
    assert.ok(messages[match[1]], match[1]);
  }
});

test("quality labels, site names, host stages, and old task messages use each requested locale", () => {
  for (const locale of locales) {
    const shared = translated(locale);
    assert.equal(shared.QUALITIES[0].label, messages.common_quality_recommended[locale]);
    assert.equal(shared.QUALITIES[1].label, messages.common_quality_best[locale]);
    assert.equal(shared.siteName("tencent"), messages.common_site_tencent[locale]);
    for (const key of ["common_stage_parsing", "common_stage_merging", "common_note_exists", "common_host_missing_engine", "bg_interrupted"]) {
      assert.equal(shared.localizeMessage(messages[key].zh_CN), messages[key][locale], `${locale}/${key}`);
    }
    assert.equal(shared.localizeMessage("助手内部错误: 原始诊断 $1"), messages.common_host_internal[locale].replace("$1", () => "原始诊断 $1"));
    assert.equal(shared.localizeMessage("助手内部错误: 下载器返回了无效的文本字段"), messages.common_host_internal[locale].replace("$1", messages.common_host_invalid_text[locale]));
    assert.equal(shared.localizeMessage("已更新到 2026.10.26"), messages.common_updated[locale].replace("$1", "2026.10.26"));
    assert.equal(shared.localizeMessage("小红书暂时限制了请求，请稍后重试"), messages.bg_site_rate_limit[locale].replace("$1", messages.common_site_xiaohongshu[locale]));
  }
});

test("legacy localization leaves unknown diagnostics and partial text matches intact", () => {
  const shared = translated("en");
  for (const text of ["网络服务的新错误", "图解：解析视频信息…", "原始错误: 合并音视频为 MP4…", "未知站点暂时限制了请求，请稍后重试", "https://example.com/video?name=中文"]) {
    assert.equal(shared.localizeMessage(text), text);
  }
  assert.equal(shared.localizeMessage(""), "");
});
