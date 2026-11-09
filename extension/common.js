// Shared by background.js (importScripts) and popup.js.
/* exported CLIPGO */
var CLIPGO = {
  HOST: "com.clipgo.host",

  t(key, fallback, substitutions = []) {
    const values = (Array.isArray(substitutions) ? substitutions : [substitutions]).map((value) => String(value ?? ""));
    try {
      const translated = globalThis.chrome?.i18n?.getMessage(key, values);
      if (typeof translated === "string" && translated) return translated;
    } catch (_) { /* Older tests and invalidated extension contexts use the fallback. */ }
    return String(fallback ?? "").replace(/\$([1-9]\d*)/g, (match, index) => values[Number(index) - 1] ?? match);
  },

  get QUALITIES() {
    return [
      { value: "1080", label: this.t("common_quality_recommended", "1080p（推荐）") },
      { value: "best", label: this.t("common_quality_best", "最高画质 (4K)") },
      { value: "720", label: this.t("common_quality_720", "720p") },
      { value: "480", label: this.t("common_quality_480", "480p") },
      { value: "360", label: this.t("common_quality_360", "360p") },
    ];
  },

  // Host messages and old saved tasks still carry the original Chinese text.
  // Only registered complete messages or one-argument templates are translated.
  localizeMessage(text) {
    if (typeof text !== "string" || !text) return text || "";
    for (const [key, fallback] of Object.entries(this._legacyMessages)) {
      if (text === fallback) return this.t(key, fallback);
    }
    for (const [key, fallback] of Object.entries(this._legacyMessages)) {
      const parts = fallback.split("$1");
      if (parts.length !== 2 || (!parts[0] && !parts[1]) || !text.startsWith(parts[0]) || !text.endsWith(parts[1])) continue;
      const end = text.length - parts[1].length;
      if (end < parts[0].length) continue;
      let value = text.slice(parts[0].length, end);
      if (key.startsWith("bg_site_")) {
        const site = { "哔哩哔哩": "bilibili", "小红书": "xiaohongshu", "抖音": "douyin", "腾讯视频": "tencent", TikTok: "tiktok" }[value];
        if (!site) continue;
        value = this.siteName(site);
      } else if (["common_host_internal", "common_update_failed", "bg_unexpected_error", "bg_network_error"].includes(key)) {
        // Native validation errors may be wrapped in an internal-error prefix.
        // Translate only a complete known inner message, leaving diagnostics alone.
        const nested = Object.entries(this._legacyMessages).find(([, message]) => message === value);
        if (nested) value = this.t(nested[0], nested[1]);
      }
      return this.t(key, fallback, [value]);
    }
    return text;
  },

  _legacyMessages: {
      "common_stage_parsing": "解析视频信息…",
      "common_stage_preparing": "准备下载…",
      "common_stage_merging": "合并音视频为 MP4…",
      "common_stage_remuxing": "转封装为 MP4…",
      "common_note_exists": "文件已存在，已跳过",
      "common_host_missing_engine": "未找到 yt-dlp，请重新运行安装脚本",
      "common_host_internal": "助手内部错误: $1",
      "common_update_failed": "更新失败: $1",
      "common_updated": "已更新到 $1",
      "common_host_invalid_text": "下载器返回了无效的文本字段",
      "common_host_media_mismatch": "页面视频地址与下载任务不匹配",
      "common_host_invalid_douyin_url": "无效的抖音播放地址",
      "common_host_douyin_cdn_only": "仅接受抖音视频 CDN 的 HTTPS 播放地址",
      "common_host_douyin_title": "抖音视频 $1",
      "common_host_exit_code": "yt-dlp 退出码 $1",
      "common_host_missing_id": "缺少任务 id",
      "common_host_supported_sites": "仅支持 YouTube / Instagram / 哔哩哔哩 / X / 小红书 / TikTok / 抖音 / 腾讯视频链接",
      "common_host_unknown_action": "未知操作: $1",
      "bg_interrupted": "任务被中断（浏览器或扩展重启），请重试",
      "bg_host_not_found": "未检测到 ClipGo 本地助手，请先运行安装脚本",
      "bg_host_forbidden": "本地助手拒绝了此扩展（扩展 ID 不匹配），请重新运行安装脚本",
      "bg_host_start_failed": "本地助手启动失败，请重新运行安装脚本",
      "bg_host_disconnected": "与本地助手的连接已断开",
      "bg_douyin_url_expired": "抖音播放地址已失效，请刷新视频页面、播放后再点重试",
      "bg_douyin_request_rejected": "抖音解析接口拒绝了请求，请打开这条视频并播放后重试；已打开的旧页面请先刷新",
      "bg_tencent_preview": "腾讯视频只返回了试看片段，未保存为完整视频。请确认本浏览器已登录有观看权限的账号后重试；仍受限时请使用腾讯视频客户端缓存",
      "bg_tencent_unverified": "暂时无法确认腾讯视频是否完整，已停止保存以免只下载到片段，请稍后重试",
      "bg_tencent_protected": "腾讯视频返回了受保护或暂不支持的视频格式，当前无法导出 MP4",
      "bg_youtube_login": "YouTube 要求登录验证：请在本浏览器登录 YouTube，并在设置中开启「YouTube 使用登录状态」后重试",
      "bg_instagram_login": "Instagram 需要登录：请先在本浏览器登录 instagram.com 后重试",
      "bg_bilibili_login": "哔哩哔哩需要登录或相应观看权限：请在本浏览器登录有权限的账号后重试",
      "bg_bilibili_rate_limit": "哔哩哔哩暂时限制了请求，请稍后重试，并确认本浏览器能正常播放该视频",
      "bg_x_login": "X 需要登录或相应观看权限：请先在本浏览器登录 x.com，并确认可以播放该帖子中的视频",
      "bg_x_rate_limit": "X 暂时限制了请求，请稍后重试",
      "bg_x_no_video": "这条 X 帖子里没有可下载的视频",
      "bg_site_protected": "$1的这个视频受加密保护，无法保存为普通 MP4",
      "bg_site_login": "$1需要有效的登录状态或观看权限：请在本浏览器打开并播放该视频，刷新页面后重试",
      "bg_site_rate_limit": "$1暂时限制了请求，请稍后重试",
      "bg_site_no_video": "$1的这个链接里没有可下载的视频（可能是图文内容）",
      "bg_private_video": "这是私密视频，无法下载",
      "bg_no_video": "这个链接里没有视频（可能是图片帖子）",
      "bg_video_unavailable": "视频不存在或已被删除",
      "bg_members_only": "会员专属视频，需要登录有权限的账号",
      "bg_network_error": "网络错误，请检查网络（或代理）后重试：$1",
      "bg_host_timeout": "本地助手无响应",
      "bg_host_empty_response": "本地助手没有返回数据",
      "bg_stage_downloading": "下载中",
      "bg_download_complete": "ClipGo 下载完成",
      "bg_supported_sites": "支持 YouTube、Instagram、B站、X、小红书、TikTok、抖音和腾讯视频链接",
      "bg_not_video_page": "这不是视频页面，请打开具体视频或粘贴视频链接",
      "bg_already_downloading": "这个视频正在下载中",
      "bg_stage_starting": "启动本地助手…",
      "bg_stage_douyin_page": "读取抖音视频页面…",
      "bg_stage_page_video": "读取网页视频…",
      "bg_unknown_command": "未知操作",
      "bg_unexpected_error": "发生错误：$1",
      "bg_menu_link": "用 ClipGo 下载这个视频 (MP4)",
      "bg_menu_page": "用 ClipGo 下载当前视频 (MP4)",
      "bg_download_failed": "ClipGo 无法下载"
  },

  DEFAULT_SETTINGS: { quality: "1080", ytCookies: false, notify: true, quickButton: true },

  /**
   * Recognize a supported video URL (or a URL embedded in copied share text).
   * Returns null for other sites, or { site, isVideo, url } where url is
   * a cleaned-up canonical link.
   */
  detect(raw) {
    let text = String(raw || "").trim();
    if (!/^[a-z][a-z0-9+.-]*:/i.test(text) || (/^https?:/i.test(text) && /\s/.test(text))) {
      // Mobile apps copy descriptions alongside a URL. Only accept a single URL
      // so a share message containing several destinations cannot pick one silently.
      const links = text.match(/https?:\/\/[^\s<>"'，。！？；：、（）【】]+/g);
      if (!links || links.length !== 1) return null;
      text = links[0].replace(/[),.;!?\]}]+$/, "");
    }
    let u;
    try { u = new URL(text); } catch (err) { return null; }
    if (!/^https?:$/.test(u.protocol)) return null;
    const domain = u.hostname.toLowerCase();
    const host = domain.replace(/^(www|m|music)\./, "");
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

    if (host === "bilibili.com" || host === "b23.tv") {
      const match = host === "bilibili.com"
        ? path.match(/^\/video\/(BV[0-9A-Za-z]{10}|av[1-9]\d*)\/?$/)
        : path.match(/^\/([0-9A-Za-z]+)\/?$/);
      if (!match) return { site: "bilibili", isVideo: false };
      // p identifies the selected part. All other parameters are tracking/view state.
      const part = u.searchParams.get("p");
      if (part !== null && (!/^\d+$/.test(part) || !Number.isSafeInteger(Number(part)) || Number(part) < 1)) {
        return { site: "bilibili", isVideo: false };
      }
      // b23.tv/BV… and /av… redirects discard p; expand these locally.
      // Opaque short codes must keep their query and let the redirect select the part.
      if (host === "b23.tv" && !/^(BV[0-9A-Za-z]{10}|av[1-9]\d*)$/.test(match[1])) {
        return { site: "bilibili", isVideo: true, url: "https://b23.tv/" + match[1] + u.search };
      }
      const base = "https://www.bilibili.com/video/";
      const url = base + match[1] + (Number(part) > 1 ? "?p=" + Number(part) : "");
      return { site: "bilibili", isVideo: true, url };
    }

    if (host === "x.com" || host === "twitter.com" || host === "mobile.twitter.com" || host === "mobile.x.com") {
      const m = path.match(/^\/([\w]{1,15}|i\/web)\/status\/([1-9]\d*)(?:\/(video|photo)\/([1-9]\d*))?\/?$/);
      if (!m) return { site: "twitter", isVideo: false };
      // A media index selects one attachment; a bare status downloads its videos.
      const url = "https://x.com/" + m[1].toLowerCase() + "/status/" + m[2] + (m[3] ? "/" + m[3] + "/" + m[4] : "");
      return { site: "twitter", isVideo: true, url };
    }

    if (["xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com"].includes(domain)) {
      const m = path.match(/^\/(explore|discovery\/item)\/([0-9a-f]+)\/?$/);
      if (!m) return { site: "xiaohongshu", isVideo: false };
      // xsec_token and related query values authorize access to shared notes.
      // Preserve both the original route and the complete query byte-for-byte.
      return { site: "xiaohongshu", isVideo: true, url: "https://www.xiaohongshu.com/" + m[1] + "/" + m[2] + u.search };
    }
    if (domain === "xhslink.com" || domain === "www.xhslink.com") {
      const m = path.match(/^\/((?:[aom]\/)?[0-9A-Za-z]{2,})\/?$/);
      return m ? { site: "xiaohongshu", isVideo: true, url: "https://xhslink.com/" + m[1] + u.search }
        : { site: "xiaohongshu", isVideo: false };
    }

    if (["tiktok.com", "www.tiktok.com", "m.tiktok.com"].includes(domain)) {
      const m = path.match(/^\/@([\w.-]+)\/video\/([1-9]\d*)\/?$/);
      if (m) return { site: "tiktok", isVideo: true, url: "https://www.tiktok.com/@" + m[1] + "/video/" + m[2] };
      const shared = path.match(/^\/(?:share\/video|embed)\/([1-9]\d*)\/?$/);
      if (shared) return { site: "tiktok", isVideo: true, url: "https://www.tiktok.com/@_/video/" + shared[1] };
      const short = path.match(/^\/t\/(\w+)\/?$/);
      if (short) return { site: "tiktok", isVideo: true, url: "https://www.tiktok.com/t/" + short[1] + u.search };
      return { site: "tiktok", isVideo: false };
    }
    if (domain === "vm.tiktok.com" || domain === "vt.tiktok.com") {
      const m = path.match(/^\/(\w+)\/?$/);
      return m ? { site: "tiktok", isVideo: true, url: "https://" + domain + "/" + m[1] + u.search }
        : { site: "tiktok", isVideo: false };
    }

    if (["douyin.com", "www.douyin.com", "m.douyin.com"].includes(domain)) {
      const m = path.match(/^\/video\/([1-9]\d*)\/?$/);
      // A selected video can be opened over a feed or profile without changing
      // the path. The modal ID is more current than a previous video's path.
      const modal = u.searchParams.get("modal_id");
      const id = modal !== null ? (/^[1-9]\d*$/.test(modal) ? modal : null) : m && m[1];
      return id ? { site: "douyin", isVideo: true, url: "https://www.douyin.com/video/" + id }
        : { site: "douyin", isVideo: false };
    }
    if (domain === "v.douyin.com") {
      const m = path.match(/^\/([\w-]+)\/?$/);
      return m ? { site: "douyin", isVideo: true, url: "https://v.douyin.com/" + m[1] + u.search }
        : { site: "douyin", isVideo: false };
    }
    if (domain === "v.qq.com") {
      const m = path.match(/^\/x\/(page|cover\/\w+)\/(\w+)(?:\.html)?\/?$/);
      // A cover without a video ID is a series, not the selected episode.
      return m ? { site: "tencent", isVideo: true, url: "https://v.qq.com/x/" + m[1] + "/" + m[2] + ".html" }
        : { site: "tencent", isVideo: false };
    }
    return null;
  },

  siteName(site) {
    switch (site) {
      case "youtube": return this.t("common_site_youtube", "YouTube");
      case "instagram": return this.t("common_site_instagram", "Instagram");
      case "bilibili": return this.t("common_site_bilibili", "哔哩哔哩");
      case "twitter": return this.t("common_site_twitter", "X");
      case "xiaohongshu": return this.t("common_site_xiaohongshu", "小红书");
      case "tiktok": return this.t("common_site_tiktok", "TikTok");
      case "douyin": return this.t("common_site_douyin", "抖音");
      case "tencent": return this.t("common_site_tencent", "腾讯视频");
      default: return "";
    }
  },

  readableTitle(title) {
    return typeof title === "string" && !title.includes("\uFFFD") ? title.trim() : "";
  },

  titleFromFiles(files) {
    if (!Array.isArray(files)) return "";
    for (const file of files) {
      if (typeof file !== "string") continue;
      const name = file.split(/[\\/]/).pop();
      const title = this.readableTitle(name.replace(/\.[A-Za-z0-9]{1,10}$/, "").replace(/ \[[^\]]+\]$/, ""));
      if (title) return title;
    }
    return "";
  },

  taskTitle(task) {
    if (!task) return "";
    return this.readableTitle(task.title) || this.titleFromFiles(task.files) || (typeof task.url === "string" ? task.url : "");
  },
};
