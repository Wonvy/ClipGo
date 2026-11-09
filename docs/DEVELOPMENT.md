# ClipGo 开发文档

这份文档面向要修改、调试或发布 ClipGo 的开发者。使用方法见 [README](../README.md)。

- [整体架构](#整体架构)
- [插件 extension/](#插件-extension)
- [本地助手 host/](#本地助手-host)
- [通信协议](#通信协议)
- [下载命令与画质策略](#下载命令与画质策略)
- [平台差异与踩过的坑](#平台差异与踩过的坑)
- [调试](#调试)
- [测试](#测试)
- [修改扩展 ID](#修改扩展-id)
- [发布新版本](#发布新版本)

---

## 整体架构

```
content.js ─┐  chrome.runtime.sendMessage
popup.js  ──┼──────────────────────────────► background.js (service worker)
右键菜单 ───┘                                   │  · 任务列表 → chrome.storage.local
                                               │  · 读取 cookie → chrome.cookies
           chrome.storage.onChanged            │
popup.js / content.js ◄────────────────────────┤
  (渲染进度)                                    │ chrome.runtime.connectNative("com.clipgo.host")
                                               ▼
                                     clipgo_host.py（每个连接一个进程）
                                               │ subprocess（每个任务一个）
                                               ▼
                                  yt-dlp ─► ffmpeg 合并 MP4（deno 处理 YouTube JS 验证）
```

- **后台是唯一的状态源**。任务数组保存在 `chrome.storage.local.tasks`，弹窗和页面脚本只负责读取和渲染，通过 `storage.onChanged` 自动更新。
- **下载走长连接**：有任务进行时，后台用 `connectNative` 保持一条连接。Chrome 105+ 在原生连接打开期间不会挂起 service worker。所有任务结束后主动断开，助手进程随之退出。
- **一次性操作走短连接**：`ping`、`reveal`、`repairPaths`、`chooseDir`、`update` 用 `sendNativeMessage`，每次启动一个独立的助手进程。
- **service worker 重启 = 连接断开**：后台启动时把仍处于进行中的任务标记为「任务被中断」，因为连接断开时助手已经结束了正在下载的任务。

## 插件 extension/

| 文件 | 职责 |
|---|---|
| `manifest.json` | MV3 配置。`key` 固定扩展 ID；为八个视频网站及其分享短链接声明权限；腾讯视频仅授权 `v.qq.com`，不授权整个 `qq.com` |
| `common.js` | `CLIPGO.detect(url)`：识别链接和单链接分享文案；标题回退；画质选项；默认设置。弹窗、后台 (`importScripts`) 和页面脚本共用 |
| `background.js` | 任务管理、助手连接、cookie 收集、错误提示翻译、右键菜单、通知、角标 |
| `content.js` | 页面上的悬浮「MP4」按钮 |
| `douyin-media.js` | 有界解析抖音已加载的播放数据，只输出视频 ID、标题与合法明文播放格式 |
| `douyin-page.js` | MAIN 环境只读观察抖音正常同源视频 JSON 响应，将播放信息交给页面脚本 |
| `popup.*` | 工具栏弹窗：链接输入、画质、任务列表、设置 |

### 链接识别 `CLIPGO.detect`

返回 `null`（不支持的网站）或 `{ site, isVideo, url }`。普通视频链接去掉跟踪参数，小红书和短链接保留访问所需的完整查询参数；分享文案必须恰好包含一个 http(s) 链接：

| 输入 | 输出 url |
|---|---|
| `youtube.com/watch?v=ID&list=…&pp=…`、`m.`、`music.` | `https://www.youtube.com/watch?v=ID` |
| `youtu.be/ID?si=…` | `https://www.youtube.com/watch?v=ID` |
| `youtube.com/shorts/ID` | `https://www.youtube.com/shorts/ID` |
| `youtube.com/live/ID`、`/embed/ID` | `https://www.youtube.com/watch?v=ID` |
| `instagram.com/reel/ID`、`/reels/ID`、`/用户名/reel/ID` | `https://www.instagram.com/reel/ID/` |
| `instagram.com/p/ID`、`/tv/ID` | `https://www.instagram.com/p/ID/` |
| `instagram.com/stories/用户名/数字ID` | 原链接 |
| `bilibili.com/video/BV…`、`/video/av…`、`m.bilibili.com/video/…` | `https://www.bilibili.com/video/视频ID` |
| `bilibili.com/video/BV…?p=2&spm_id_from=…` | `https://www.bilibili.com/video/BV…?p=2` |
| `b23.tv/短码?share_source=…` | `https://b23.tv/短码?share_source=…`（保留短链接查询参数） |
| `b23.tv/BV…?p=2`、`b23.tv/av…?p=2` | `https://www.bilibili.com/video/视频ID?p=2` |
| `x.com/用户名/status/数字ID?s=…`、`twitter.com/用户名/status/数字ID`（含移动版） | `https://x.com/用户名/status/数字ID`（用户名转小写） |
| `x.com/i/web/status/数字ID` | `https://x.com/i/web/status/数字ID` |
| `twitter.com/用户名/status/数字ID/video/2`、`/photo/2` | `https://x.com/用户名/status/数字ID/video/2` 或 `/photo/2`（保留附件序号） |
| `xiaohongshu.com/explore/笔记ID`、`/discovery/item/笔记ID` | `https://www.xiaohongshu.com/原路径`（完整保留 `xsec_token` 等查询参数） |
| `xhslink.com/短码`、`/a/短码`、`/o/短码`、`/m/短码` | 同路径 HTTPS 链接，保留查询参数 |
| `tiktok.com/@用户名/video/数字ID`（含移动版） | `https://www.tiktok.com/@用户名/video/数字ID` |
| `tiktok.com/share/video/数字ID`、`/embed/数字ID` | `https://www.tiktok.com/@_/video/数字ID` |
| `vm.tiktok.com/短码`、`vt.tiktok.com/短码`、`tiktok.com/t/短码` | 对应 HTTPS 短链接，保留查询参数 |
| `douyin.com/video/数字ID`、`douyin.com/…?modal_id=数字ID` | `https://www.douyin.com/video/数字ID`（有效 `modal_id` 优先） |
| `v.douyin.com/短码` | 同路径 HTTPS 链接，保留查询参数 |
| `v.qq.com/x/page/视频ID.html`、`/x/cover/专辑ID/视频ID.html` | 同路径 HTTPS 链接，保留专辑和视频 ID，去掉跟踪参数 |

规范化后，普通视频链接的跟踪参数不会影响「正在下载」检查和按钮进度。哔哩哔哩 `p=1` 与无 `p` 参数使用同一链接，其他正整数分 P 参数会保留，不同 P 是独立任务；非法分 P 参数不视为可下载链接。`b23.tv/BV…`、`b23.tv/av…` 会直接展开，以免重定向丢失分 P 参数；其他 b23.tv 短码保留查询参数，由 yt-dlp 跟随跳转。BV 与 av 不会在浏览器端相互转换，短码链接与完整链接也不合并任务。哔哩哔哩支持范围是普通视频，不包含番剧、直播或整套合集。

X 支持 `x.com/用户名/status/数字ID`、`/i/web/status/数字ID` 和对应的 `twitter.com` 帖子链接，去掉分享跟踪参数，并保留 `/video/N`、`/photo/N` 附件序号。首页和个人主页不会被识别为视频。帖子是否实际含有视频由下载引擎判断；未指定附件的一帖多视频可能产生多个文件。

小红书保留原始 `explore` / `discovery/item` 路径和查询参数，避免破坏访问令牌。TikTok、抖音不支持主页批量下载、合集或直播；腾讯视频仅接受具体视频或单集，`/x/cover/专辑ID.html` 这种整部专辑链接不会启用下载。

### 任务对象

```js
{
  id, url, site,            // youtube | instagram | bilibili | twitter |
                           // xiaohongshu | tiktok | douyin | tencent
  quality,                  // "1080" | "best" | "720" | "480" | "360"
  status,                   // queued → running → merging → done | error | cancelled
  stage,                    // 给人看的阶段文字
  percent, speed, eta, title,
  files: [],                // 完成后的文件绝对路径
  error, rawError, note,
  downloadSource,            // 可选 "page"：抖音使用网页播放信息；不存播放地址
  createdAt, finishedAt
}
```

最多保留 30 条已结束的任务。进度写入 `storage` 有 300ms 节流。

标题展示使用 `CLIPGO.taskTitle()`：正常标题 → 文件名去掉扩展名和 `[ID]` → 视频链接。含 U+FFFD 替换字符的标题不展示。恢复旧任务时，后台最多提交 30 个损坏路径给 `repairPaths`，再用修复后的文件名恢复标题；正常标题和文件路径保持原值。

### 页面按钮 content.js

- 只用**一个**按钮，放在 `<body>` 下的 closed shadow root 里（`<clipgo-quick>`），用 `position: fixed` 定位到目标元素的左上角。按钮不在页面的 `<a>` 里，所以点击不会触发跳转；z-index 最大，能盖在 YouTube 的悬停预览上面。
- 目标检测（`findTarget`），按顺序：
  1. 指针下最近的 `<a>`，链接是视频，并且尺寸 ≥ 100×56（用来排除标题链接）
  2. `document.elementsFromPoint` 里的 `<video>`（能穿过覆盖层）：
     - YouTube `#movie_player` → 当前页面链接
     - YouTube `ytd-video-preview` → 预览里的链接
     - Instagram `<article>` → 帖子里的 `/p/`、`/reel/` 链接；否则用页面链接；再否则向上找 8 层
- 目标元素被替换，或页面滚动时，会重新检测指针下的元素（`relookup`），而不是直接隐藏按钮。
- 哔哩哔哩另外识别视频卡片内的预览和播放器容器，兼容 `<video>` 与 `<bwp-video>`。播放器目标在渲染和点击时重新读取当前页面链接，避免切换分 P 后仍下载上一 P。
- X 时间线中的视频从所属帖子查找状态链接；帖子详情页也支持播放器按钮。
- 小红书、TikTok、抖音和腾讯视频识别所属视频卡片或播放器，避免把背景推荐视频误认为当前视频。小红书优先保留带访问令牌的笔记链接，图文详情浮层没有视频时不会回退到背景视频。
- 页面脚本响应 `{cmd:"currentVideo", url?}`，返回 `{url, title, media?}`。弹窗和「下载当前视频」右键操作借此识别当前详情页、信息流或浮层中的视频；候选不唯一时返回空 URL，要求用户打开具体视频或粘贴链接。重试时可传入原视频页 URL，重新取得与该视频匹配的播放信息。
- 抖音专用取源页响应 `{cmd:"pageVideo", url}`，返回相同结构。它仅在明确的 `/video/ID` 页面、没有 `modal_id`、请求 URL 与页面 URL 完全对应时读取同 ID 的 SSR / 桥接记录；无需播放器可见或已经开始播放，不借用信息流中的其他记录。
- `currentVideo` 中的抖音 `blob:` 播放器通过 `RENDER_DATA` 或正常 API 响应里的同 ID 播放记录关联，要求播放器可见、已加载且祖先视频 ID 一致；不会把邻近推荐视频当作目标。`CLIPGO_DOUYIN.parse()` 支持对象、JSON 和 URL 编码 JSON，限制 2 MiB 输入、深度 16、10000 个遍历节点、100 条记录和每条 16 个格式。
- 抖音 MAIN 脚本在 `document_start` 观察同源视频 detail/feed 等接口的 fetch / XHR JSON 响应副本；不主动请求 API、不读取 cookie、不改变响应。只传递白名单 HTTPS `douyinvod.com` 播放地址和 H.264 格式，拒绝账号、显式端口及加密信息；两侧均检查 `postMessage` 来源和源站，重播最多 100 条缓存记录。
- 抖音临时签名播放地址仅随本次下载消息传给助手，不写入 `chrome.storage` 任务历史。下载或重试先查询已打开页面；明确视频页仍无匹配信息时，创建非激活、静音的后台标签页，不弹出独立窗口、不切换当前标签页。每 500 ms 查询 `pageVideo`，最多 24 次（约 12 秒）；成功或超时后关闭仍由扩展拥有的专用标签页，用户接管或导航后保留。未取得信息仍可尝试标准解析，网站的登录或验证要求由用户正常完成。
- 准备阶段的取消必须立即停止取源和后续下载启动，并清理仍由扩展拥有的专用标签页；不能等轮询超时后再取消，也不能误关用户接管的标签页。
- **必须用 DOM API 创建元素，不能用 `innerHTML`**：YouTube 启用了 Trusted Types，赋值 `innerHTML` 会抛异常（对插件的 content script 也一样）。

## 本地助手 host/

`clipgo_host.py` 只依赖 Python 标准库，兼容 Python 3.8+，Windows 和 macOS 用同一份代码。

| | Windows | macOS |
|---|---|---|
| 安装目录 | `%LOCALAPPDATA%\ClipGo` | `~/Library/Application Support/ClipGo` |
| Python | 嵌入式 Python 3.12（`python\`） | 系统 `/usr/bin/python3` |
| 启动入口 | `clipgo_host.bat` | `clipgo_host.py`（shebang） |
| yt-dlp | `bin\yt-dlp.exe`（单文件版） | `bin/yt-dlp-mac/yt-dlp_macos`（解压版） |
| ffmpeg | yt-dlp/FFmpeg-Builds win64-gpl | martin-riedl.de（arm64 / amd64） |
| 注册方式 | 注册表 `HKCU\Software\{Google\Chrome,Microsoft\Edge,Chromium,BraveSoftware\Brave-Browser}\NativeMessagingHosts\com.clipgo.host` | `~/Library/Application Support/{Google/Chrome,Microsoft Edge,…}/NativeMessagingHosts/com.clipgo.host.json` |
| 取消任务 | `taskkill /T /F`（连同 ffmpeg 子进程） | `killpg(SIGTERM)`（独立进程组） |
| 打开位置 | `explorer /select,` | `open -R` |
| 选择文件夹 | PowerShell `FolderBrowserDialog` | AppleScript `choose folder` |

安装目录里的其他文件：`config.json`（`downloadDir`）、`host.log`（超过 1MB 自动清空）。

安全措施：
- 只接受 `youtube.com`、`youtu.be`、`instagram.com`、`bilibili.com`、`b23.tv`、`x.com`、`twitter.com`、`xiaohongshu.com`、`xhslink.com`、`tiktok.com`、`douyin.com`、`v.qq.com` 及其子域名的 http(s) 链接
- URL 前加 `--` 传给 yt-dlp，防止以 `-` 开头的参数注入
- 所有子进程都用参数列表启动，不经过 shell
- cookie 写入 `mkstemp` 创建的临时文件（权限 0600），任务结束后在 `finally` 里删除
- Native Messaging 清单的 `allowed_origins` 只包含固定的扩展 ID

## 通信协议

浏览器和助手之间是 Native Messaging 标准格式：4 字节本机字节序长度 + UTF-8 JSON。

### 插件 → 助手

| action | 字段 | 回复 |
|---|---|---|
| `ping` | `rid` | `{type:"pong", version, ytdlp, ffmpeg, deno, downloadDir, rid}` |
| `download` | `id, url, quality, cookies[]` | 异步推送 `stage` / `progress` / `done` / `error` / `cancelled` |
| `cancel` | `id` | 对应任务推送 `cancelled` |
| `reveal` | `path` | `{type:"ok"}`；文件不存在时打开下载目录 |
| `repairPaths` | `paths[]` | `{type:"repairedPaths", paths:{旧路径:实际路径}, rid}` |
| `openDir` | — | `{type:"ok"}` |
| `chooseDir` | — | `{type:"dir", downloadDir}`（用户取消时返回原目录） |
| `update` | — | `{type:"updated", ok, output, ytdlp}` |

`cookies` 的元素格式与 `chrome.cookies.Cookie` 相同（`domain, hostOnly, path, secure, expirationDate, name, value`），助手会转成 Netscape `cookies.txt` 格式。

抖音的 `download` 消息可额外带 `title` 和 `media:{videoUrl,url,formats:[{url,width,height,bitrate}]}`。`videoUrl` 必须等于任务的规范视频页 URL；后台和助手再次校验播放域名、协议、视频匹配及格式数量。助手把有效信息写入本次任务的临时 JSON，交给 yt-dlp `--load-info-json` 下载，任务结束后删除。

后台自动收集对应平台的 cookie：Instagram、哔哩哔哩、小红书、TikTok、抖音使用各自主域，短链接沿用所属平台；X 使用 `x.com` 和 `twitter.com`；腾讯视频使用 URL 过滤器 `https://v.qq.com/`，仅收集适用于该地址的 cookie。YouTube 仍由 `ytCookies` 设置控制。cookie 不保存在任务对象中，助手任务结束后删除临时文件。

`repairPaths` 仅处理前 30 个含 U+FFFD 的绝对路径，且父目录必须等于当前配置的下载目录。它在该目录内按 ` [视频ID].mp4` 后缀匹配现存普通文件，只有唯一匹配时才返回映射；不递归、不重命名或移动文件，也不会猜测多个候选中的哪一个正确。

### 助手 → 插件（下载过程）

```jsonc
{"type": "stage",    "id": "…", "stage": "解析视频信息…"}
{"type": "stage",    "id": "…", "stage": "准备下载…", "title": "视频标题"}
{"type": "progress", "id": "…", "percent": 46.2, "speed": "3.2MiB/s", "eta": "00:04", "title": "…"}
{"type": "stage",    "id": "…", "stage": "合并音视频为 MP4…"}
{"type": "done",     "id": "…", "files": ["/…/标题 [ID].mp4"], "title": "视频标题", "note": ""}
{"type": "error",    "id": "…", "message": "yt-dlp 的原始错误"}
{"type": "cancelled","id": "…"}
```

进度推送节流为每 0.4 秒最多一次（100% 时一定推送）。`background.js` 的 `friendlyError()` 会把常见的 yt-dlp 错误翻译成中文提示。

助手发现 stdin 关闭（浏览器断开连接）时，会结束所有正在进行的任务再退出。

## 下载命令与画质策略

助手实际执行的命令（`build_command`）：

```
yt-dlp --ignore-config --encoding utf-8 --no-playlist --no-mtime --newline --progress
       --progress-template "download:CLIPGO_PJ:%(progress._percent_str)s|…|%(info.title)j"
       --print "before_dl:CLIPGO_TJ:%(title)j"
       --print "after_move:CLIPGO_FJ:%(filepath)j" --no-quiet
       --ffmpeg-location <bin>
       -f "bv*+ba/b" -S <排序规则>
       --merge-output-format mp4 --remux-video mp4
       -o "<下载目录>/%(title).120B [%(id)s].%(ext)s"
       --js-runtimes deno:<bin/deno>
       [--cookies <临时文件>]
       -- <url>
```

- 进度、下载前标题和最终文件路径分别使用 `CLIPGO_PJ:` / `CLIPGO_TJ:` / `CLIPGO_FJ:`。`j` 模板把标题与路径编码为 JSON 字符串，保留中文、emoji、引号和换行；助手先按前缀拆分，再解码字符串。旧 `CLIPGO_P:` / `CLIPGO_F:` 行仍可解析。
- `--print` 会自动开启 `--quiet`，所以要加 `--no-quiet`，否则看不到 `[Merger]` 行，界面就无法显示「合并中」。
- `--ignore-config` 避免用户自己的 yt-dlp 配置干扰。
- `--encoding utf-8` 与助手读取管道的编码保持一致，防止 Windows 下中文标题和完成路径乱码。
- `before_dl` 标题会立即通过 `stage` 消息更新任务，并保留到 `done`，避免快速下载没有进度消息时始终显示 URL。
- 哔哩哔哩沿用 `--no-playlist`：普通分 P 视频未指定 `p` 时下载第一 P，指定 `?p=N` 时下载该 P，不批量下载整套视频；助手保留 URL 中的分 P 参数。
- 抖音有匹配的网页播放信息时，用 `--load-info-json <临时文件>` 替代末尾的页面 URL；格式选择仍使用用户的画质设置。
- 腾讯视频加载内置 `plugins/yt_dlp_plugins/extractor/clipgo_tencent.py`，按网站正常播放器方式传递 `v.qq.com` 登录信息，不扩大 cookie 域，也不修改客户端或加密参数。HLS 的 `EXT-X-KEY` / `EXT-X-SESSION-KEY` 允许 `METHOD=NONE`，以及 `METHOD=AES-128` 且 `KEYFORMAT` 缺省或为 `identity` 的普通加密流，由 yt-dlp 标准处理；保留 API 和清单中的 DRM 标记，其他加密方法或未知 `KEYFORMAT` 不扩展支持。插件继续比较可用 HLS 时长与视频信息中的完整时长；只有试看、受保护或暂不支持的格式、无法确认完整性时给出明确错误，不将试看片段报告为完整下载。

画质用 `-S`（格式排序）而不是写死的 `-f`，这样视频达不到所选分辨率时会自动降级：

| 选项 | `-S` | 实际效果（4K 视频实测） |
|---|---|---|
| `1080` | `res:1080,+codec:avc:m4a` | 1920×1080 avc1 + mp4a |
| `720` 等 | `res:720,+codec:avc:m4a` | 1280×720 avc1 + mp4a |
| `best` | `res,ext:mp4:m4a` | 3840×2160 av01 + mp4a |

`+codec:avc:m4a` 让编码偏好变成 H.264 > H.265 > VP9 > AV1，音频偏好 AAC，保证兼容性。

哔哩哔哩复用同一画质策略，可用格式由视频和登录账号的观看权限决定。

小红书图文笔记可能没有视频格式；抖音自动取源仍可能遇到网站登录或验证要求；腾讯视频可能返回试看、观看权限、地区或加密限制。错误应展示相应原因，不能把解析到链接或读取到视频信息当作实际下载成功。

## 平台差异与踩过的坑

**macOS：不能用单文件版 yt-dlp。**
`yt-dlp_macos`（PyInstaller 单文件版）每次运行都会把 `Python.framework` 解压到 `$TMPDIR/_MEIxxxx`。助手由浏览器启动时，会继承浏览器的隔离属性，它写出的文件都会被加上 `com.apple.quarantine`（值里带 `Edge` / `Chrome`）。这些没有公证的文件会触发 Gatekeeper 弹窗「Python.framework 已损坏」。
所以改用解压版 `yt-dlp_macos.zip`：安装时解压好并执行 `xattr -dr com.apple.quarantine`，运行时不再写出可执行文件。同理，在 macOS 上「更新 yt-dlp」不能用 `yt-dlp -U`，而是由助手重新下载 zip、去掉隔离属性后替换（`update_ytdlp()`）。

**macOS：安装目录不能放在桌面 / 文稿。**
`~/Desktop` 和 `~/Documents` 受 TCC 隐私保护，浏览器启动其中的脚本可能被拦截或弹窗，所以助手安装到 `~/Library/Application Support`。

**Windows：子进程不能弹出控制台窗口。**
所有子进程都使用 `CREATE_NO_WINDOW`；下载进程额外用 `CREATE_NEW_PROCESS_GROUP`，方便 `taskkill /T` 结束整个进程树。stdin/stdout 用 `msvcrt.setmode` 设为二进制模式。

**Windows：PowerShell 脚本必须带 UTF-8 BOM。**
Windows PowerShell 5.1 默认用 ANSI 读取没有 BOM 的脚本，中文会乱码，甚至导致解析失败。`.bat` 只用 ASCII，`.ps1` 存为 UTF-8 with BOM + CRLF（`.gitattributes` 已配置换行）。Native Messaging 清单 JSON 必须是**无 BOM** 的 UTF-8，所以用 `[IO.File]::WriteAllText` + `UTF8Encoding($false)` 写入。

**YouTube 需要 JS 运行时。**
yt-dlp 从 2025 年底开始，下载 YouTube 需要外部 JavaScript 运行时来处理签名和 n 参数，所以安装脚本会附带 deno，并用 `--js-runtimes deno:<路径>` 指定。

**YouTube 的 Trusted Types。** 见上文 content.js 一节。

## 调试

**插件后台**：扩展管理页 → ClipGo → 「Service Worker / 检查视图」，可以看到 `console` 输出和 `chrome.storage`。

**弹窗**：在弹窗上右键 →「检查」。

**页面按钮**：在 YouTube 页面打开开发者工具，Console 顶部的上下文切换到 **ClipGo**（content script 运行在独立环境中）。

**助手**：
- 日志：安装目录下的 `host.log`
- 浏览器连接失败的原因会出现在后台 service worker 的 console 里（如 `Specified native messaging host not found.`）
- 不经过浏览器直接测试，见下一节

**改了 `clipgo_host.py` 之后**要重新运行安装脚本，它才会被复制到安装目录。或者在开发时直接把改好的文件复制过去。

## 测试

### 自动回归测试（无需联网）

在仓库根目录运行：

```bash
node --test tests/*.test.js
python -m unittest discover -s tests -p "test_*.py"
```

测试覆盖八个平台的链接规范化、分 P / 附件 / modal 识别、分享令牌保留、后台消息和 cookie 传递、页面按钮与 `currentVideo` / `pageVideo`、抖音播放数据及只读响应桥接、中文和 emoji 的 JSON 输出解析、旧路径修复。离线测试不替代真实浏览器与视频网站下载验证。

### 助手（不需要浏览器）

`tools/nm_client.py` 会像浏览器一样启动助手并收发消息：

```bash
# 检查助手状态
python3 tools/nm_client.py "$HOME/Library/Application Support/ClipGo/clipgo_host.py" \
  '[0, {"action": "ping", "rid": 1}]'

# 真实下载（19 秒的视频）
python3 tools/nm_client.py "$HOME/Library/Application Support/ClipGo/clipgo_host.py" \
  '[0, {"action": "download", "id": "t1", "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "quality": "1080"}]'

# 下载 2 秒后取消
python3 tools/nm_client.py "$HOME/Library/Application Support/ClipGo/clipgo_host.py" \
  '[0, {"action": "download", "id": "t1", "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"}]' \
  '[2, {"action": "cancel", "id": "t1"}]'
```

Windows 上把第一个参数换成 `%LOCALAPPDATA%\ClipGo\clipgo_host.bat`。

想不联网测试进度解析、错误处理和取消，可以在临时目录放一份 `clipgo_host.py`，`bin/` 里放一个假的 `yt-dlp` 脚本，按协议格式输出 `CLIPGO_PJ:…`、`CLIPGO_TJ:…`、`CLIPGO_FJ:…` 行；标题和路径字段必须是合法 JSON 字符串。

用 ffprobe 检查下载结果的编码：

```bash
"$HOME/Library/Application Support/ClipGo/bin/ffprobe" -v error \
  -show_entries stream=codec_name,width,height -of compact "文件.mp4"
```

### 插件

发布前手动检查：

- [ ] 弹窗显示「助手已就绪」，版本号正确
- [ ] YouTube 视频页：弹窗自动填入链接，能下载
- [ ] YouTube 首页 / 搜索 / Shorts / 播放器上出现「MP4」按钮；悬停预览开始播放后按钮仍然可见；点击不会跳转
- [ ] 下载中按钮显示百分比，完成后显示「已下载」
- [ ] 取消、重试、打开位置、更改保存位置都正常
- [ ] Instagram（已登录）：信息流视频帖子、Reels、主页网格
- [ ] 哔哩哔哩 BV / av 视频：弹窗、右键菜单、视频卡片和播放器按钮均可下载
- [ ] 哔哩哔哩 b23.tv 视频短链接可下载，任务显示哔哩哔哩标识
- [ ] 哔哩哔哩分 P：无 `p` 时只下载第一 P，`?p=2` 时只下载第二 P；页面内切换 P 后按钮跟随更新
- [ ] 哔哩哔哩登录后可下载账号有权观看的画质；请求受限时显示可理解的提示
- [ ] X / twitter.com 视频帖子：弹窗、右键菜单、时间线和详情页播放器按钮均可下载
- [ ] X 首页和个人主页不直接启用下载；纯文字或图片帖显示无视频提示
- [ ] X 一帖多视频的下载结果和完成任务中的文件列表正确
- [ ] 小红书视频笔记、带令牌分享链接及 xhslink 短链接；图文浮层不误选背景视频
- [ ] TikTok 视频及 vm / vt / t 短链接；抖音视频、modal_id 和分享短链接
- [ ] 小红书 / TikTok / 抖音信息流或浮层：弹窗识别当前视频，切换后不使用旧链接
- [ ] 抖音 `blob:` 播放视频关联正确 ID；重试重新取得播放源，签名地址不进入任务历史
- [ ] 粘贴或重试未打开的抖音视频：后台静音标签页取得同 ID 数据并关闭；无独立窗口且当前标签页不切换；用户接管标签页后保留
- [ ] 抖音准备阶段取消立即停止取源、清理专用标签页且不启动下载；登录验证不被跳过
- [ ] 腾讯视频单个视频或剧集；专辑页面不触发批量下载，仅有试看、权限或加密限制时不误报完整下载
- [ ] 中文、emoji 标题和完成路径显示正确；旧任务唯一匹配时可恢复「打开位置」
- [ ] 卸载助手后弹窗提示「未连接助手」，页面按钮点击后提示安装

## 修改扩展 ID

扩展 ID 由 `manifest.json` 的 `key`（RSA 公钥的 DER 格式再 base64 编码）决定。如果 fork 后想换成自己的 ID：

```bash
openssl genrsa -out clipgo_key.pem 2048
openssl rsa -in clipgo_key.pem -pubout -outform DER -out pub.der
base64 -i pub.der | tr -d '\n'                                       # → manifest.json 的 "key"
shasum -a 256 pub.der | cut -c1-32 | tr '0-9a-f' 'a-p'               # → 新的扩展 ID
```

然后修改三处的 `EXT_ID` / `$ExtId`：`host/install-mac.sh`、`host/install-windows.ps1`，以及 README 里的说明。私钥 `clipgo_key.pem` 不要提交到仓库，只有打包 `.crx` 时才需要。

## 发布新版本

1. 修改 `extension/manifest.json` 的 `version`，以及 `host/clipgo_host.py` 的 `VERSION`
2. 按上面的清单测试
3. 提交并打 tag：`git tag v1.x.x && git push --tags`
4. 通知同事 `git pull`，重新运行安装脚本，在扩展管理页刷新插件，再刷新视频网站页面。升级到 1.2.3 必须同步更新助手及内置插件，才能完整支持抖音后台取源、准备阶段取消、腾讯视频登录与试看检查、JSON 标题 / 路径协议及旧任务路径修复
