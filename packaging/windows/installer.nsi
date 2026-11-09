; -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!ifndef VERSION
  !error "Build with build.ps1"
!endif
Name "ClipGo ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\ClipGo"
RequestExecutionLevel user
ManifestDPIAware true
ManifestSupportedOS all
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails nevershow
ShowUninstDetails nevershow
BrandingText "ClipGo"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "ClipGo"
VIAddVersionKey /LANG=2052 "FileDescription" "ClipGo Windows 离线安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "ClipGo contributors"

Var TestMode
Var Result
Var PsExe

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "$(welcomeTitle)"
!define MUI_WELCOMEPAGE_TEXT "$(welcomeText)"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW WelcomeShow
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "$(finishTitle)"
!define MUI_FINISHPAGE_TEXT "$(finishText)"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "$(openGuide)"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenGuide
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "Korean"
!include "${LANGFILE}"

Function WelcomeShow
  GetDlgItem $0 $HWNDPARENT 1
  SendMessage $0 ${WM_SETTEXT} 0 "STR:$(install)"
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(needsWin)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "$(needsWin)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  SetShellVarContext current
  StrCpy $PsExe "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/TEST" $1
  ${IfNot} ${Errors}
    StrCpy $TestMode "1"
  ${EndIf}
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "安装 ClipGo"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r /x "__pycache__" /x "*.pyc" "${PAYLOAD}\*"
  SetOutPath "$PLUGINSDIR"
  File "install-runtime.ps1"
  DetailPrint "$(installing)"
  ${If} $TestMode == "1"
    nsExec::Exec '"$PsExe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\install-runtime.ps1" -PayloadDir "$PLUGINSDIR\payload" -AppDir "$INSTDIR" -SkipRegistration'
  ${Else}
    nsExec::Exec '"$PsExe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\install-runtime.ps1" -PayloadDir "$PLUGINSDIR\payload" -AppDir "$INSTDIR"'
  ${EndIf}
  Pop $Result
  ${If} $Result != 0
    MessageBox MB_ICONSTOP "$(installFailed)$\r$\n$INSTDIR\install.log" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  File "uninstall-runtime.ps1"
  File "open-guide.ps1"
  File /oname=ClipGo.ico "${ICON}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteINIStr "$INSTDIR\installer-language.ini" "Installer" "Language" "$LANGUAGE"
  ${If} $TestMode != "1"
    CreateDirectory "$SMPROGRAMS\ClipGo"
    CreateShortcut "$SMPROGRAMS\ClipGo\ClipGo Setup Guide.lnk" "$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" '-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\open-guide.ps1"' "$INSTDIR\ClipGo.ico"
    CreateShortcut "$SMPROGRAMS\ClipGo\Uninstall ClipGo.lnk" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "DisplayName" "ClipGo ${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "DisplayIcon" "$INSTDIR\ClipGo.ico"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "NoRepair" 1
  ${EndIf}
  SetErrorLevel 0
SectionEnd

Function OpenGuide
  ${If} $TestMode != "1"
    Exec '"$PsExe" -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\open-guide.ps1"'
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  StrCpy $PsExe "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  ReadINIStr $LANGUAGE "$INSTDIR\installer-language.ini" "Installer" "Language"
FunctionEnd

Section "Uninstall"
  nsExec::Exec '"$PsExe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\uninstall-runtime.ps1" -AppDir "$INSTDIR"'
  Pop $Result
  ${If} $Result != 0
    MessageBox MB_ICONSTOP "$(uninstallFailed)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo" "InstallLocation"
  ${If} $0 == $INSTDIR
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo"
    Delete "$SMPROGRAMS\ClipGo\ClipGo Setup Guide.lnk"
    Delete "$SMPROGRAMS\ClipGo\Uninstall ClipGo.lnk"
    RMDir "$SMPROGRAMS\ClipGo"
  ${EndIf}
  Delete "$INSTDIR\open-guide.ps1"
  Delete "$INSTDIR\uninstall-runtime.ps1"
  Delete "$INSTDIR\ClipGo.ico"
  Delete "$INSTDIR\installer-language.ini"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  SetErrorLevel 0
SectionEnd
