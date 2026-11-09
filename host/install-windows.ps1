# ClipGo 本地助手安装脚本 (Windows 10/11)
# 双击 install-windows.bat 运行即可；加 --force 会重新下载 Python / ffmpeg / deno。
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ExtId    = 'mnhnajpalaecohikjibocdeamfmnpena'
$HostName = 'com.clipgo.host'
$PyVer    = '3.12.10'
$AppDir   = Join-Path $env:LOCALAPPDATA 'ClipGo'
$Bin      = Join-Path $AppDir 'bin'
$PyDir    = Join-Path $AppDir 'python'
$Src      = $PSScriptRoot
$Force    = $args -contains '--force'
$Tmp      = Join-Path $env:TEMP ('clipgo_' + [guid]::NewGuid().ToString('N'))
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

New-Item -ItemType Directory -Force -Path $Bin, $Tmp | Out-Null

function Fetch($Url, $Out) {
    Write-Host "  下载 $Url"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -fL --retry 3 --progress-bar -o $Out $Url
        if ($LASTEXITCODE -ne 0) { throw "下载失败: $Url" }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing
    }
}

try {
    Write-Host "==> 安装 ClipGo 助手到: $AppDir"

    # 正在运行的 yt-dlp / ffmpeg 会锁住文件，先结束它们
    Get-Process yt-dlp, ffmpeg, deno, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    if ($Force -or -not (Test-Path (Join-Path $PyDir 'python.exe'))) {
        Write-Host "==> 嵌入式 Python $PyVer（仅供助手使用，不影响系统）"
        Fetch "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" "$Tmp\python.zip"
        if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
        Expand-Archive -Path "$Tmp\python.zip" -DestinationPath $PyDir -Force
    } else { Write-Host "==> Python 已存在，跳过" }

    Write-Host "==> yt-dlp（每次安装都会更新到最新版）"
    Fetch 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' "$Tmp\yt-dlp.exe"
    Move-Item -Force "$Tmp\yt-dlp.exe" (Join-Path $Bin 'yt-dlp.exe')

    if ($Force -or -not (Test-Path (Join-Path $Bin 'ffmpeg.exe'))) {
        Write-Host "==> ffmpeg / ffprobe（约 100MB，请稍候）"
        Fetch 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' "$Tmp\ffmpeg.zip"
        Expand-Archive -Path "$Tmp\ffmpeg.zip" -DestinationPath "$Tmp\ffmpeg" -Force
        foreach ($exe in 'ffmpeg.exe', 'ffprobe.exe') {
            $f = Get-ChildItem -Path "$Tmp\ffmpeg" -Recurse -Filter $exe | Select-Object -First 1
            if (-not $f) { throw "压缩包中没有 $exe" }
            Copy-Item -Force $f.FullName (Join-Path $Bin $exe)
        }
    } else { Write-Host "==> ffmpeg 已存在，跳过" }

    if ($Force -or -not (Test-Path (Join-Path $Bin 'deno.exe'))) {
        Write-Host "==> deno（YouTube 解密需要的 JS 运行时）"
        Fetch 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' "$Tmp\deno.zip"
        Expand-Archive -Path "$Tmp\deno.zip" -DestinationPath $Bin -Force
    } else { Write-Host "==> deno 已存在，跳过" }

    Copy-Item -Force (Join-Path $Src 'clipgo_host.py') (Join-Path $AppDir 'clipgo_host.py')
    Copy-Item -Recurse -Force (Join-Path $Src 'plugins') $AppDir
    $bat = "@echo off`r`n`"%~dp0python\python.exe`" `"%~dp0clipgo_host.py`" %*`r`n"
    [IO.File]::WriteAllText((Join-Path $AppDir 'clipgo_host.bat'), $bat, [Text.Encoding]::ASCII)

    Write-Host "==> 注册 Native Messaging（当前用户，无需管理员权限）"
    $manifestPath = Join-Path $AppDir "$HostName.json"
    $allowedIds = @($ExtId)
    $storeIdsPath = Join-Path $Src 'browser-stores.json'
    if (Test-Path -LiteralPath $storeIdsPath) {
        $storeIds = Get-Content -LiteralPath $storeIdsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($storeName in @('chrome', 'edge')) {
            $storeId = [string]$storeIds.$storeName
            if ($storeId) {
                if ($storeId -cnotmatch '^[a-p]{32}$') { throw "Invalid $storeName extension ID" }
                $allowedIds += $storeId
            }
        }
    }
    $manifest = [ordered]@{
        name            = $HostName
        description     = 'ClipGo download helper'
        path            = (Join-Path $AppDir 'clipgo_host.bat')
        type            = 'stdio'
        allowed_origins = @($allowedIds | Select-Object -Unique | ForEach-Object { "chrome-extension://$_/" })
    }
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json), $Utf8NoBom)

    $keys = @(
        'HKCU:\Software\Google\Chrome\NativeMessagingHosts',
        'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts',
        'HKCU:\Software\Chromium\NativeMessagingHosts',
        'HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts'
    )
    foreach ($k in $keys) {
        New-Item -Path "$k\$HostName" -Value $manifestPath -Force | Out-Null
        Write-Host "   OK $k"
    }

    Write-Host "==> 自检"
    $ping = & (Join-Path $PyDir 'python.exe') -c "import json,subprocess,os;print('python ok')"
    Write-Host "   $ping"
    Write-Host "   yt-dlp $(& (Join-Path $Bin 'yt-dlp.exe') --version)"
    Write-Host "   $((& (Join-Path $Bin 'ffmpeg.exe') -version | Select-Object -First 1))"
    Write-Host "   $((& (Join-Path $Bin 'deno.exe') --version | Select-Object -First 1))"
    Write-Host ""
    Write-Host "安装完成。在浏览器扩展页面重新加载 ClipGo 后即可使用。" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "安装失败: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
