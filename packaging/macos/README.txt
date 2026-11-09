ClipGo for macOS — 本地安装

这是联网安装包，不是 Windows EXE，也不是经过 Apple 签名、公证的 PKG。
现有安装脚本会按 Apple 芯片 arm64 / Intel x86_64 下载对应组件。
打包和文件检查已完成；当前开发环境为 Windows，尚未在 Mac 真机验收。

1. 把 ZIP 复制到 Mac，解压，打开 ClipGo 文件夹。
2. 双击 Install ClipGo.command，终端会显示安装进度。
   如果不能双击运行：打开「终端」，输入 bash 和一个空格，
   将 Install ClipGo.command 拖进终端，再按回车。
3. 如果提示缺少 /usr/bin/python3，请先在终端运行：
      xcode-select --install
   完成 Apple 命令行工具安装后，再运行 ClipGo 安装入口。
4. 安装需要联网访问组件下载地址。完成后会打开扩展文件夹。
5. 在 Chrome 地址栏输入 chrome://extensions
   或在 Edge 地址栏输入 edge://extensions
   开启开发者模式，点击加载已解压的扩展程序。
6. 文件夹选择窗口按 Command+Shift+G，粘贴下面的路径并确认：
      ~/Library/Application Support/ClipGo/extension
   选择文件夹，打开 ClipGo，检查助手已就绪。已有视频页面需刷新。

可以删除下载的 ZIP 和解压的安装包。请保留 Library 下的安装目录。
默认视频保存到 ~/Downloads/ClipGo。
本包用于 Chrome / Edge；不是 Safari 扩展。
如果 macOS 拦截某个组件，保留提示并核实来源，不要关闭系统安全保护。

English
Extract the ZIP on your Mac and run Install ClipGo.command.
Alternatively, type bash and a space in Terminal, drag that file into Terminal,
then press Enter. An internet connection and /usr/bin/python3 are required.
If Python is missing, install Apple's Command Line Tools with
xcode-select --install, then run the installer again.
Load ~/Library/Application Support/ClipGo/extension using Developer mode in
Chrome or Edge. Press Command+Shift+G in the folder picker to enter this path.
Keep the installed folder. The download archive can be removed after setup.
This is a source installer, not a signed/notarized Mac application or PKG.
It has not yet been validated on a physical Mac.
