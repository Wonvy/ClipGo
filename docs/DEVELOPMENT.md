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
- **一次性操作走短连接**：`ping`、`reveal`、`chooseDir`、`update` 用 `sendNativeMessage`，每次启动一个独立的助手进程。
- **service worker 重启 = 连接断开**：后台启动时把仍处于进行中的任务标记为「任务被中断」，因为连接断开时助手已经结束了正在下载的任务。

## 插件 extension/

| 文件 | 职责 |
|---|---|
| `manifest.json` | MV3 配置。`key` 固定扩展 ID；`host_permissions` 用于读取 YouTube/Instagram 的 cookie 和注入页面脚本 |
| `common.js` | `CLIPGO.detect(url)`：识别并规范化视频链接；画质选项；默认设置。弹窗、后台 (`importScripts`) 和页面脚本共用 |
| `background.js` | 任务管理、助手连接、cookie 收集、错误提示翻译、右键菜单、通知、角标 |
| `content.js` | 页面上的悬浮「MP4」按钮 |
| `popup.*` | 工具栏弹窗：链接输入、画质、任务列表、设置 |

### 链接识别 `CLIPGO.detect`

返回 `null`（不支持的网站）或 `{ site, isVideo, url }`，其中 `url` 是去掉跟踪参数后的规范链接：

| 输入 | 输出 url |
|---|---|
| `youtube.com/watch?v=ID&list=…&pp=…`、`m.`、`music.` | `https://www.youtube.com/watch?v=ID` |
| `youtu.be/ID?si=…` | `https://www.youtube.com/watch?v=ID` |
| `youtube.com/shorts/ID` | `https://www.youtube.com/shorts/ID` |
| `youtube.com/live/ID`、`/embed/ID` | `https://www.youtube.com/watch?v=ID` |
| `instagram.com/reel/ID`、`/reels/ID`、`/用户名/reel/ID` | `https://www.instagram.com/reel/ID/` |
| `instagram.com/p/ID`、`/tv/ID` | `https://www.instagram.com/p/ID/` |
| `instagram.com/stories/用户名/数字ID` | 原链接 |

规范化后，同一个视频在不同页面的链接都相同，这样「正在下载」检查和按钮进度才能对得上。

### 任务对象

```js
{
  id, url, site,            // site: "youtube" | "instagram"
  quality,                  // "1080" | "best" | "720" | "480" | "360"
  status,                   // queued → running → merging → done | error | cancelled
  stage,                    // 给人看的阶段文字
  percent, speed, eta, title,
  files: [],                // 完成后的文件绝对路径
  error, rawError, note,
  createdAt, finishedAt
}
```

最多保留 30 条已结束的任务。进度写入 `storage` 有 300ms 节流。

### 页面按钮 content.js

- 只用**一个**按钮，放在 `<body>` 下的 closed shadow root 里（`<clipgo-quick>`），用 `position: fixed` 定位到目标元素的左上角。按钮不在页面的 `<a>` 里，所以点击不会触发跳转；z-index 最大，能盖在 YouTube 的悬停预览上面。
- 目标检测（`findTarget`），按顺序：
  1. 指针下最近的 `<a>`，链接是视频，并且尺寸 ≥ 100×56（用来排除标题链接）
  2. `document.elementsFromPoint` 里的 `<video>`（能穿过覆盖层）：
     - YouTube `#movie_player` → 当前页面链接
     - YouTube `ytd-video-preview` → 预览里的链接
     - Instagram `<article>` → 帖子里的 `/p/`、`/reel/` 链接；否则用页面链接；再否则向上找 8 层
- 目标元素被替换，或页面滚动时，会重新检测指针下的元素（`relookup`），而不是直接隐藏按钮。
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
- 只接受 `youtube.com`、`youtu.be`、`instagram.com` 及其子域名的 http(s) 链接
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
| `openDir` | — | `{type:"ok"}` |
| `chooseDir` | — | `{type:"dir", downloadDir}`（用户取消时返回原目录） |
| `update` | — | `{type:"updated", ok, output, ytdlp}` |

`cookies` 的元素格式与 `chrome.cookies.Cookie` 相同（`domain, hostOnly, path, secure, expirationDate, name, value`），助手会转成 Netscape `cookies.txt` 格式。

### 助手 → 插件（下载过程）

```jsonc
{"type": "stage",    "id": "…", "stage": "解析视频信息…"}
{"type": "progress", "id": "…", "percent": 46.2, "speed": "3.2MiB/s", "eta": "00:04", "title": "…"}
{"type": "stage",    "id": "…", "stage": "合并音视频为 MP4…"}
{"type": "done",     "id": "…", "files": ["/…/标题 [ID].mp4"], "note": ""}
{"type": "error",    "id": "…", "message": "yt-dlp 的原始错误"}
{"type": "cancelled","id": "…"}
```

进度推送节流为每 0.4 秒最多一次（100% 时一定推送）。`background.js` 的 `friendlyError()` 会把常见的 yt-dlp 错误翻译成中文提示。

助手发现 stdin 关闭（浏览器断开连接）时，会结束所有正在进行的任务再退出。

## 下载命令与画质策略

助手实际执行的命令（`build_command`）：

```
yt-dlp --ignore-config --no-playlist --no-mtime --newline --progress
       --progress-template "download:CLIPGO_P:%(progress._percent_str)s|…|%(info.title)s"
       --print "after_move:CLIPGO_F:%(filepath)s" --no-quiet
       --ffmpeg-location <bin>
       -f "bv*+ba/b" -S <排序规则>
       --merge-output-format mp4 --remux-video mp4
       -o "<下载目录>/%(title).120B [%(id)s].%(ext)s"
       --js-runtimes deno:<bin/deno>
       [--cookies <临时文件>]
       -- <url>
```

- 进度和最终文件路径用 `CLIPGO_P:` / `CLIPGO_F:` 前缀输出，助手逐行解析。
- `--print` 会自动开启 `--quiet`，所以要加 `--no-quiet`，否则看不到 `[Merger]` 行，界面就无法显示「合并中」。
- `--ignore-config` 避免用户自己的 yt-dlp 配置干扰。

画质用 `-S`（格式排序）而不是写死的 `-f`，这样视频达不到所选分辨率时会自动降级：

| 选项 | `-S` | 实际效果（4K 视频实测） |
|---|---|---|
| `1080` | `res:1080,+codec:avc:m4a` | 1920×1080 avc1 + mp4a |
| `720` 等 | `res:720,+codec:avc:m4a` | 1280×720 avc1 + mp4a |
| `best` | `res,ext:mp4:m4a` | 3840×2160 av01 + mp4a |

`+codec:avc:m4a` 让编码偏好变成 H.264 > H.265 > VP9 > AV1，音频偏好 AAC，保证兼容性。

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

想不联网测试进度解析、错误处理和取消，可以在临时目录放一份 `clipgo_host.py`，`bin/` 里放一个假的 `yt-dlp` 脚本，按协议格式输出 `CLIPGO_P:…` 和 `CLIPGO_F:…` 行。

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
4. 通知同事 `git pull`，重新运行安装脚本，然后在扩展管理页刷新插件
