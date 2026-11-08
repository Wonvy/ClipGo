# ClipGo

**一键把 YouTube / Instagram 视频下载为 MP4 的浏览器插件。**
支持 Microsoft Edge 和 Google Chrome，运行在 Windows 10/11 和 macOS 上。

- 鼠标移到视频缩略图上，点一下「⬇ MP4」就开始下载
- 默认 1080p H.264 + AAC：任何播放器、手机和剪辑软件都能直接打开
- 支持最高 4K 画质、YouTube Shorts、Instagram Reels / 帖子视频 / 快拍
- 显示下载进度，可以取消、重试，下载完成后弹出系统通知
- 自动使用浏览器里的 Instagram 登录状态，不需要另外导出 cookie

> ⚠️ ClipGo 仅供个人和团队内部使用。YouTube 服务条款不允许下载视频，Chrome 应用商店也禁止这类插件上架。请只下载你有权保存的内容。

---

## 目录

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

在 YouTube 或 Instagram 上，把鼠标移到任意视频的**缩略图**或**正在播放的播放器**上，左上角会出现 **「⬇ MP4」** 按钮。点一下就会按默认画质开始下载，不会打开视频页。

| 位置 | 支持 |
|---|---|
| YouTube 首页、搜索结果、频道页、侧边栏推荐 | ✅ |
| YouTube Shorts 缩略图 | ✅ |
| YouTube 视频播放页的播放器 | ✅ |
| YouTube 鼠标悬停时的自动预览 | ✅（按钮显示在预览上层） |
| Instagram 信息流里的视频帖子 | ✅ |
| Instagram 个人主页网格、Reels 页面 | ✅ |

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
2. 链接会自动填好；也可以粘贴任意 YouTube / Instagram 视频链接
3. 选择画质，点 **「下载 MP4」**

弹窗下方的 **下载任务** 列表会显示所有任务的进度，可以 **取消**、**重试**、**打开位置**（在 Finder / 资源管理器中定位文件）。

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
| 更新 yt-dlp | YouTube 改版导致下载失败时点这里 | — |

弹窗里选的画质会记住，也是页面按钮和右键菜单使用的默认画质。

---

## 画质说明

| 选项 | 视频编码 | 说明 |
|---|---|---|
| **1080p（推荐，默认）** | H.264 + AAC | 兼容性最好：QuickTime、Windows 自带播放器、手机、剪映、Premiere 都能直接打开 |
| 最高画质 (4K) | AV1 / VP9 + AAC | 最高 4K/8K。较老的电脑、QuickTime 和 Windows 自带播放器可能无法播放，建议用 VLC 或浏览器打开 |
| 720p / 480p / 360p | H.264 + AAC | 文件更小 |

如果视频本身达不到所选分辨率，会自动下载可用的最高画质。Instagram 视频通常只有一种画质，画质选项对它基本不起作用。

---

## 常见问题

**插件显示「未连接助手」/「未检测到 ClipGo 本地助手」**
没有安装助手，或者安装后没有重新加载插件。重新运行安装脚本，然后在扩展管理页点 ClipGo 的刷新按钮。

**「本地助手拒绝了此扩展（扩展 ID 不匹配）」**
扩展 ID 不是 `mnhnajpalaecohikjibocdeamfmnpena`。确认 `manifest.json` 里的 `key` 字段没有被删除或修改。

**Instagram 提示需要登录**
先在**同一个浏览器**里登录 instagram.com，再下载。ClipGo 会把 Instagram 的登录 cookie 写进一个临时文件交给助手，下载结束后立即删除。

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
3. 刷新已经打开的 YouTube / Instagram 页面

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
│   ├── common.js              共用：链接识别、画质选项、默认设置
│   ├── popup.html/css/js      工具栏弹窗
│   └── icons/
├── host/                      本地助手
│   ├── clipgo_host.py         Native Messaging 助手（Windows / macOS 通用）
│   ├── install-windows.bat    Windows 安装入口（调用 .ps1）
│   ├── install-windows.ps1
│   ├── uninstall-windows.bat / .ps1
│   ├── install-mac.sh
│   └── uninstall-mac.sh
├── tools/
│   └── nm_client.py           模拟浏览器测试本地助手
└── docs/
    └── DEVELOPMENT.md         开发文档：架构、通信协议、调试、发布
```

想修改或参与开发，请看 **[开发文档](docs/DEVELOPMENT.md)**。

## 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：视频下载引擎
- [FFmpeg](https://ffmpeg.org/)：音视频合并（Windows 版来自 [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)，macOS 版来自 [martin-riedl.de](https://ffmpeg.martin-riedl.de/)）
- [Deno](https://deno.com/)：yt-dlp 处理 YouTube 时需要的 JavaScript 运行时
