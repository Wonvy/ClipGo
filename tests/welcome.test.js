const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..', 'packaging', 'windows');
const source = fs.readFileSync(path.join(root, 'welcome.html'), 'utf8');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'welcome-locales.json'), 'utf8'));
const embedded = source.match(/<script type="application\/json" id="welcome-locales">([\s\S]*?)<\/script>/)[1];
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
const locales = ['zh_CN', 'zh_TW', 'en', 'ja', 'ko'];
const escapeHtml = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const unescapeHtml = value => value.replaceAll('&quot;', '"').replaceAll('&gt;', '>').replaceAll('&lt;', '<').replaceAll('&amp;', '&');

function guide(language = 'en', options = {}) {
  const installedPath = options.path || 'C:\\ClipGo\\extension';
  const html = source.replace('{{EXTENSION_PATH}}', escapeHtml(installedPath));
  const elements = [];
  const ids = new Map();
  const copied = [];
  const timers = [];
  let selected = null;
  for (const match of html.matchAll(/<([a-z][\w-]*)([^>]*)>([^<]*)/gi)) {
    const attrs = Object.fromEntries(Array.from(match[2].matchAll(/([\w-]+)="([^"]*)"/g), m => [m[1], m[2]]));
    const element = {
      tag: match[1], attrs, listeners: {}, textContent: unescapeHtml(match[3]),
      dataset: Object.fromEntries(Object.entries(attrs).filter(([name]) => name.startsWith('data-')).map(([name, value]) => [name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value])),
      setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      focus() {}, scrollIntoView() {},
      set innerHTML(_) { throw new Error('Translations must not write HTML'); },
    };
    elements.push(element);
    if (attrs.id) ids.set(attrs.id, element);
  }
  ids.get('welcome-locales').textContent = embedded;
  const document = {
    documentElement: elements.find(element => element.tag === 'html'),
    getElementById: id => ids.get(id),
    querySelectorAll: selector => elements.filter(element => Object.hasOwn(element.attrs, selector.slice(1, -1))),
    createRange: () => ({ selectNodeContents: target => { selected = target; } }),
  };
  const navigator = { language };
  if (!options.clipboardFailure) navigator.clipboard = { writeText: async text => { copied.push(text); } };
  vm.runInNewContext(script, {
    document, navigator,
    window: { getSelection: () => options.noSelection ? null : { removeAllRanges() {}, addRange() {} } },
    clearTimeout() {}, setTimeout: callback => { timers.push(callback); return timers.length; },
  }, { filename: 'welcome.html' });
  return { ids, elements, document, copied, timers, get selected() { return selected; } };
}

test('welcome page embeds its complete five-language catalog and keeps the path outside scripts', () => {
  assert.deepEqual(JSON.parse(embedded), catalog);
  assert.equal((source.match(/\{\{EXTENSION_PATH\}\}/g) || []).length, 1);
  assert.match(source, /<code id="extension-path"[^>]*>\{\{EXTENSION_PATH\}\}<\/code>/);
  assert.ok(!script.includes('{{EXTENSION_PATH}}'));
  assert.ok(!/<(?:script|link)[^>]+(?:src|href)=/i.test(source));
  const keys = new Set([
    ...Array.from(source.matchAll(/data-(?:i18n(?:-aria-label)?|copy-name)="([^"]+)"/g), m => m[1]),
    ...Array.from(script.matchAll(/text\("([^"]+)"/g), m => m[1]),
  ]);
  assert.deepEqual([...keys].sort(), Object.keys(catalog).sort());
  for (const [key, translations] of Object.entries(catalog)) {
    assert.deepEqual(Object.keys(translations).sort(), [...locales].sort(), key);
    for (const locale of locales) {
      assert.ok(translations[locale].trim());
      assert.deepEqual(translations[locale].match(/\$\d+/g) || [], translations.zh_CN.match(/\$\d+/g) || [], `${key}/${locale}`);
    }
  }
});

for (const locale of locales) {
  test(`welcome page selects ${locale} and preserves native language options`, () => {
    const ui = guide(locale);
    assert.equal(ui.document.documentElement.lang, locale.replace('_', '-'));
    assert.equal(ui.ids.get('language').value, locale);
    assert.equal(ui.ids.get('folder-title').textContent, catalog.folder_title[locale]);
    assert.equal(ui.ids.get('extension-path').attrs['aria-label'], catalog.path_label[locale]);
    assert.deepEqual(ui.elements.filter(element => element.tag === 'option').map(element => element.textContent), ['简体中文', '繁體中文', 'English', '日本語', '한국어']);
  });
}

test('regional browser languages select the expected translation', () => {
  for (const [language, expected] of Object.entries({ 'zh-HK': 'zh_TW', 'zh-Hant': 'zh_TW', 'zh-SG': 'zh_CN', 'ja-JP': 'ja', 'ko-KR': 'ko', 'en-GB': 'en', 'fr-FR': 'en' })) {
    assert.equal(guide(language).ids.get('language').value, expected, language);
  }
});

test('switching languages never changes or executes a Chinese path with HTML-sensitive characters', async () => {
  const installedPath = 'C:\\测试 & 空格\\"ClipGo" <folder>\\extension';
  const ui = guide('en', { path: installedPath });
  const button = ui.elements.find(element => element.dataset.copyTarget === 'extension-path');
  for (const locale of locales) {
    ui.ids.get('language').value = locale;
    ui.ids.get('language').listeners.change();
    assert.equal(ui.ids.get('extension-path').textContent, installedPath);
    await button.listeners.click();
    assert.equal(button.textContent, catalog.copied[locale]);
    assert.equal(ui.copied.at(-1), installedPath);
  }
  ui.ids.get('language').value = 'en';
  ui.ids.get('language').listeners.change();
  ui.timers.at(-1)();
  assert.equal(button.textContent, catalog.copy_path.en);
});

test('clipboard failure selects the exact path and gives a localized keyboard fallback', async () => {
  const ui = guide('ja', { clipboardFailure: true });
  const button = ui.elements.find(element => element.dataset.copyTarget === 'extension-path');
  await button.listeners.click();
  assert.equal(ui.selected, ui.ids.get('extension-path'));
  assert.equal(button.textContent, catalog.copy_shortcut.ja);
  assert.equal(ui.ids.get('copy-status').textContent, catalog.copy_selected.ja.replace('$1', catalog.path_name.ja));
  assert.equal(button.disabled, false);
});

test('clipboard and selection unavailability retain the manual-copy fallback', async () => {
  const ui = guide('ko', { clipboardFailure: true, noSelection: true });
  const button = ui.elements.find(element => element.dataset.copyTarget === 'chrome-address');
  await button.listeners.click();
  assert.equal(ui.ids.get('copy-status').textContent, catalog.copy_manual.ko.replace('$1', catalog.chrome_address_name.ko));
  assert.equal(ui.ids.get('chrome-address').textContent, 'chrome://extensions');
});
