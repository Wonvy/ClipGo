# ClipGo

**一键把 YouTube、Instagram、哔哩哔哩、X（Twitter）、小红书、TikTok、抖音和腾讯视频下载为 MP4 的浏览器插件。**
支持 Microsoft Edge 和 Google Chrome，运行在 Windows 10/11 和 macOS 上。

- 鼠标移到视频缩略图上，点一下「⬇ MP4」就开始下载
- 默认 1080p H.264 + AAC：任何播放器、手机和剪辑软件都能直接打开
- 支持最高 4K 画质、YouTube Shorts、Instagram Reels / 帖子视频 / 快拍
- 支持哔哩哔哩 BV / av 视频、b23.tv 视频短链接和分 P 视频的当前 P
- 支持 X（Twitter）视频帖子，兼容 x.com 和 twitter.com 链接
- 支持小红书视频笔记、TikTok / 抖音视频及分享短链接、腾讯视频单个视频或剧集
- 显示下载进度，可以取消、重试，下载完成后弹出系统通知
- 界面跟随浏览器语言，支持简体中文、繁体中文、英文、日文和韩文
- 除 YouTube 需手动开启外，自动使用对应网站在本浏览器的登录状态，不需要另外导出 cookie

> 请只保存你有权下载的内容。DRM 保护内容不支持，实际可用画质取决于视频源和账号观看权限。商店发布需通过各商店审核。

---

## 目录

另有独立的 [Windows 微信视频号工具](tools/wechat_channels/README.md)：默认同时读取微信主界面和独立播放窗口的视频，使用 HTML 卡片界面，支持内置播放、自动同步、下载与 MP4 校验，无需代理或证书。刷新保留已有列表，可单条移除或清空。已在 Windows 微信 4.1.15.56 上成功下载指定的 23 分钟独立窗口视频，并通过完整解码验证。当前视频定位及其他微信版本仍有兼容限制。

