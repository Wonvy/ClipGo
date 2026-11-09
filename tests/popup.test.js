const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'extension/popup.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'extension/popup.js'), 'utf8');
const messages = JSON.parse(fs.readFileSync(path.join(root, 'i18n/popup.json'), 'utf8'));
const locales = ['zh_CN', 'zh_TW', 'en', 'ja', 'ko'];

class Element {
  constructor(tag, attributes = {}, text = '') {
    this.tag = tag;
    this.attributes = attributes;
    this.children = [];
    this.listeners = {};
    this.textContent = text;
    this.value = '';
    this.className = attributes.class || '';
    this.style = {};
    this.classList = { add: name => { this.className += ' ' + name; } };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Translations must not write HTML'); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); }
  querySelector() { return this.span; }
}

async function popup(locale = 'en', options = {}) {
  const nodes = [];
  const ids = new Map();
  for (const match of html.matchAll(/<([a-z][\w-]*)([^>]*)>([^<]*)/gi)) {
    const attrs = Object.fromEntries(Array.from(match[2].matchAll(/([\w-]+)="([^"]*)"/g), m => [m[1], m[2]]));
    const element = new Element(match[1], attrs, match[3]);
    nodes.push(element);
    if (attrs.id) ids.set(attrs.id, element);
  }
  ids.get('helperStatus').span = nodes.find(node => node.attributes['data-i18n'] === 'popup_helper_checking');
  const document = {
    documentElement: nodes.find(node => node.tag === 'html'),
    getElementById: id => ids.get(id),
    createElement: tag => new Element(tag),
    querySelectorAll: selector => nodes.filter(node => node.getAttribute(selector.slice(1, -1)) !== null),
  };
  const calls = [];
  const localized = [];
  const translate = (key, fallback, substitutions = []) => {
    const text = options.noApi ? fallback : options.translations?.[key] ?? messages[key]?.[locale] ?? fallback;
    return text.replace(/\$(\d+)/g, (_, index) => String(substitutions[Number(index) - 1] ?? ''));
  };
  const chrome = {
    runtime: { sendMessage: async message => {
      calls.push(message);
      if (message.payload?.action === 'ping') return options.helper || { type: 'pong', ytdlp: '2026.10.26', version: '1.3.0', ffmpeg: true, deno: true, downloadDir: 'Downloads' };
      if (message.payload?.action === 'chooseDir') return { downloadDir: 'Selected folder' };
      return { ok: true };
    } },
    storage: { local: { get: async () => ({ settings: {}, tasks: [] }), set: async () => {} }, onChanged: { addListener() {} } },
    tabs: { query: async () => options.tabs || [] },
  };
  if (!options.noApi) chrome.i18n = { getUILanguage: () => locale };
  const context = vm.createContext({
    document, chrome, navigator: { language: options.navigatorLanguage || locale },
    CLIPGO: {
      t: translate,
      localizeMessage: text => { localized.push(text); return options.localize?.(text) ?? text; },
      DEFAULT_SETTINGS: { quality: '1080' }, QUALITIES: [{ value: '1080', label: '1080p' }],
      detect: () => null, siteName: site => site, taskTitle: task => task.title || task.url,
    },
  });
  vm.runInContext(script, context, { filename: 'popup.js' });
  await new Promise(setImmediate);
  return { ids, nodes, document, context, calls, localized, translate };
}

test('popup catalog covers every used key in all five languages with matching placeholders', () => {
  const keys = new Set((html + script).match(/popup_[a-z_]+/g));
  assert.deepEqual([...keys].sort(), Object.keys(messages).sort());
  for (const [key, translations] of Object.entries(messages)) {
    assert.deepEqual(Object.keys(translations).sort(), [...locales].sort(), key);
    const placeholders = translations.zh_CN.match(/\$\d+/g) || [];
    for (const locale of locales) {
      assert.ok(translations[locale].trim(), `${key}/${locale}`);
      assert.deepEqual((translations[locale].match(/\$\d+/g) || []).sort(), [...placeholders].sort(), `${key}/${locale}`);
    }
  }
});

for (const locale of locales) {
  test(`popup boots in ${locale}, including accessible labels and helper versions`, async () => {
    const ui = await popup(locale);
    assert.equal(ui.document.documentElement.lang, locale.replace('_', '-'));
    assert.equal(ui.ids.get('download').textContent, messages.popup_download[locale]);
    assert.equal(ui.ids.get('url').getAttribute('placeholder'), messages.popup_url_placeholder[locale]);
    assert.equal(ui.ids.get('url').getAttribute('aria-label'), messages.popup_url_label[locale]);
    assert.equal(ui.ids.get('helperStatus').span.textContent, messages.popup_helper_ready[locale]);
    assert.equal(ui.ids.get('helperStatus').title, ui.translate('popup_helper_version', '', ['1.3.0', '2026.10.26']));
    assert.ok(ui.nodes.some(node => node.tag === 'code' && node.textContent === 'bash host/install-mac.sh'));
  });
}

test('missing i18n API retains Chinese fallback and uses the browser language for the folder dialog', async () => {
  const ui = await popup('en', { noApi: true, navigatorLanguage: 'ja-JP' });
  assert.equal(ui.ids.get('download').textContent, messages.popup_download.zh_CN);
  assert.equal(ui.document.documentElement.lang, 'ja-JP');
  await ui.ids.get('chooseDir').listeners.click();
  assert.equal(ui.calls.at(-1).payload.locale, 'ja-JP');
  assert.equal(ui.ids.get('dir').textContent, 'Selected folder');
});

test('known task and helper messages pass through shared localization, while unknown errors remain intact', async () => {
  const known = { '准备视频': 'Preparing video', '已存在': 'Already saved', '本地助手无响应': 'Helper did not respond' };
  const ui = await popup('en', { localize: text => known[text] ?? text, helper: { type: 'error', message: '本地助手无响应' } });
  assert.equal(ui.ids.get('setupDetail').textContent, 'Helper did not respond');
  assert.equal(ui.context.fmtMeta({ status: 'queued', stage: '准备视频' })[0], 'Preparing video');
  assert.equal(ui.context.fmtMeta({ status: 'done', note: '已存在' })[0], 'Already saved');
  assert.equal(ui.context.fmtMeta({ status: 'error', error: 'HTTP Error 403: Forbidden' })[0], '✕ HTTP Error 403: Forbidden');
  assert.equal(ui.context.fmtMeta({ status: 'running', percent: 10, eta: '00:30' })[1], '00:30 remaining');
});

test('translated text is never parsed as HTML', async () => {
  const literal = '<img src=x onerror=alert(1)>';
  const ui = await popup('en', { translations: { popup_download: literal } });
  assert.equal(ui.ids.get('download').textContent, literal);
  assert.equal(ui.ids.get('download').children.length, 0);
});
