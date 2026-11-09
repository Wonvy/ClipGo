$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dist = Join-Path $repo 'dist\stores'
$stage = Join-Path $repo 'build\stores'
$utf8 = New-Object Text.UTF8Encoding $false
New-Item -ItemType Directory -Path $dist, $stage -Force | Out-Null
& node (Join-Path $repo 'i18n\build.mjs')
if ($LASTEXITCODE -ne 0) { throw 'Translation generation failed' }
$manifest = Get-Content -LiteralPath "$repo\extension\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $manifest.version
$manifest.PSObject.Properties.Remove('key')
$manifest.PSObject.Properties.Remove('update_url')
$locales = @('en', 'ja', 'ko', 'zh_CN', 'zh_TW')
foreach ($locale in $locales) {
    $messages = Get-Content -LiteralPath "$repo\extension\_locales\$locale\messages.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($messages.appName.message.Length -gt 75 -or $messages.appDescription.message.Length -gt 132) { throw "Metadata too long: $locale" }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($store in @('Chrome', 'Edge')) {
    $directory = Join-Path $stage $store
    $resolved = [IO.Path]::GetFullPath($directory)
    if (!$resolved.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe staging path' }
    if (Test-Path -LiteralPath $directory) {
        if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked staging path' }
        Remove-Item -LiteralPath $directory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath "$repo\extension" -Recurse -File) {
        if ($file.Extension -notin @('.js', '.json', '.html', '.css', '.png')) { continue }
        $relative = $file.FullName.Substring((Join-Path $repo 'extension').Length + 1)
        $target = Join-Path $directory $relative
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
    [IO.File]::WriteAllText("$directory\manifest.json", ($manifest | ConvertTo-Json -Depth 20), $utf8)
    $zip = Join-Path $dist "ClipGo-$version-$store.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    [IO.Compression.ZipFile]::CreateFromDirectory($directory, $zip)
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($zip + '.sha256', $hash + '  ' + [IO.Path]::GetFileName($zip) + "`r`n", $utf8)
    Write-Output $zip
}
Copy-Item -LiteralPath "$repo\extension\icons\icon128.png" -Destination "$dist\store-icon-128.png" -Force
foreach ($file in @('listings.json', 'publisher.json', 'privacy-policy.html', 'privacy-declarations.json', 'SUBMISSION.txt')) {
    $source = Join-Path $PSScriptRoot $file
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $dist $file) -Force }
}
$publisher = Get-Content -LiteralPath "$PSScriptRoot\publisher.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$storeIds = Get-Content -LiteralPath "$repo\host\browser-stores.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$missing = @('publisherName', 'privacyPolicyUrl', 'helperDownloadUrl') | Where-Object { !$publisher.$_ }
$report = [ordered]@{
    version = $version; uploaded = $false; submitted = $false
    missingPublisherFields = @($missing)
    chromeNativeOriginConfigured = [bool]$storeIds.chrome
    edgeNativeOriginConfigured = [bool]$storeIds.edge
    nextStep = 'Upload each ZIP as a draft to obtain its store ID. Add the IDs to host/browser-stores.json and rebuild the helper before submitting for review. Public privacy-policy and helper-download URLs are required.'
}
[IO.File]::WriteAllText("$dist\submission-status.json", ($report | ConvertTo-Json -Depth 5), $utf8)