- [工作原理](#工作原理)
- [安装](#安装)
- [使用方法](#使用方法)
- [设置](#设置)
- [画质说明](#画质说明)
- [常见问题](#常见问题)
- [更新与卸载](#更新与卸载)
- [项目结构](#项目结构)
- [开发文档](docs/DEVELOPMENT.md)

---

## 工作原理

```
┌────────────── 浏览器 (Edge / Chrome) ──────────────┐        ┌──────── 本机：ClipGo 助手 ────────┐
│                                                    │        │                                   │
│  页面上的「MP4」按钮 ─┐                            │ Native │  clipgo_host.py                   │
│  右键菜单 ────────────┼─► 后台 (background.js) ────┼────────┼─► yt-dlp   下载视频和音频          │
│  插件弹窗 ────────────┘     任务列表 / 进度         │Messaging│   ffmpeg   合并成 MP4               │
│                                                    │        │   deno     处理 YouTube 的 JS 验证   │
└────────────────────────────────────────────────────┘        └───────────────────────────────────┘
```

YouTube 的高清视频是音视频分开的流，还带签名加密，纯浏览器插件很难稳定下载，通常只能拿到 360p，YouTube 一改版就失效。
所以 ClipGo 分成两部分：

| 部分 | 作用 |
|---|---|
| **浏览器插件** (`extension/`) | 界面：页面按钮、右键菜单、弹窗、任务列表 |
| **本地助手** (`host/`) | 真正下载：调用 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载，用 ffmpeg 合成 MP4 |

两者通过浏览器的 [Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) 通信。助手不常驻后台，只在下载时由浏览器启动。

---

## 安装

### Windows 离线安装包

双击 `ClipGo-1.3.0-Windows-x64-Setup.exe`，选择语言并安装。安装包包含下载助手、Python、yt-dlp、FFmpeg、FFprobe 和 Deno，不需要管理员权限，也不需要联网下载运行组件。

安装完成后会打开 450px 宽的本地安装助手。选择 Chrome 或 Edge，点击 **打开浏览器 · 复制扩展页地址**，在浏览器按 Ctrl+L、Ctrl+V、Enter 打开扩展页（浏览器限制外部程序直接跳转内部页面）。开启开发者模式后，点击加载已解压的扩展程序。回到助手复制文件夹路径，在文件夹选择窗口按 Ctrl+L、Ctrl+V、Enter，再选择文件夹。助手提供“打开扩展文件夹”和五种语言指引。已加载过时，点击 ClipGo 的重新加载按钮并刷新视频页面。请保留安装目录。通过开始菜单的 **ClipGo Setup Guide** 可以再次打开助手。

程序安装到 `%LOCALAPPDATA%\ClipGo`。更新前请结束下载并关闭浏览器。Windows“已安装的应用”中的 ClipGo 提供卸载入口；卸载保留设置、日志及已下载的视频，浏览器中的扩展需另行移除。

从源码生成安装包：运行 `powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1`。构建需要 NSIS，运行时版本和 SHA256 固定在 `packaging/windows/runtime-lock.json`。构建产物位于 `dist/`，不包含本机配置、Cookie 或下载记录。

本地安装改进版使用 `powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -LocalSetup` 构建，文件名为 `ClipGo-1.3.0-Local-Windows-x64-Setup.exe`，不会覆盖原商店配套助手安装包。扩展及下载助手功能版本仍为 1.3.0。

### 从源码手动安装

每台电脑需要做两步：**安装本地助手**，**加载浏览器插件**。

### 第 1 步：下载代码

```bash
git clone https://github.com/Wonvy/ClipGo.git
```

也可以在 GitHub 页面点 **Code → Download ZIP** 下载后解压。

### 第 2 步：安装本地助手

助手会下载约 150MB 的组件（yt-dlp、ffmpeg、deno），不需要管理员权限。

#### Windows

双击 **`host\install-windows.bat`**，看到「安装完成」后按任意键关闭窗口。

- 安装位置：`%LOCALAPPDATA%\ClipGo`
- 自带一个仅供助手使用的嵌入式 Python，不需要事先安装 Python，也不影响系统
- 在注册表 `HKCU` 下为 Edge、Chrome、Chromium、Brave 注册

#### macOS

打开「终端」，运行：

```bash
bash ~/Downloads/ClipGo/host/install-mac.sh
```

（把路径换成你放 ClipGo 的位置。）

- 安装位置：`~/Library/Application Support/ClipGo`
- 使用系统自带的 Python 3；如果提示没有，先运行 `xcode-select --install`
- 支持 Apple 芯片 (arm64) 和 Intel (x86_64)

### 第 3 步：加载浏览器插件

1. 打开扩展管理页：Edge 输入 `edge://extensions`，Chrome 输入 `chrome://extensions`
2. 打开 **开发人员模式 / 开发者模式**（Edge 在左侧栏，Chrome 在右上角）
3. 点 **加载解压缩的扩展 / 加载已解压的扩展程序**，选择 ClipGo 里的 **`extension`** 文件夹
4. 确认扩展 ID 是 **`mnhnajpalaecohikjibocdeamfmnpena`**
5. 点工具栏的拼图图标，把 ClipGo 固定到工具栏

点开 ClipGo，右上角显示 **「● 助手已就绪」** 就装好了。

> 扩展 ID 由 `manifest.json` 里的 `key` 固定，所以每台电脑、每个浏览器的 ID 都一样，助手只接受这个 ID 的连接。

---

## 使用方法

### 方式一：页面上的快速下载按钮（最快）

在支持的网站上，把鼠标移到视频的**缩略图**或**正在播放的播放器**上，左上角会出现 **「⬇ MP4」** 按钮。点一下就会按默认画质开始下载。

| 位置 | 支持 |
|---|---|
| YouTube 首页、搜索结果、频道页、侧边栏推荐 | ✅ |
| YouTube Shorts 缩略图 | ✅ |
| YouTube 视频播放页的播放器 | ✅ |
| YouTube 鼠标悬停时的自动预览 | ✅（按钮显示在预览上层） |
| Instagram 信息流里的视频帖子 | ✅ |
| Instagram 个人主页网格、Reels 页面 | ✅ |
| 哔哩哔哩首页、搜索结果、个人空间、推荐视频卡片 | ✅ |
| 哔哩哔哩普通视频播放页的播放器 | ✅（下载当前 P） |
| X 时间线和帖子详情里的视频播放器 | ✅ |
| 小红书视频笔记卡片、详情浮层里的播放器 | ✅ |
| TikTok / 抖音视频卡片和播放器 | ✅ |
| 腾讯视频具体视频或剧集的卡片、播放器 | ✅ |

按钮会显示当前状态：

| 按钮显示 | 含义 |
|---|---|
| `⬇ MP4` | 点击开始下载 |
| `启动中…` → `✓ 已开始` | 已交给助手 |
| `45%` / `合并中…` | 这个视频正在下载 |
| `已下载 · 再下` | 已经下载过，可以再下一次 |
| `失败` + 底部提示 | 查看提示里的原因 |

### 方式二：插件弹窗

1. 打开一个视频页，点工具栏上的 ClipGo 图标
2. 链接会自动填好；也可以粘贴支持的网站视频链接，或包含一个链接的分享文案
3. 选择画质，点 **「下载 MP4」**

弹窗下方的 **下载任务** 列表会显示所有任务的进度，可以 **取消**、**重试**、**打开位置**（在 Finder / 资源管理器中定位文件）。

小红书等网站在首页浮层里播放视频时，弹窗会尝试识别正在播放的视频。没有明确的视频或同时存在多个候选时，请打开具体视频或粘贴分享链接。

哔哩哔哩支持 `bilibili.com/video/BV…`、`bilibili.com/video/av…`（含手机版链接）和指向普通视频的 `b23.tv/…` 短链接。分 P 视频会保留链接里的 `?p=2` 等分 P 参数，只下载所选 P；没有分 P 参数时下载第一 P。当前不支持番剧、直播或整套合集批量下载。

X 支持 `x.com/用户名/status/帖子ID` 和对应的 `twitter.com` 帖子链接。在时间线上，可以悬停视频点「MP4」，也可以在帖子的时间戳上右键下载。首页、个人主页本身不是视频链接；仅含文字或图片的帖子无法下载为视频。一个帖子包含多个视频时，下载结果可能有多个文件；链接末尾的 `/video/2` 等附件序号会保留，用于下载所选附件。

| 网站 | 新增支持的链接与范围 |
|---|---|
| 小红书 | `/explore/笔记ID`、`/discovery/item/笔记ID`、`xhslink.com` 分享短链接；请保留完整分享链接中的 `xsec_token` 等参数。图文笔记不属于视频 |
| TikTok | `/@用户名/video/视频ID`、`vm.tiktok.com`、`vt.tiktok.com`、`www.tiktok.com/t/短码`；不下载个人主页、合集或直播 |
| 抖音 | `/video/视频ID`、页面中已选视频的 `modal_id`、`v.douyin.com` 分享短链接；不下载整个信息流或直播 |
| 腾讯视频 | `v.qq.com/x/page/视频ID.html`、`/x/cover/专辑ID/视频ID.html`；仅处理具体视频或单集，不批量下载整部专辑 |

粘贴抖音 `/video/视频ID` 链接或重试旧任务时，无需先手动打开播放。没有可用的网页视频信息时，扩展会新建后台静音标签页打开目标视频，不弹出独立窗口，也不切走当前标签页；最多等待约 12 秒取得播放地址后自动关闭。如果你接管该标签页或导航到其他页面，扩展会保留它。任务历史只保存视频页链接，不保存临时播放地址。

取源的「准备中」阶段也可以取消：扩展会立即停止准备并清理仍由它使用的标签页，不继续启动下载。

### 方式三：右键菜单

- 在视频**链接**上右键，选 **「用 ClipGo 下载这个视频 (MP4)」**。适合 Instagram 信息流：在帖子的时间戳上右键。
- 在视频**页面**上右键，选 **「用 ClipGo 下载当前视频 (MP4)」**

### 下载到哪里

默认保存在 **`下载/ClipGo`** 文件夹（`~/Downloads/ClipGo`），文件名为 `视频标题 [视频ID].mp4`。保存位置可以在设置里更改。

---

## 设置

点开 ClipGo 弹窗，展开底部的 **设置**：

| 设置 | 说明 | 默认 |
|---|---|---|
| 保存位置 | 点「更改」会弹出系统的文件夹选择框 | `下载/ClipGo` |
| 页面上的快速下载按钮 | 是否在缩略图和播放器上显示「MP4」按钮 | 开 |
| YouTube 使用登录状态 | 把 YouTube 的登录 cookie 交给助手，用于年龄限制、会员视频，或遇到「确认你不是机器人」时 | 关 |
| 下载完成时通知 | 完成后弹出系统通知，点通知可以定位到文件 | 开 |
| 更新 yt-dlp | 视频网站改版导致下载失败时点这里 | — |

弹窗里选的画质会记住，也是页面按钮和右键菜单使用的默认画质。

---

## 画质说明

| 选项 | 视频编码 | 说明 |
|---|---|---|
| **1080p（推荐，默认）** | H.264 + AAC | 兼容性最好：QuickTime、Windows 自带播放器、手机、剪映、Premiere 都能直接打开 |
| 最高画质 (4K) | AV1 / VP9 + AAC | 最高 4K/8K。较老的电脑、QuickTime 和 Windows 自带播放器可能无法播放，建议用 VLC 或浏览器打开 |
| 720p / 480p / 360p | H.264 + AAC | 文件更小 |

如果视频本身达不到所选分辨率，会自动下载可用的最高画质。Instagram 视频通常只有一种画质，画质选项对它基本不起作用。

哔哩哔哩的可用画质取决于视频和账号权限。先在同一个浏览器登录哔哩哔哩，ClipGo 会自动使用该登录状态；选择 1080p 或最高画质并不会获得账号原本没有的观看权限。

---

## 常见问题

**插件显示「未连接助手」/「未检测到 ClipGo 本地助手」**
没有安装助手，或者安装后没有重新加载插件。重新运行安装脚本，然后在扩展管理页点 ClipGo 的刷新按钮。

**「本地助手拒绝了此扩展（扩展 ID 不匹配）」**
扩展 ID 不是 `mnhnajpalaecohikjibocdeamfmnpena`。确认 `manifest.json` 里的 `key` 字段没有被删除或修改。

**Instagram 提示需要登录**
先在**同一个浏览器**里登录 instagram.com，再下载。ClipGo 会把 Instagram 的登录 cookie 写进一个临时文件交给助手，下载结束后立即删除。

**哔哩哔哩提示需要登录，或下载画质低于预期**
先在**同一个浏览器**里登录 bilibili.com，确认账号能播放该视频及所需画质，再重试。ClipGo 会自动把哔哩哔哩的登录 cookie 通过临时文件交给助手，任务结束后删除；仅下载账号有权观看的内容和画质。

**哔哩哔哩提示请求受限（412 / 429）**
请稍后重试，并确认同一浏览器能正常播放视频。如果网页可以播放而下载持续失败，尝试在设置里更新 yt-dlp。

**X 视频提示需要登录或无法访问**
先在**同一个浏览器**里登录 X，确认账号能播放该帖子的视频后重试。ClipGo 会自动使用 `x.com` / `twitter.com` 的登录状态，下载结束后删除助手的临时 cookie 文件。

**小红书、TikTok 或抖音提示需要登录、验证或新 cookie**
在同一个浏览器完成网站要求的正常登录或验证，再重试。抖音重试会重新尝试取得目标视频的播放地址；如果网站仍要求登录或验证，需要你在视频页正常完成后再试。小红书请重新复制完整分享链接，保留访问令牌。请求过于频繁时请稍后重试。

**腾讯视频提示没有权限或视频受到加密保护**
确认当前账号能够播放该视频。ClipGo 会传递当前浏览器的正常登录状态，并检查返回的视频是否只是试看；检测到仅有试看片段时会报错，不会把它当作完整视频保存。普通 HLS AES-128 由现有下载引擎按标准处理；DRM 和未知加密方法不在支持范围内。遇到权限、地区限制或无法确认完整性时会显示原因，登录不保证所有会员视频都能下载。

**下载任务的中文标题乱码，或「打开位置」找不到文件**
升级助手和扩展到 1.2.3 后刷新扩展。新版修复中文和 emoji 标题、文件路径的传递；旧任务会尝试从当前下载目录恢复唯一匹配的文件路径和标题。若文件已移走或存在多个匹配，任务会回退显示视频链接，可在原保存位置手动查找。

**YouTube 提示「请登录以确认你不是机器人」、年龄限制或会员视频**
在同一个浏览器里登录 YouTube，并在设置中打开 **「YouTube 使用登录状态」**。

**YouTube 突然全部下载失败**
通常是 YouTube 改版了。在设置里点 **「更新 yt-dlp」**，或重新运行一次安装脚本。

**下载很慢或超时**
助手使用系统的网络设置。如果你通过代理访问 YouTube，请确认是系统级代理或开启了 TUN 模式，而不只是浏览器代理。

**页面上没有出现「MP4」按钮**
- 插件更新或重新加载后，已经打开的页面需要**刷新一次**
- 检查设置里的 **「页面上的快速下载按钮」** 是否打开
- 按钮只在足够大的缩略图（宽 100px 以上）和播放器上出现

**macOS 弹出「Python.framework 已损坏，无法打开」**
旧版安装脚本才会出现这个问题。重新运行 `install-mac.sh` 即可：新版改用解压版 yt-dlp，不会再触发 macOS 的隔离检查。

**Windows 安装时杀毒软件报警**
yt-dlp.exe 是用 PyInstaller 打包的程序，偶尔会被误报。它的来源是 yt-dlp 官方 GitHub 发布页，可以放心添加信任。

**怎么排查问题**
助手的日志在安装目录下的 `host.log`：
- Windows：`%LOCALAPPDATA%\ClipGo\host.log`
- macOS：`~/Library/Application Support/ClipGo/host.log`

---

## 更新与卸载

**更新 ClipGo**：`git pull`（或重新下载 ZIP），然后：
1. 重新运行安装脚本（会同时更新 yt-dlp）
2. 在扩展管理页点 ClipGo 的刷新按钮
3. 刷新已经打开的视频网站页面

升级到 **1.2.3** 需要完成以上三步：新版支持八个平台，修复标题和路径乱码，抖音通过后台静音标签页自动取源，并支持准备阶段立即取消；腾讯视频保留登录和试看识别。旧版助手无法完整支持，仅刷新扩展不足以生效。

**只更新 yt-dlp**：插件设置里点「更新 yt-dlp」。

**卸载**：
- Windows：双击 `host\uninstall-windows.bat`
- macOS：`bash host/uninstall-mac.sh`
- 然后在扩展管理页移除 ClipGo

卸载不会删除已经下载的视频。

---

## 项目结构

```
ClipGo/
├── extension/                 浏览器插件（Manifest V3，Edge / Chrome 通用）
│   ├── manifest.json          插件配置（含固定 ID 用的 key）
│   ├── background.js          后台：任务管理、与助手通信、右键菜单、通知
│   ├── content.js             页面脚本：缩略图 / 播放器上的「MP4」按钮
│   ├── douyin-media.js        抖音网页已加载的视频信息解析
│   ├── douyin-page.js         抖音正常网页响应的播放信息传递
│   ├── common.js              共用：链接识别、画质选项、默认设置
│   ├── popup.html/css/js      工具栏弹窗
│   └── icons/
├── host/                      本地助手
│   ├── clipgo_host.py         Native Messaging 助手（Windows / macOS 通用）
│   ├── plugins/               腾讯视频登录与完整性检查插件
│   ├── install-windows.bat    Windows 安装入口（调用 .ps1）
│   ├── install-windows.ps1
│   ├── uninstall-windows.bat / .ps1
│   ├── install-mac.sh
│   └── uninstall-mac.sh
├── tools/
│   └── nm_client.py           模拟浏览器测试本地助手
├── tests/                    链接识别、页面按钮、后台与助手的回归测试
└── docs/
    └── DEVELOPMENT.md         开发文档：架构、通信协议、调试、发布
```

想修改或参与开发，请看 **[开发文档](docs/DEVELOPMENT.md)**。

## 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：视频下载引擎
- [FFmpeg](https://ffmpeg.org/)：音视频合并（Windows 版来自 [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)，macOS 版来自 [martin-riedl.de](https://ffmpeg.martin-riedl.de/)）
- [Deno](https://deno.com/)：yt-dlp 处理 YouTube 时需要的 JavaScript 运行时
