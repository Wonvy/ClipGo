; -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WordFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!ifndef VERSION
  !error "Use build-installer.ps1 to build this installer."
!endif
!define APPID "ClipGoChannels"
!define APPNAME "ClipGo 视频号"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
!define WEBVIEW_GUID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
!include "${PAYLOAD_INCLUDE}"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\${APPID}"
RequestExecutionLevel user
ManifestDPIAware true
ManifestSupportedOS all
SetCompressor zlib
SetOverwrite on
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APPNAME}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APPNAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "ClipGo contributors"

Var RuntimeInstalled
Var RuntimeVersion
Var Result
Var FrameworkRelease

; Keep the same official bootstrapper bytes in release and isolated test builds.
ReserveFile "${WEBVIEW_BOOTSTRAPPER}"

!define MUI_ABORTWARNING
!ifdef ICON
  !define MUI_ICON "${ICON}"
  !define MUI_UNICON "${ICON}"
!endif
!define MUI_WELCOMEPAGE_TITLE "$(WelcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(WelcomeText)"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(FinishTitle)"
!define MUI_FINISHPAGE_TEXT "$(FinishText)"
!define MUI_FINISHPAGE_RUN "$INSTDIR\ClipGoChannels.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(LaunchApp)"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

LangString WelcomeTitle ${LANG_ENGLISH} "Install ClipGo Channels ${VERSION}"
LangString WelcomeTitle ${LANG_SIMPCHINESE} "安装 ${APPNAME} ${VERSION}"
LangString WelcomeText ${LANG_ENGLISH} "Save videos you have opened in WeChat. This installs for your Windows account only.$\r$\n$\r$\nPython and application files are included. An internet connection is needed only if Microsoft Edge WebView2 Runtime is missing.$\r$\n$\r$\nClose ClipGo Channels before upgrading. Downloaded videos are kept when uninstalling."
LangString WelcomeText ${LANG_SIMPCHINESE} "保存你在微信中打开的视频。本软件仅为当前 Windows 用户安装。$\r$\n$\r$\n已包含 Python 和应用文件；如果电脑缺少 Microsoft Edge WebView2 运行时，安装该组件时需要联网。$\r$\n$\r$\n升级前请关闭视频号助手。卸载不会删除已下载的视频。"
LangString FinishTitle ${LANG_ENGLISH} "ClipGo Channels is installed"
LangString FinishTitle ${LANG_SIMPCHINESE} "${APPNAME} 已安装"
LangString FinishText ${LANG_ENGLISH} "Open WeChat and play a Channels video, then open ClipGo Channels. The list refreshes automatically.$\r$\n$\r$\nThis installer does not change WeChat, network proxy or HTTPS certificates."
LangString FinishText ${LANG_SIMPCHINESE} "在微信中播放视频号视频，然后打开视频号助手；列表会自动刷新。$\r$\n$\r$\n本安装程序不会修改微信、网络代理或 HTTPS 证书。"
LangString LaunchApp ${LANG_ENGLISH} "Open ClipGo Channels"
LangString LaunchApp ${LANG_SIMPCHINESE} "打开${APPNAME}"
LangString MainComponent ${LANG_ENGLISH} "Application files (required)"
LangString MainComponent ${LANG_SIMPCHINESE} "应用文件（必需）"
LangString DesktopComponent ${LANG_ENGLISH} "Desktop shortcut"
LangString DesktopComponent ${LANG_SIMPCHINESE} "桌面快捷方式"
LangString NeedsWindows ${LANG_ENGLISH} "Windows 10 or 11 (64-bit) is required."
LangString NeedsWindows ${LANG_SIMPCHINESE} "需要 64 位 Windows 10 或 Windows 11。"
LangString NeedsFramework ${LANG_ENGLISH} "Microsoft .NET Framework 4.8 or later is required. Install it from https://dotnet.microsoft.com/download/dotnet-framework/net48 and run this setup again."
LangString NeedsFramework ${LANG_SIMPCHINESE} "需要 Microsoft .NET Framework 4.8 或更新版本。请从以下微软官网安装后，再运行本安装程序：$\r$\nhttps://dotnet.microsoft.com/download/dotnet-framework/net48"
LangString NeedsWebView ${LANG_ENGLISH} "Microsoft Edge WebView2 Runtime is missing. Setup will download and install it from Microsoft. An internet connection is required. Continue?"
LangString NeedsWebView ${LANG_SIMPCHINESE} "电脑缺少 Microsoft Edge WebView2 运行时。接下来将通过微软安装程序下载并安装，需要联网。是否继续？"
LangString WebViewFailed ${LANG_ENGLISH} "WebView2 Runtime could not be installed. Check your internet connection or install the Evergreen Runtime from https://developer.microsoft.com/microsoft-edge/webview2 and try again. Application files have not been installed."
LangString WebViewFailed ${LANG_SIMPCHINESE} "WebView2 运行时未能安装。请检查网络，或从 https://developer.microsoft.com/microsoft-edge/webview2 安装 Evergreen 运行时后重试。尚未安装应用文件。"
LangString CloseApp ${LANG_ENGLISH} "Please close ClipGo Channels and try again. The application file is in use or the selected folder is not writable."
LangString CloseApp ${LANG_SIMPCHINESE} "请先关闭视频号助手后重试。应用文件正在使用中，或所选文件夹不可写。"
LangString InstallingRuntime ${LANG_ENGLISH} "Installing Microsoft Edge WebView2 Runtime..."
LangString InstallingRuntime ${LANG_SIMPCHINESE} "正在安装 Microsoft Edge WebView2 运行时…"
LangString RuntimeTestMissing ${LANG_ENGLISH} "Isolated test mode requires an existing WebView2 Runtime; it never installs system components."
LangString RuntimeTestMissing ${LANG_SIMPCHINESE} "隔离测试模式需要已有 WebView2 运行时，不会安装系统组件。"

