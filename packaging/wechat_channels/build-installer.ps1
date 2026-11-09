# Build a per-user NSIS installer from the already-built standalone application.
[CmdletBinding()]
param(
    [string]$Payload = '',
    [string]$WebView2Bootstrapper = '',
    [string]$Compiler = '',
    [string]$Icon = '',
    [string]$Version = '1.0.0',
    [string]$Output = '',
    [switch]$TestMode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$buildRoot = Join-Path $repoRoot 'build\wechat-installer'
$stage = Join-Path $buildRoot $(if ($TestMode) { 'test' } else { 'release' })
$utf8 = New-Object System.Text.UTF8Encoding $false
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Version must contain three numeric components.' }
if (!$Payload) { $Payload = Join-Path $repoRoot 'dist\ClipGoChannels-Release' }
$Payload = (Resolve-Path -LiteralPath $Payload).Path.TrimEnd('\')
if (!$WebView2Bootstrapper) {
    $WebView2Bootstrapper = Join-Path $repoRoot 'build\wechat-release\cache\MicrosoftEdgeWebview2Setup.exe'
}
$WebView2Bootstrapper = (Resolve-Path -LiteralPath $WebView2Bootstrapper).Path
if (!$Output) {
    $Output = if ($TestMode) { Join-Path $buildRoot "ClipGoChannels-$Version-Windows-x64-TestSetup.exe" }
              else { Join-Path $repoRoot "dist\ClipGoChannels-$Version-Windows-x64-Setup.exe" }
}
if (!$Icon) { $Icon = Join-Path $repoRoot 'build\wechat-release\ClipGoChannels.ico' }
$Output = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path $stage, ([IO.Path]::GetDirectoryName($Output)) | Out-Null

if (!$Compiler) {
    $choices = @((Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe'),
                 (Join-Path ${env:ProgramFiles(x86)} 'NSIS\Unicode\makensis.exe'))
    $Compiler = $choices | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (!$Compiler -or !(Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw 'NSIS Unicode is required; provide -Compiler with makensis.exe.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $WebView2Bootstrapper
if ($signature.Status -ne 'Valid' -or !$signature.SignerCertificate -or
    $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O=Microsoft Corporation(?:,|$)') {
    throw 'The WebView2 bootstrapper must have a valid Microsoft Corporation signature.'
}
$bootstrapHash = (Get-FileHash -LiteralPath $WebView2Bootstrapper -Algorithm SHA256).Hash.ToLowerInvariant()
$runtimeLock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($bootstrapHash -ne $runtimeLock.webview2.sha256.ToLowerInvariant()) {
    throw 'The WebView2 bootstrapper hash does not match runtime-lock.json.'
}
$required = @('ClipGoChannels.exe', '_internal\web_assets\index.html',
              '_internal\web_assets\app.js', '_internal\web_assets\app.css')
foreach ($relative in $required) {
    if (!(Test-Path -LiteralPath (Join-Path $Payload $relative) -PathType Leaf)) {
        throw "Application payload is incomplete: $relative"
    }
}
if ((Get-Item -LiteralPath $Payload -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The payload root must not be a linked directory.'
}
$entries = @(Get-ChildItem -LiteralPath $Payload -Recurse -Force)
foreach ($entry in $entries) {
    if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Payload links are not supported.' }
}
$files = @($entries | Where-Object { !$_.PSIsContainer } | Sort-Object FullName)
if (!$files.Count) { throw 'The payload is empty.' }
$reserved = @('Uninstall.exe', 'package-manifest.json', 'installer-info.ini')
$manifestFiles = New-Object 'System.Collections.Generic.List[object]'
foreach ($file in $files) {
    $relative = $file.FullName.Substring($Payload.Length + 1)
    if ($reserved -contains $relative) { throw "Payload uses an installer-reserved filename: $relative" }
    if ($relative -match '(^|[\\/])(__pycache__|\.git)([\\/]|$)' -or
        $relative -match '\.(mp4|mkv|mov|webm|part|pcap|pcapng)$') {
        throw "Payload contains cache or personal media: $relative"
    }
    $manifestFiles.Add([ordered]@{ path = $relative; length = $file.Length
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() })
}
$manifestPath = Join-Path $stage 'package-manifest.json'
$manifest = [ordered]@{ product = 'ClipGoChannels'; version = $Version; files = @($manifestFiles.ToArray()) }
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), $utf8)

function Escape-Nsis([string]$Value) {
    if ($Value -match '[\r\n\x00]') { throw 'An NSIS path contains an invalid control character.' }
    return $Value.Replace('$', '$$').Replace('"', '$\"')
}

# Generate the exact package-file removal list at build time. No recursive
# uninstall or untrusted on-disk manifest is used to decide what to delete.
$include = New-Object 'System.Collections.Generic.List[string]'
$include.Add('!macro InstallPayload')
foreach ($directory in @($entries | Where-Object { $_.PSIsContainer } | Sort-Object FullName)) {
    $relative = $directory.FullName.Substring($Payload.Length + 1)
    $include.Add('  CreateDirectory "$INSTDIR\' + (Escape-Nsis $relative) + '"')
}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($Payload.Length + 1)
    $directory = [IO.Path]::GetDirectoryName($relative)
    $destination = if ($directory) { '$INSTDIR\' + (Escape-Nsis $directory) } else { '$INSTDIR' }
    $include.Add('  SetOutPath "' + $destination + '"')
    $include.Add('  File "/oname=' + (Escape-Nsis $file.Name) + '" "' + (Escape-Nsis $file.FullName) + '"')
}
$include.Add('!macroend')
$include.Add('!macro UninstallPayload')
foreach ($entry in $manifestFiles) {
    if ($entry.path -ne 'ClipGoChannels.exe') {
        $include.Add('  Delete "$INSTDIR\' + (Escape-Nsis $entry.path) + '"')
    }
}
$directories = @($entries | Where-Object { $_.PSIsContainer } |
    Sort-Object @{ Expression = { $_.FullName.Length }; Descending = $true })
foreach ($directory in $directories) {
    $relative = $directory.FullName.Substring($Payload.Length + 1)
    $include.Add('  RMDir "$INSTDIR\' + (Escape-Nsis $relative) + '"')
}
$include.Add('!macroend')
$includePath = Join-Path $stage 'payload-files.nsh'
[IO.File]::WriteAllLines($includePath, $include, $utf8)
$sizeKB = [int][Math]::Ceiling(($files | Measure-Object -Property Length -Sum).Sum / 1024.0)
$compilerOptions = @('/NOCONFIG', '/INPUTCHARSET', 'UTF8', '/V2',
    "/DVERSION=$Version", "/DOUTPUT=$Output", "/DPAYLOAD_INCLUDE=$includePath",
    "/DPACKAGE_MANIFEST=$manifestPath", "/DPAYLOAD_SIZE_KB=$sizeKB",
    "/DWEBVIEW_BOOTSTRAPPER=$WebView2Bootstrapper")
$modeHelp = & $Compiler /CMDHELP OutFileMode 2>&1 | Out-String
if ($modeHelp -match 'OutFileMode\s+.*aio') { $compilerOptions = @('/XOutFileMode aio') + $compilerOptions }
if ($TestMode) { $compilerOptions += '/DTEST_MODE' }
if (Test-Path -LiteralPath $Icon -PathType Leaf) { $compilerOptions += '/DICON=' + [IO.Path]::GetFullPath($Icon) }
& $Compiler @compilerOptions (Join-Path $PSScriptRoot 'installer.nsi')
if ($LASTEXITCODE -ne 0) { throw "NSIS compilation failed with exit code $LASTEXITCODE." }
$installerHash = (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($Output + '.sha256', "$installerHash  $([IO.Path]::GetFileName($Output))`r`n", $utf8)
$buildInfo = [ordered]@{ product = 'ClipGoChannels'; version = $Version; test_mode = [bool]$TestMode
    installer_sha256 = $installerHash; payload = $Payload; file_count = $files.Count
    payload_manifest = $manifestPath; webview2_sha256 = $bootstrapHash }
[IO.File]::WriteAllText($Output + '.build.json', ($buildInfo | ConvertTo-Json -Depth 4), $utf8)
Write-Output "Built: $Output"
Write-Output "SHA256: $installerHash"
