// MAIN-world bridge: observe only normal same-origin video JSON responses.
// It neither requests APIs nor changes the responses seen by the website.
(() => {
  if (!/(^|\.)douyin\.com$/.test(location.hostname) || window.__clipgoDouyinMediaBridge) return;
  const parse = globalThis.CLIPGO_DOUYIN?.parse;
  if (typeof parse !== "function") return;
  window.__clipgoDouyinMediaBridge = true;

  const MAX_BYTES = 2 * 1024 * 1024;
  const cache = new Map();
  const origin = location.origin;
  const jsonType = (type) => /^(?:application|text)\/(?:[\w.+-]*\+)?json\b/i.test(type || "");
  function videoApi(raw) {
    try {
      const url = new URL(raw, location.href);
      return url.origin === origin && !url.username && !url.password
        && /^\/aweme\/v\d+\/(?:web\/)?(?:[\w-]+\/)*(?:detail|feed|post|favorite|related|aweme)\/?$/.test(url.pathname);
    } catch (_) {
      return false;
    }
  }

  function publish(data) {
    const records = parse(data);
    if (!records.length) return;
    for (const item of records) {
      cache.delete(item.id);
      cache.set(item.id, item);
      if (cache.size > 100) cache.delete(cache.keys().next().value);
    }
    window.postMessage({ type: "clipgo:douyin-media", records }, origin);
  }

  async function inspect(response) {
    try {
      if (!response || !videoApi(response.url) || !jsonType(response.headers.get("content-type"))) return;
      if (Number(response.headers.get("content-length")) > MAX_BYTES) return;
      const reader = response.clone().body?.getReader();
      if (!reader) return;
      const decoder = new TextDecoder();
      let size = 0;
      let text = "";
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > MAX_BYTES) { reader.cancel().catch(() => {}); return; }
          text += decoder.decode(value, { stream: true });
        }
        text += decoder.decode();
      } finally {
        reader.releaseLock();
      }
      publish(text);
    } catch (_) { /* Observing a response must never affect page requests. */ }
  }

  if (typeof window.fetch === "function") {
    const fetch = window.fetch;
    window.fetch = function (...args) {
      const result = fetch.apply(this, args);
      Promise.resolve(result).then(inspect).catch(() => {});
      return result;
    };
  }

  if (typeof window.XMLHttpRequest === "function") {
    const prototype = window.XMLHttpRequest.prototype;
    const open = prototype.open;
    const send = prototype.send;
    const requests = new WeakMap();
    const listening = new WeakSet();
    prototype.open = function (method, url, ...args) {
      const result = open.call(this, method, url, ...args);
      try { requests.set(this, videoApi(url)); } catch (_) { requests.set(this, false); }
      return result;
    };
    prototype.send = function (...args) {
      if (!listening.has(this)) {
        listening.add(this);
        this.addEventListener("load", () => {
          try {
            if (!requests.get(this) || !videoApi(this.responseURL) || !jsonType(this.getResponseHeader("content-type"))) return;
            if (Number(this.getResponseHeader("content-length")) > MAX_BYTES) return;
            if (this.responseType === "json") publish(this.response);
            else if ((!this.responseType || this.responseType === "text") && this.responseText.length <= MAX_BYTES) publish(this.responseText);
          } catch (_) { /* Ignore non-JSON/invalid responses. */ }
        });
      }
      return send.apply(this, args);
    };
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== origin || event.data?.type !== "clipgo:douyin-media-request") return;
    if (cache.size) window.postMessage({ type: "clipgo:douyin-media", records: [...cache.values()] }, origin);
  });
})();