!macro CheckRuntime ROOT VIEW
  SetRegView ${VIEW}
  ReadRegStr $RuntimeVersion ${ROOT} "Software\Microsoft\EdgeUpdate\Clients\${WEBVIEW_GUID}" "pv"
  ${If} $RuntimeVersion != ""
    ${VersionCompare} $RuntimeVersion "0.0.0.0" $Result
    ${If} $Result == 1
      StrCpy $RuntimeInstalled 1
    ${EndIf}
  ${EndIf}
!macroend

Function DetectWebView
  StrCpy $RuntimeInstalled 0
  !insertmacro CheckRuntime HKLM 32
  !insertmacro CheckRuntime HKCU 32
  !insertmacro CheckRuntime HKLM 64
  !insertmacro CheckRuntime HKCU 64
  SetRegView 64
FunctionEnd

Function EnsureWebView
  Call DetectWebView
  ${If} $RuntimeInstalled == 1
    Return
  ${EndIf}
  !ifdef TEST_MODE
    MessageBox MB_ICONSTOP "$(RuntimeTestMissing)" /SD IDOK
    SetErrorLevel 2
    Abort
  !else
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(NeedsWebView)" /SD IDOK IDCANCEL runtime_cancelled
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File /oname=MicrosoftEdgeWebview2Setup.exe "${WEBVIEW_BOOTSTRAPPER}"
    DetailPrint "$(InstallingRuntime)"
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install' $Result
    Call DetectWebView
    ${If} $RuntimeInstalled != 1
      MessageBox MB_ICONSTOP "$(WebViewFailed)" /SD IDOK
      SetErrorLevel 1603
      Abort
    ${EndIf}
    Return
    runtime_cancelled:
      SetErrorLevel 1602
      Abort
  !endif
FunctionEnd

Function CheckApplicationClosed
  IfFileExists "$INSTDIR\ClipGoChannels.exe" 0 app_not_installed
  ; OPEN_EXISTING and no truncation: request exclusive write access without
  ; changing a byte, before copying any DLL or other application file.
  System::Call 'kernel32::CreateFileW(w "$INSTDIR\ClipGoChannels.exe", i 0x40000000, i 0, p 0, i 3, i 0, p 0) p.r0'
  ${If} $0 == -1
    MessageBox MB_ICONSTOP "$(CloseApp)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r0)'
  app_not_installed:
FunctionEnd

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(NeedsWindows)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "$(NeedsWindows)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ReadRegDWORD $FrameworkRelease HKLM "SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" "Release"
  ${If} $FrameworkRelease < 528040
    SetRegView 32
    ReadRegDWORD $FrameworkRelease HKLM "SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" "Release"
    SetRegView 64
  ${EndIf}
  ${If} $FrameworkRelease < 528040
    MessageBox MB_ICONSTOP "$(NeedsFramework)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "$(MainComponent)" Main
  SectionIn RO
  Call CheckApplicationClosed
  Call EnsureWebView
  !insertmacro InstallPayload
  SetOutPath "$INSTDIR"
  File /oname=package-manifest.json "${PACKAGE_MANIFEST}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteINIStr "$INSTDIR\installer-info.ini" "Installer" "Language" "$LANGUAGE"
  WriteINIStr "$INSTDIR\installer-info.ini" "Installer" "Version" "${VERSION}"
  !ifndef TEST_MODE
    CreateDirectory "$SMPROGRAMS\${APPNAME}"
    CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\ClipGoChannels.exe"
    CreateShortcut "$SMPROGRAMS\${APPNAME}\卸载.lnk" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APPNAME}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "ClipGo contributors"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\ClipGoChannels.exe"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${PAYLOAD_SIZE_KB}
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  !endif
  SetErrorLevel 0
SectionEnd

Section /o "$(DesktopComponent)" Desktop
  !ifndef TEST_MODE
    CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\ClipGoChannels.exe"
  !endif
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  ReadINIStr $LANGUAGE "$INSTDIR\installer-info.ini" "Installer" "Language"
FunctionEnd

Section "Uninstall"
  ; Check the running EXE first; never partially remove a live application.
  ClearErrors
  Delete "$INSTDIR\ClipGoChannels.exe"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "$(CloseApp)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  !insertmacro UninstallPayload
  !ifndef TEST_MODE
    ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ${If} $0 == $INSTDIR
      DeleteRegKey HKCU "${UNINSTALL_KEY}"
      Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
      Delete "$SMPROGRAMS\${APPNAME}\卸载.lnk"
      RMDir "$SMPROGRAMS\${APPNAME}"
      Delete "$DESKTOP\${APPNAME}.lnk"
    ${EndIf}
  !endif
  Delete "$INSTDIR\package-manifest.json"
  Delete "$INSTDIR\installer-info.ini"
  Delete "$INSTDIR\Uninstall.exe"
  ; No recursive delete: unrelated files and nonempty directories are retained.
  RMDir "$INSTDIR"
  SetErrorLevel 0
SectionEnd
