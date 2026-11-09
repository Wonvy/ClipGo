$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$testDir = Join-Path $repo ('build\windows\guide-test-' + [guid]::NewGuid().ToString('N'))
$appDir = Join-Path $testDir '中文 空格 & , 测试'
New-Item -ItemType Directory -Force -Path (Join-Path $appDir 'extension') | Out-Null
Set-Content -LiteralPath (Join-Path $appDir 'extension\manifest.json') -Value '{}' -Encoding ASCII
. "$PSScriptRoot\open-guide.ps1" -AppDir $appDir -NoLaunch
$actualBrowsers = @(Get-SetupBrowsers)
if (!$actualBrowsers.Count) { throw 'No installed browser available for integration checks' }
# Capture launch requests, without opening browsers or modifying their profiles.
$script:calls = @()
function Start-Process { param($FilePath, $ArgumentList) $script:calls += @{ FilePath=$FilePath; Arguments=@($ArgumentList) } }
foreach ($browser in $actualBrowsers) {
    Open-SetupBrowser $browser
    $call = $calls[-1]
    if ($call.FilePath -ne $browser.Path -or $call.Arguments.Count -ne 2 -or $call.Arguments[0] -ne '--new-window' -or $call.Arguments[1] -ne 'about:blank') { throw 'Incorrect browser launch' }
    if ($browser.Url -ne ($browser.Kind + '://extensions/')) { throw 'Incorrect management URL for clipboard' }
}
Open-SetupFolder
if ($calls[-1].Arguments[0] -cne ('"' + (Join-Path $appDir 'extension') + '"')) { throw 'Folder path was not preserved/quoted' }
$callsBefore = $calls.Count
$extensionPath = Join-Path $testDir 'missing'
try { Open-SetupBrowser $actualBrowsers[0]; throw 'Missing files were accepted' } catch { if ($_.Exception.Message -ne 'missingExtension') { throw } }
if ($calls.Count -ne $callsBefore) { throw 'Browser was opened for a missing extension' }
$translations = Get-Content "$PSScriptRoot\setup-locales.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$keys = @($translations.en.PSObject.Properties.Name | Sort-Object)
foreach ($locale in @('en', 'zh_CN', 'zh_TW', 'ja', 'ko')) {
    if ((@($translations.$locale.PSObject.Properties.Name | Sort-Object) -join ',') -ne ($keys -join ',')) { throw "Missing translation: $locale" }
    foreach ($key in $keys) { if ([string]::IsNullOrWhiteSpace($translations.$locale.$key)) { throw "Empty translation: $locale/$key" } }
}
Write-Output ('Setup checks passed: installed browsers ' + ($actualBrowsers.Name -join ', ') + '; exact URLs, quoted Unicode folder, missing-file handling, five languages.')
