# Actual silent install / locked-upgrade / HTML startup / uninstall test.
# Only a separately compiled TEST_MODE installer is accepted.
[CmdletBinding()]
param(
    [string]$Installer = '',
    [string]$Payload = '',
    [int]$WaitSeconds = 150
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$testParent = Join-Path $repoRoot 'build\wechat-release-tests'
if (!$Installer) { $Installer = Join-Path $repoRoot 'build\wechat-installer\ClipGoChannels-1.0.0-Windows-x64-TestSetup.exe' }
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$buildInfo = Get-Content -LiteralPath ($Installer + '.build.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($buildInfo.test_mode -ne $true -or $buildInfo.product -ne 'ClipGoChannels') {
    throw 'Only an installer compiled with -TestMode may be tested automatically.'
}
if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $buildInfo.installer_sha256) {
    throw 'Installer hash does not match the test-build metadata.'
}
if (!$Payload) { $Payload = $buildInfo.payload }
$Payload = (Resolve-Path -LiteralPath $Payload).Path.TrimEnd('\')
$testRoot = Join-Path $testParent ([guid]::NewGuid().ToString('N'))
$app = Join-Path $testRoot '中文 空格 & ClipGo'
$checks = New-Object 'System.Collections.Generic.List[string]'
$utf8 = New-Object System.Text.UTF8Encoding $false
$report = [ordered]@{ status = 'running'; version = $buildInfo.version; checks = @(); error = ''; diagnostic = $null }
$reportPath = Join-Path $testRoot 'report.json'

function Assert-Check([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
    $checks.Add($Message)
}

function Assert-IsolatedPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($testParent + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Test path escaped build/wechat-release-tests.'
    }
    $node = $full
    while ($node) {
        if ((Test-Path -LiteralPath $node) -and
            ((Get-Item -LiteralPath $node -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Test path contains a linked file or directory.'
        }
        $parent = [IO.Directory]::GetParent($node)
        if (!$parent) { break }
        $node = $parent.FullName
    }
    return $full
}

function Get-ExternalState {
    $values = [ordered]@{}
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $view)
        try {
            foreach ($path in @('Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGoChannels',
                    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')) {
                $key = $base.OpenSubKey($path, $false)
                try {
                    $entry = if ($key) { [ordered]@{} } else { $null }
                    if ($key) {
                        foreach ($name in @($key.GetValueNames() | Sort-Object)) {
                            $entry[$name] = $key.GetValue($name, $null,
                                [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                        }
                    }
                    $values[([string]$view + ':' + $path)] = $entry
                } finally { if ($key) { $key.Dispose() } }
            }
        } finally { $base.Dispose() }
    }
    $knownFiles = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ClipGo 视频号.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'ClipGo 视频号\ClipGo 视频号.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'ClipGo 视频号\卸载.lnk'),
        (Join-Path $env:LOCALAPPDATA 'ClipGoChannels\last-web-state.json')
    )
    foreach ($path in $knownFiles) {
        $values[$path] = if (Test-Path -LiteralPath $path -PathType Leaf) {
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        } else { $null }
    }
    return ($values | ConvertTo-Json -Depth 8 -Compress)
}

function Run-OwnedProcess([string]$Executable, [string]$Arguments, [int]$Seconds = 150) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $testRoot -WindowStyle Hidden -PassThru
    try {
        if (!$process.WaitForExit($Seconds * 1000)) {
            $process.Kill()
            throw 'The isolated test process exceeded its timeout.'
        }
        $process.Refresh()
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Assert-PayloadHashes {
    $manifest = Get-Content -LiteralPath (Join-Path $app 'package-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Check ($manifest.product -eq 'ClipGoChannels' -and $manifest.version -eq $buildInfo.version) 'Installed package identity and version match'
    Assert-Check ($manifest.files.Count -eq $buildInfo.file_count) 'Installed manifest has every payload file'
    foreach ($directory in @(Get-ChildItem -LiteralPath $Payload -Directory -Recurse -Force)) {
        $relative = $directory.FullName.Substring($Payload.Length + 1)
        if (!(Test-Path -LiteralPath (Join-Path $app $relative) -PathType Container)) {
            throw "Installed payload directory is missing: $relative"
        }
    }
    $checks.Add('Installed payload directory structure includes empty SDK resource directories')
    foreach ($entry in $manifest.files) {
        if ([IO.Path]::IsPathRooted($entry.path)) { throw 'Manifest entry is not relative.' }
        $destination = [IO.Path]::GetFullPath((Join-Path $app $entry.path))
        if (!$destination.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Manifest entry escaped the isolated application directory.'
        }
        Assert-IsolatedPath $destination | Out-Null
        $source = [IO.Path]::GetFullPath((Join-Path $Payload $entry.path))
        if (!$source.StartsWith($Payload + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest source path escaped payload.' }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $installedHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($sourceHash -ne $entry.sha256 -or $installedHash -ne $entry.sha256) {
            throw "Installed payload hash differs: $($entry.path)"
        }
    }
    $checks.Add("All $($manifest.files.Count) installed payload SHA256 hashes match the release")
    return $manifest
}

Assert-IsolatedPath $testRoot | Out-Null
Assert-IsolatedPath $app | Out-Null
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$before = Get-ExternalState
try {
    # /D= is the final NSIS argument; its whole remainder is a literal path.
    $code = Run-OwnedProcess $Installer ("/S /D=" + $app) $WaitSeconds
    Assert-Check ($code -eq 0) "Silent isolated install succeeds (exit $code)"
    $manifest = Assert-PayloadHashes
    Assert-Check ((Get-ExternalState) -ceq $before) 'Install leaves real registration, shortcuts and application state unchanged'

    $personal = Join-Path $app '_internal\my-notes.txt'
    $video = Join-Path $testRoot 'Downloaded-video.mp4'
    [IO.File]::WriteAllText($personal, 'User-created file: must survive reinstall and uninstall.', $utf8)
    [IO.File]::WriteAllBytes($video, [byte[]](1, 2, 3, 4, 5))
    $personalHash = (Get-FileHash -LiteralPath $personal).Hash
    $videoHash = (Get-FileHash -LiteralPath $video).Hash

    # Simulate a file lock before reinstall. A changed package file must remain
    # unchanged if the EXE cannot be opened exclusively; no partial upgrade.
    $asset = Join-Path $app '_internal\web_assets\app.js'
    [IO.File]::AppendAllText($asset, "`r`n/* locked upgrade sentinel */`r`n", $utf8)
    $lockedAssetHash = (Get-FileHash -LiteralPath $asset).Hash
    $held = [IO.File]::Open((Join-Path $app 'ClipGoChannels.exe'), [IO.FileMode]::Open,
                          [IO.FileAccess]::Read, [IO.FileShare]::None)
    try {
        $code = Run-OwnedProcess $Installer ("/S /D=" + $app) $WaitSeconds
        Assert-Check ($code -eq 1) 'Locked application is rejected before upgrading any payload file'
        Assert-Check ((Get-FileHash -LiteralPath $asset).Hash -eq $lockedAssetHash) 'Rejected upgrade preserves the previous application bytes'
    } finally { $held.Dispose() }
    $code = Run-OwnedProcess $Installer ("/S /D=" + $app) $WaitSeconds
    Assert-Check ($code -eq 0) 'Reinstall succeeds after the application lock is released'
    $manifest = Assert-PayloadHashes
    Assert-Check ((Get-FileHash -LiteralPath $personal).Hash -eq $personalHash) 'Reinstall preserves user-created files'

    $diagnosticPath = Join-Path $testRoot 'web-diagnostic.json'
    $code = Run-OwnedProcess (Join-Path $app 'ClipGoChannels.exe') ('--diagnose-web "' + $diagnosticPath + '"') $WaitSeconds
    Assert-Check ($code -eq 0) 'Installed application finishes its hidden HTML diagnostic'
    $diagnostic = Get-Content -LiteralPath $diagnosticPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Check ($diagnostic.page_loaded -eq $true -and $diagnostic.frontend_errors -eq 0) 'Installed WebView2 page and JavaScript load successfully'
    Assert-Check (@('done', 'error') -contains $diagnostic.result) 'Installed application completes reading without a diagnostic timeout'
    Assert-Check (!$diagnostic.PSObject.Properties['reason']) 'Installed page has no DOM data-flow mismatch'
    Assert-Check ($diagnostic.engagement_rows -eq $diagnostic.dom_engagement_rows -and
                  $diagnostic.complete_engagement_rows -eq $diagnostic.dom_complete_engagement_rows) 'Real engagement counts reach the installed HTML interface'
    $report.diagnostic = $diagnostic

    # Run a copy outside the installation so Windows can delete the installed
    # uninstaller too, while _?= lets us wait for the actual uninstall process.
    $runner = Join-Path $testRoot 'uninstall-runner.exe'
    Copy-Item -LiteralPath (Join-Path $app 'Uninstall.exe') -Destination $runner
    $code = Run-OwnedProcess $runner ("/S _?=" + $app) $WaitSeconds
    Assert-Check ($code -eq 0) 'Silent isolated uninstall succeeds'
    foreach ($entry in $manifest.files) {
        if (Test-Path -LiteralPath (Join-Path $app $entry.path)) { throw "Uninstall left a package file: $($entry.path)" }
    }
    foreach ($name in @('Uninstall.exe', 'package-manifest.json', 'installer-info.ini')) {
        Assert-Check (!(Test-Path -LiteralPath (Join-Path $app $name))) ("Uninstall removes " + $name)
    }
    Assert-Check ((Get-FileHash -LiteralPath $personal).Hash -eq $personalHash) 'Uninstall preserves unrelated files inside the application directory'
    Assert-Check ((Get-FileHash -LiteralPath $video).Hash -eq $videoHash) 'Uninstall preserves downloaded video bytes outside the application directory'
    Assert-Check ((Get-ExternalState) -ceq $before) 'Test leaves real registration, shortcuts, WebView2 registration and application state unchanged'
    $report.status = 'passed'
} catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
    throw
} finally {
    $report.checks = @($checks.ToArray())
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 8), $utf8)
    Write-Output "Installer test report: $reportPath"
}
