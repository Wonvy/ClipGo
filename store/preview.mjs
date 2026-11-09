#!/usr/bin/env node
// Local screenshot preview. No extension installation, accounts, or native host.
// Run: node store/preview.mjs [--port 8765]
// Open: http://127.0.0.1:8765/showcase?locale=en
// Popup: /popup?locale=zh_TW&expanded=1
import http from "node:http";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const extension = resolve(dirname(fileURLToPath(import.meta.url)), "../extension");
const locales = new Set(["en", "ja", "ko", "zh_CN", "zh_TW"]);
const languageNames = { en: "English", ja: "日本語", ko: "한국어", zh_CN: "简体中文", zh_TW: "繁體中文" };
const staticFiles = new Map([
  ["/popup.css", ["popup.css", "text/css; charset=utf-8"]],
  ["/common.js", ["common.js", "text/javascript; charset=utf-8"]],
  ["/popup.js", ["popup.js", "text/javascript; charset=utf-8"]],
  ...[16, 32, 48, 128].map((size) => [`/icons/icon${size}.png`, [`icons/icon${size}.png`, "image/png"]]),
]);
const copy = {
  en: {
    label: "BROWSER EXTENSION", heading: "Your videos.\nSaved as MP4.",
    body: "Paste a video link, choose the quality, and keep your downloads in one place.",
    quality: "Choose the quality", platforms: "Multiple video platforms", languages: "Five interface languages",
    helper: "Requires the ClipGo desktop helper. Availability and quality depend on the source and your viewing access.",
    sample: "Interface preview · Sample helper status · No downloads run here",
    blocked: "This is a local interface preview. Downloads and desktop actions are disabled.", folder: "Downloads / ClipGo (sample)",
  },
  ja: {
    label: "ブラウザ拡張機能", heading: "動画を、\nMP4で保存。",
    body: "動画リンクを貼り付けて画質を選択。ダウンロードを一か所で管理できます。",
    quality: "画質を選択", platforms: "複数の動画サービスに対応", languages: "5言語のインターフェース",
    helper: "ClipGo デスクトップヘルパーが必要です。保存の可否と画質は、配信元と視聴権限によって異なります。",
    sample: "画面プレビュー · ヘルパーの状態はサンプル · ダウンロードは実行されません",
    blocked: "これはローカルの画面プレビューです。ダウンロードとデスクトップ操作は無効です。", folder: "Downloads / ClipGo（サンプル）",
  },
  ko: {
    label: "브라우저 확장 프로그램", heading: "동영상을\nMP4로 저장하세요.",
    body: "동영상 링크를 붙여 넣고 화질을 선택하세요. 다운로드를 한곳에서 관리할 수 있습니다.",
    quality: "화질 선택", platforms: "다양한 동영상 플랫폼 지원", languages: "5개 언어 인터페이스",
    helper: "ClipGo 데스크톱 도우미가 필요합니다. 저장 가능 여부와 화질은 원본 및 시청 권한에 따라 달라집니다.",
    sample: "화면 미리보기 · 도우미 상태는 예시 · 실제 다운로드 없음",
    blocked: "로컬 화면 미리보기입니다. 다운로드 및 데스크톱 작업은 비활성화되어 있습니다.", folder: "Downloads / ClipGo (예시)",
  },
  zh_CN: {
    label: "浏览器扩展", heading: "喜欢的视频，\n保存为 MP4。",
    body: "粘贴视频链接，选择画质，在同一个地方管理下载。",
    quality: "自由选择画质", platforms: "支持多个视频平台", languages: "提供五种界面语言",
    helper: "需要安装 ClipGo 本地助手。能否保存及可用画质取决于视频来源和账号观看权限。",
    sample: "界面预览 · 助手状态为示例 · 此处不会执行下载",
    blocked: "这是本地界面预览，下载和桌面操作已禁用。", folder: "Downloads / ClipGo（示例）",
  },
  zh_TW: {
    label: "瀏覽器擴充功能", heading: "喜歡的影片，\n儲存為 MP4。",
    body: "貼上影片連結，選擇畫質，在同一個地方管理下載。",
    quality: "自由選擇畫質", platforms: "支援多個影片平台", languages: "提供五種介面語言",
    helper: "需要安裝 ClipGo 本機助手。能否儲存及可用畫質取決於影片來源和帳號觀看權限。",
    sample: "介面預覽 · 助手狀態為範例 · 此處不會執行下載",
    blocked: "這是本機介面預覽，下載和桌面操作已停用。", folder: "Downloads / ClipGo（範例）",
  },
};

