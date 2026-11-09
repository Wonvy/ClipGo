# 视频号 Windows 安装包

这是独立的 **ClipGo 视频号 1.0.0** 安装器，适用于 Windows 10/11 x64、.NET Framework 4.8 或更新版本。应用文件和 Python 已包含在包中；缺少 WebView2 时，普通安装会提示联网并运行包内经过签名、哈希校验的微软官方引导安装程序。

默认安装到 `%LOCALAPPDATA%\Programs\ClipGoChannels`，使用当前用户的开始菜单和卸载项。桌面快捷方式可选。卸载只删除构建时登记的包文件及空目录，保留已下载视频和额外文件；不卸载共享 WebView2 运行时。

先生成完整的发布应用目录和许可证文件，再编译安装包：

```powershell
./packaging/wechat_channels/build-installer.ps1 -Payload ./dist/ClipGoChannels-Release
```

默认使用本机 NSIS Unicode。可传 `-Compiler`、`-WebView2Bootstrapper`、`-Icon`、`-Output`、`-Version`。WebView2 文件默认来自 `build/wechat-release/cache/MicrosoftEdgeWebview2Setup.exe`，必须与 `runtime-lock.json` 的 SHA256 相符且带有有效微软签名。安装器使用 zlib 压缩。

交付文件为 `dist/ClipGoChannels-1.0.0-Windows-x64-Setup.exe` 及其 `.sha256`；不要把测试安装包发给朋友。

隔离测试使用另行编译的测试模式，复制与卸载步骤和发布版相同，但编译时排除了注册表、开始菜单、桌面写入和运行时安装：

```powershell
./packaging/wechat_channels/build-installer.ps1 -Payload ./dist/ClipGoChannels-Release -TestMode
./packaging/wechat_channels/test-installer.ps1
```

测试目录位于 `build/wechat-release-tests/`。测试实际静默安装、逐文件核对 SHA256、验证文件占用时不会部分升级、重新安装、运行隐藏的 HTML/WebView2 诊断并卸载；同时验证额外文件和模拟下载文件仍在。测试诊断可以在没有打开微信时验证空列表界面；打开微信并播放视频后还可验证真实互动数已进入界面。测试报告不会包含媒体地址或解码密钥。测试不会运行正常安装向导或修改系统组件。
