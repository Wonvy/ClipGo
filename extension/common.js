// Shared by background.js (importScripts) and popup.js.
/* exported CLIPGO */
var CLIPGO = {
  HOST: "com.clipgo.host",

  QUALITIES: [
    { value: "1080", label: "1080p（推荐）" },
    { value: "best", label: "最高画质 (4K)" },
    { value: "720", label: "720p" },
    { value: "480", label: "480p" },
    { value: "360", label: "360p" },
  ],

  DEFAULT_SETTINGS: { quality: "1080", ytCookies: false, notify: true, quickButton: true },

  /**
   * Work out whether a URL is a downloadable YouTube / Instagram video.
   * Returns null for other sites, or { site, isVideo, url } where url is
   * a cleaned-up canonical link.
   */
  detect(raw) {
    let u;
    try {
      u = new URL(String(raw || "").trim());
    } catch (e) {
      return null;
    }
    if (!/^https?:$/.test(u.protocol)) return null;
    const host = u.hostname.toLowerCase().replace(/^(www|m|music)\./, "");
    const path = u.pathname;

    if (host === "youtube.com") {
      const v = u.searchParams.get("v");
      if (path === "/watch" && v) {
        return { site: "youtube", isVideo: true, url: "https://www.youtube.com/watch?v=" + v };
      }
      const m = path.match(/^\/(shorts|live|embed|v)\/([\w-]{6,})/);
      if (m) {
        const url = m[1] === "shorts"
          ? "https://www.youtube.com/shorts/" + m[2]
          : "https://www.youtube.com/watch?v=" + m[2];
        return { site: "youtube", isVideo: true, url };
      }
      return { site: "youtube", isVideo: false };
    }

    if (host === "youtu.be") {
      const id = path.slice(1).split("/")[0];
      if (id) return { site: "youtube", isVideo: true, url: "https://www.youtube.com/watch?v=" + id };
      return null;
    }

    if (host === "instagram.com") {
      const m = path.match(/^\/(?:[\w.]+\/)?(p|reel|reels|tv)\/([\w-]+)/);
      if (m) {
        const kind = m[1] === "reels" ? "reel" : m[1];
        return { site: "instagram", isVideo: true, url: "https://www.instagram.com/" + kind + "/" + m[2] + "/" };
      }
      const s = path.match(/^\/stories\/([\w.]+)\/(\d+)/);
      if (s) {
        return { site: "instagram", isVideo: true, url: "https://www.instagram.com/stories/" + s[1] + "/" + s[2] + "/" };
      }
      return { site: "instagram", isVideo: false };
    }
    return null;
  },

  siteName(site) {
    return site === "youtube" ? "YouTube" : site === "instagram" ? "Instagram" : "";
  },
};
