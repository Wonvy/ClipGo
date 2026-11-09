param([string]$Version = '1.1.0')
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$releaseBuild = Join-Path $repoRoot 'build\wechat-release'
$envRoot = Join-Path $repoRoot 'build\wechat-release-venv'
$pythonExe = Join-Path $envRoot 'Scripts\python.exe'
$packageRoot = Join-Path $repoRoot 'dist\ClipGoChannels-Release'
New-Item -ItemType Directory -Force -Path $releaseBuild | Out-Null
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & py -3.12 -m venv $envRoot
    if ($LASTEXITCODE) { throw 'Python 3.12 x64 is required.' }
}
& $pythonExe -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE) { throw 'The release requires Python 3.12 x64.' }
& $pythonExe -m pip install --disable-pip-version-check -q -r (Join-Path $PSScriptRoot 'requirements-release.txt') pyinstaller==6.16.0
if ($LASTEXITCODE) { throw 'Release dependency installation failed.' }
& $pythonExe (Join-Path $PSScriptRoot 'release_assets.py') prepare --version $Version --target $releaseBuild
if ($LASTEXITCODE) { throw 'Release metadata generation failed.' }
$exclude = @('mitmproxy_windows','mitmproxy_linux','mitmproxy_macos','tkinter',
    'mitmproxy.tools.console','mitmproxy.tools.web','mitmproxy.tools.main','urwid','wcwidth',
    'wechat_channels.app','wechat_channels.view','wechat_channels.cards',
    'playwright','pytest','IPython','numpy','pandas','matplotlib','PyQt5','PyQt6','PySide2','PySide6',
    'setuptools','packaging','_distutils_hack')
$options = @('--noconfirm','--windowed','--onedir','--name','ClipGoChannels',
    '--distpath',(Join-Path $releaseBuild 'dist'),'--workpath',(Join-Path $releaseBuild 'pyinstaller'),
    '--specpath',$releaseBuild,'--paths',(Join-Path $repoRoot 'tools'),
    '--icon',(Join-Path $releaseBuild 'ClipGoChannels.ico'),
    '--version-file',(Join-Path $releaseBuild 'version.txt'),
    '--additional-hooks-dir',(Join-Path $releaseBuild 'hooks'),
    '--collect-data','webview','--collect-binaries','webview',
    '--collect-data','mitmproxy','--collect-data','mitmproxy_rs','--collect-binaries','mitmproxy_rs',
    '--collect-all','pythonnet','--collect-binaries','clr_loader',
    '--hidden-import','webview.platforms.winforms','--hidden-import','webview.platforms.edgechromium',
    '--hidden-import','clr','--copy-metadata','pywebview','--copy-metadata','mitmproxy',
    '--copy-metadata','mitmproxy_rs',
    '--add-data',((Join-Path $PSScriptRoot 'web_assets')+';web_assets'),
    '--add-data',((Join-Path $PSScriptRoot 'page_assets')+';page_assets'))
foreach ($name in $exclude) { $options += @('--exclude-module',$name) }
$options += (Join-Path $PSScriptRoot 'release.py')
& $pythonExe -m PyInstaller @options
if ($LASTEXITCODE) { throw 'Release application packaging failed.' }
# Only replace this generated payload, never the developer's running build.
$resolvedPayload = [IO.Path]::GetFullPath($packageRoot)
$allowedPayload = [IO.Path]::GetFullPath((Join-Path $repoRoot 'dist\ClipGoChannels-Release'))
if ($resolvedPayload -ne $allowedPayload -or -not $resolvedPayload.StartsWith($repoRoot+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unexpected release payload path'
}
if (Test-Path -LiteralPath $resolvedPayload) {
    if ((Get-Item -LiteralPath $resolvedPayload).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked release payload' }
    Remove-Item -LiteralPath $resolvedPayload -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $releaseBuild 'dist\ClipGoChannels') -Destination $resolvedPayload -Recurse
# Keep pywebview's SDK directories intact: its Windows renderer resolves all
# loader directories during startup, even when only the x64 loader is used.
& $pythonExe (Join-Path $PSScriptRoot 'release_assets.py') notices --version $Version --target $resolvedPayload
if ($LASTEXITCODE) { throw 'Release notices generation failed.' }
$forbidden = @(Get-ChildItem -LiteralPath $resolvedPayload -Recurse -File | Where-Object {
    $relative = $_.FullName.Substring($resolvedPayload.Length + 1).Replace('\','/')
    $publicCA = $relative -eq '_internal/certifi/cacert.pem'
    $_.FullName -match '(?i)[\\/](mitmproxy_windows|mitmproxy_linux|mitmproxy_macos|playwright|_tcl_data|_tk_data)[\\/]' -or
    $_.Name -match '(?i)^windivert.*\.(dll|sys|exe|lib)$|^windows-redirector\.exe$' -or
    $_.Extension -in @('.key','.mp4','.part','.sys') -or
    ($_.Extension -eq '.pem' -and -not $publicCA)
})
if ($forbidden.Count) { throw ('Unexpected release file: '+$forbidden[0].Name) }
Write-Output ('Release payload: '+$resolvedPayload)
