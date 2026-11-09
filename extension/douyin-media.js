// Pure, bounded extraction of already available Douyin playback metadata.
(() => {
  const MAX_INPUT = 2 * 1024 * 1024;
  const MAX_NODES = 10000;
  const MAX_DEPTH = 16;
  const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  function protectedMedia(value) {
    if (!value || typeof value !== "object") return false;
    for (const key of ["playerAccessKey", "player_access_key"]) {
      if (own(value, key) && value[key] !== "" && value[key] != null) return true;
    }
    // Do not interpret encryption metadata or return any of its contents.
    return ["encrypted", "isEncrypted", "is_encrypted", "drm", "drmInfo", "drm_info", "encryptKey", "encrypt_key"]
      .some((key) => own(value, key));
  }

  function playbackUrl(raw) {
    if (typeof raw !== "string" || raw.length > 8192 || /\s/.test(raw)) return null;
    const authority = raw.match(/^https:\/\/([^/?#]+)/i);
    if (!authority || authority[1].includes(":")) return null; // Includes an explicit :443 port.
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
      if (url.hostname !== "douyinvod.com" && !url.hostname.endsWith(".douyinvod.com")) return null;
      return url.href;
    } catch (_) {
      return null;
    }
  }

  function addresses(address) {
    const found = [];
    const queue = [{ value: address, depth: 0 }];
    const seen = new Set();
    for (let i = 0; i < queue.length && i < 64 && found.length < 16; i++) {
      const { value, depth } = queue[i];
      const url = playbackUrl(value);
      if (url) {
        if (!seen.has(url)) { found.push(url); seen.add(url); }
        continue;
      }
      if (!value || typeof value !== "object" || depth >= 4 || protectedMedia(value)) continue;
      const children = Array.isArray(value) ? value.slice(0, 16)
        : [value.src, value.url, value.urlList, value.url_list];
      for (const child of children) {
        if (queue.length >= 64) break;
        if (child != null) queue.push({ value: child, depth: depth + 1 });
      }
    }
    return found;
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
  }

  function h265Flag(value) {
    return own(value, "isH265") ? value.isH265 : value.is_h265;
  }

  function record(node) {
    const rawId = node.awemeId ?? node.aweme_id;
    const id = typeof rawId === "string" ? rawId
      : Number.isSafeInteger(rawId) && rawId > 0 ? String(rawId) : "";
    if (!/^[1-9]\d{0,24}$/.test(id)) return null;
    const video = node.video;
    if (!video || typeof video !== "object" || protectedMedia(video)) return null;
    const flag = h265Flag(video);
    const defaults = flag == null || flag === 0 ? addresses(video.playAddr ?? video.play_addr) : [];
    const formats = [];
    const seen = new Set();
    function add(url, source) {
      if (formats.length >= 16 || seen.has(url)) return;
      seen.add(url);
      formats.push({ url, width: number(source.width) || number(video.width), height: number(source.height) || number(video.height),
        bitrate: number(source.bitRate ?? source.bit_rate ?? source.bitrate) });
    }
    const rates = video.bitRateList ?? video.bit_rate_list ?? video.bit_rate;
    if (Array.isArray(rates)) {
      const candidates = rates.slice(0, 64).filter((rate) => rate && typeof rate === "object"
        && h265Flag(rate) === 0 && !protectedMedia(rate));
      candidates.sort((a, b) => (number(b.height) * number(b.width) - number(a.height) * number(a.width))
        || number(b.bitRate ?? b.bit_rate) - number(a.bitRate ?? a.bit_rate));
      for (const rate of candidates) {
        // Alternate CDNs for the same quality do not need separate format slots.
        const url = addresses(rate.playAddr ?? rate.play_addr)[0];
        if (url) add(url, rate);
      }
    }
    const url = defaults[0] || formats[0]?.url;
    if (!url) return null;
    if (defaults.length && !seen.has(url)) {
      if (formats.length >= 16) formats.pop();
      add(url, video);
    }
    const title = typeof node.desc === "string" ? node.desc : typeof node.title === "string" ? node.title : "";
    return { id, title: title.replace(/\s+/g, " ").trim().slice(0, 300), url, formats };
  }

  function parse(data) {
    if (typeof data === "string") {
      if (data.length > MAX_INPUT) return [];
      try { data = JSON.parse(data); }
      catch (_) {
        try { data = JSON.parse(decodeURIComponent(data)); }
        catch (_) { return []; }
      }
    }
    if (!data || typeof data !== "object") return [];
    const queue = [{ value: data, depth: 0 }];
    const seen = new WeakSet();
    const records = new Map();
    let examined = 0;
    for (let i = 0; i < queue.length && i < MAX_NODES && records.size < 100; i++) {
      const { value, depth } = queue[i];
      if (!value || typeof value !== "object" || seen.has(value) || protectedMedia(value)) continue;
      seen.add(value);
      const item = record(value);
      if (item && !records.has(item.id)) records.set(item.id, item);
      if (depth >= MAX_DEPTH) continue;
      for (const key in value) {
        if (!own(value, key)) continue;
        if (++examined > MAX_NODES || queue.length >= MAX_NODES) break;
        const child = value[key];
        if (child && typeof child === "object") queue.push({ value: child, depth: depth + 1 });
      }
      if (examined > MAX_NODES) break;
    }
    return [...records.values()];
  }

  globalThis.CLIPGO_DOUYIN = Object.freeze({ parse });
})();