const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const asScript = (value) => JSON.stringify(value).replace(/</g, "\\u003c").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");

async function mockScript(locale, expanded) {
  // Read the generated Chrome catalogs, not a second set of UI translations.
  const messages = JSON.parse(await readFile(join(extension, "_locales", locale, "messages.json"), "utf8"));
  return String.raw`(() => {
    const messages = ${asScript(messages)};
    const locale = ${asScript(locale.replace("_", "-"))};
    const blocked = ${asScript(copy[locale].blocked)};
    const state = { settings: { quality: "1080", ytCookies: false, notify: true, quickButton: true }, tasks: [] };
    const listeners = new Set();
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const getMessage = (key, substitutions = []) => {
      const entry = messages[key];
      if (!entry) return "";
      const values = Array.isArray(substitutions) ? substitutions : [substitutions];
      let message = entry.message.replace(/\$([a-zA-Z_][a-zA-Z0-9_]*)\$/g, (match, name) => {
        const placeholder = Object.entries(entry.placeholders || {}).find(([key]) => key.toLowerCase() === name.toLowerCase());
        return placeholder ? placeholder[1].content : match;
      });
      return message.replace(/\$\$|\$([1-9])/g, (match, index) => index ? String(values[Number(index) - 1] ?? "") : "$" );
    };
    globalThis.chrome = {
      i18n: { getMessage, getUILanguage: () => locale },
      tabs: { query: async () => [], sendMessage: async () => null },
      storage: {
        local: {
          async get(defaults) { return { ...clone(defaults), ...clone(state) }; },
          async set(updates) {
            const changes = {};
            for (const [key, value] of Object.entries(updates)) {
              changes[key] = { oldValue: clone(state[key] ?? null), newValue: clone(value) };
              state[key] = clone(value);
            }
            for (const listener of listeners) listener(changes, "local");
          },
        },
        onChanged: { addListener: (listener) => listeners.add(listener), removeListener: (listener) => listeners.delete(listener) },
      },
      runtime: {
        async sendMessage(message) {
          if (message.cmd === "host" && message.payload?.action === "ping") {
            return { type: "pong", version: "1.3.0", ytdlp: "2026.10.26", ffmpeg: true, deno: true, downloadDir: ${asScript(copy[locale].folder)} };
          }
          if (message.cmd === "clearFinished") return { ok: true };
          return { ok: false, type: "error", message: blocked, error: blocked };
        },
      },
    };
    document.addEventListener("DOMContentLoaded", () => {
      if (${expanded}) document.querySelector("details.settings").open = true;
      document.documentElement.dataset.preview = "true";
    }, { once: true });
  })();`;
}

function showcase(locale, expanded) {
  const text = copy[locale];
  const languages = Object.entries(languageNames).map(([code, name]) => `<span${code === locale ? ' class="selected"' : ""}>${escapeHTML(name)}</span>`).join("");
  return `<!doctype html><html lang="${locale.replace("_", "-")}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ClipGo — ${escapeHTML(text.label)}</title><link rel="stylesheet" href="/showcase.css"></head>
  <body><main class="canvas"><section class="story">
    <div class="brand"><img src="/icons/icon128.png" width="46" height="46" alt=""><span>ClipGo</span></div>
    <div class="eyebrow">${escapeHTML(text.label)}</div>
    <h1>${escapeHTML(text.heading).replace(/\n/g, "<br>")}</h1><p class="intro">${escapeHTML(text.body)}</p>
    <ul><li>${escapeHTML(text.quality)}</li><li>${escapeHTML(text.platforms)}</li><li>${escapeHTML(text.languages)}</li></ul>
    <div class="languages">${languages}</div><p class="helper">${escapeHTML(text.helper)}</p>
  </section><section class="product"><div class="window${expanded ? " expanded" : ""}"><iframe title="ClipGo" src="/popup?locale=${locale}${expanded ? "&amp;expanded=1" : ""}"></iframe></div></section>
  <footer>${escapeHTML(text.sample)}</footer></main></body></html>`;
}

