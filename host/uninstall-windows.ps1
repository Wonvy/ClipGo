# 卸载 ClipGo 本地助手 (Windows)。不会删除已下载的视频。
$HostName = 'com.clipgo.host'
$AppDir = Join-Path $env:LOCALAPPDATA 'ClipGo'
foreach ($k in 'HKCU:\Software\Google\Chrome\NativeMessagingHosts',
               'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts',
               'HKCU:\Software\Chromium\NativeMessagingHosts',
               'HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts') {
    Remove-Item -Path "$k\$HostName" -Recurse -Force -ErrorAction SilentlyContinue
}
Get-Process yt-dlp, ffmpeg, deno, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
Write-Host "已卸载 ClipGo 助手" -ForegroundColor Green
