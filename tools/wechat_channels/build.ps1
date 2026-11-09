$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envRoot = Join-Path $repoRoot 'build\wechat-venv'
$pythonExe = Join-Path $envRoot 'Scripts\python.exe'
$webAssets = Join-Path $PSScriptRoot 'web_assets'
$pageAssets = Join-Path $PSScriptRoot 'page_assets'
if (-not (Test-Path -LiteralPath (Join-Path $webAssets 'index.html') -PathType Leaf)) {
    throw 'HTML interface assets are missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $pageAssets 'embed.js') -PathType Leaf)) {
    throw 'WeChat page button assets are missing.'
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & py -3.12 -m venv $envRoot
    if ($LASTEXITCODE) { throw 'Python 3.12 is required.' }
}
& $pythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt') pyinstaller==6.16.0
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
& $pythonExe -m PyInstaller --noconfirm --windowed --onedir --name ClipGoChannels `
    --distpath (Join-Path $repoRoot 'dist') --workpath (Join-Path $repoRoot 'build\wechat-package') `
    --specpath (Join-Path $repoRoot 'build') --paths (Join-Path $repoRoot 'tools') `
    --collect-all mitmproxy --collect-all mitmproxy_rs --collect-all mitmproxy_windows --collect-all pydivert `
    --collect-data webview --collect-binaries webview --collect-all pythonnet --collect-binaries clr_loader `
    --hidden-import webview.platforms.winforms --hidden-import webview.platforms.edgechromium --hidden-import clr `
    --copy-metadata mitmproxy --copy-metadata pywebview `
    --add-data "$PSScriptRoot\capture.py;." --add-data "$webAssets;web_assets" `
    --add-data "$pageAssets;page_assets" (Join-Path $PSScriptRoot 'run.py')
if ($LASTEXITCODE) { throw 'Packaging failed.' }
$dest = Join-Path $repoRoot 'dist\ClipGoChannels'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $dest '使用说明.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD-PARTY-NOTICES.txt') -Destination $dest
# Preserve the bundled packages' license texts for this local development build.
$licenses = Join-Path $dest 'licenses'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null
Get-ChildItem (Join-Path $envRoot 'Lib\site-packages') -Directory -Filter '*.dist-info' | ForEach-Object {
    $package = $_
    $found = Get-ChildItem -LiteralPath $package.FullName -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)' }
    foreach ($file in $found) {
        $target = Join-Path $licenses $package.Name
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}
Write-Host "Built: $dest\ClipGoChannels.exe"