const showcaseCSS = `
  * { box-sizing: border-box; }
  html, body { margin: 0; background: #f5f6fa; color: #171a24; }
  body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC","Yu Gothic UI","Malgun Gothic",sans-serif; }
  .canvas { position: relative; width: 1280px; height: 800px; overflow: hidden; display: grid; grid-template-columns: 600px 1fr; padding: 58px 74px 70px; gap: 58px; background: radial-gradient(ellipse at 87% 42%,#f5e6ee 0,transparent 53%),linear-gradient(130deg,#fbfcff,#f0f2f8); }
  .story { align-self: center; }
  .brand { display: flex; align-items: center; gap: 14px; font-size: 27px; font-weight: 750; letter-spacing: -.7px; }
  .brand img { border-radius: 12px; }
  .eyebrow { font-size: 12px; font-weight: 750; letter-spacing: 2px; color: #b6344a; margin-top: 35px; }
  h1 { font-size: 48px; line-height: 1.17; font-weight: 750; letter-spacing: -1.8px; margin: 17px 0 18px; }
  .intro { font-size: 18px; line-height: 1.65; max-width: 510px; color: #5e6472; margin: 0; }
  ul { display: grid; gap: 11px; list-style: none; padding: 0; margin: 23px 0; font-size: 16px; font-weight: 600; }
  li::before { content: "✓"; color: #c83650; margin-right: 12px; }
  .languages { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 24px; }
  .languages span { padding: 6px 10px; background: #fff8; border: 1px solid #dfe2e9; border-radius: 7px; font-size: 12px; color: #616879; }
  .languages .selected { background: #fbe7ec; border-color: #efc0cb; color: #ab3048; }
  .helper { font-size: 12px; line-height: 1.7; max-width: 505px; margin: 20px 0 0; color: #727887; }
  .product { display: flex; align-items: center; justify-content: center; }
  .window { width: 402px; border: 1px solid #dfe1e8; border-radius: 15px; background: #fff; box-shadow: 0 24px 70px #34344e22,0 3px 12px #34344e0b; overflow: hidden; }
  iframe { display: block; width: 400px; height: 400px; border: 0; color-scheme: light; }
  .expanded iframe { height: 660px; }
  footer { position: absolute; left: 74px; right: 74px; bottom: 22px; color: #818796; font-size: 11px; text-align: center; }
`;

export function createPreviewServer() {
  return http.createServer(async (request, response) => {
    const port = request.socket.localPort;
    if (!["127.0.0.1:" + port, "localhost:" + port].includes(request.headers.host)) {
      response.writeHead(403).end("Local preview only");
      return;
    }
    const headers = {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; frame-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
    };
    const reply = (status, type, body) => response.writeHead(status, { ...headers, "Content-Type": type }).end(body);
    if (!["GET", "HEAD"].includes(request.method)) {
      reply(405, "text/plain; charset=utf-8", "Read-only preview");
      return;
    }
    let url;
    try { url = new URL(request.url, "http://127.0.0.1"); }
    catch (_) { reply(400, "text/plain; charset=utf-8", "Invalid preview URL"); return; }
    const locale = url.searchParams.get("locale") || "en";
    const expanded = url.searchParams.get("expanded") === "1";
    if (!locales.has(locale)) {
      reply(400, "text/plain; charset=utf-8", "Supported locales: en, ja, ko, zh_CN, zh_TW");
      return;
    }
    try {
      if (url.pathname === "/" || url.pathname === "/showcase") {
        reply(200, "text/html; charset=utf-8", showcase(locale, expanded));
      } else if (url.pathname === "/popup") {
        const html = await readFile(join(extension, "popup.html"), "utf8");
        reply(200, "text/html; charset=utf-8", html.replace('<script src="common.js"></script>', `<script src="/preview-api.js?locale=${locale}&amp;expanded=${expanded ? "1" : "0"}"></script>\n  <script src="common.js"></script>`));
      } else if (url.pathname === "/preview-api.js") {
        reply(200, "text/javascript; charset=utf-8", await mockScript(locale, expanded));
      } else if (url.pathname === "/showcase.css") {
        reply(200, "text/css; charset=utf-8", showcaseCSS);
      } else if (staticFiles.has(url.pathname)) {
        const [file, type] = staticFiles.get(url.pathname);
        reply(200, type, await readFile(join(extension, file)));
      } else {
        reply(404, "text/plain; charset=utf-8", "Not found");
      }
    } catch (error) {
      console.error("Preview resource unavailable:", error.code || error.name);
      reply(503, "text/plain; charset=utf-8", "Preview unavailable. Generate the extension locale catalogs first.");
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length && (args.length !== 2 || args[0] !== "--port")) throw new Error("Usage: node store/preview.mjs [--port 8765]");
  const port = Number(args[1] || process.env.PORT || 8765);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Port must be between 1 and 65535");
  const server = createPreviewServer();
  server.on("error", (error) => { console.error("Preview server failed:", error.code || error.name); process.exitCode = 1; });
  server.listen(port, "127.0.0.1", () => {
    console.log(`ClipGo local preview: http://127.0.0.1:${port}/showcase?locale=en`);
    console.log("Uses sample data only; no native helper, account access, or downloads.");
  });
}
