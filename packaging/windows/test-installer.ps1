# Isolated installer/reinstall/uninstall smoke test. Windows PowerShell 5.1+.
[CmdletBinding()]
param(
    [string]$Installer = '',
    [string]$ExpectedVersion = '',
    [int]$WaitSeconds = 600
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$build = Join-Path $repo 'build\windows'
$payload = Join-Path $build 'payload'
$version = if ($ExpectedVersion) { $ExpectedVersion } else { (Get-Content -LiteralPath (Join-Path $repo 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version }
if (!$Installer) { $Installer = Join-Path $repo "dist\ClipGo-$version-Windows-x64-Setup.exe" }
$Installer = [IO.Path]::GetFullPath($Installer)
$testRoot = Join-Path $build ('installer-test-' + [guid]::NewGuid().ToString('N'))
$app = Join-Path $testRoot '中文 空格 & 安装'
$utf8 = New-Object System.Text.UTF8Encoding $false
$checks = New-Object 'System.Collections.Generic.List[string]'
$report = [ordered]@{
    status = 'running'; started = (Get-Date).ToString('o'); installer = $Installer
    installDirectory = $app; version = $version; checks = @(); notes = @(); error = ''
}
$reportPath = Join-Path $testRoot 'report.json'
$before = $null
$exitCode = 1

function Assert-Test([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
    $checks.Add($Message)
}

function Assert-TestPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($build + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Test path escaped build/windows' }
    $node = $full
    while ($node) {
        if (Test-Path -LiteralPath $node) {
            if ((Get-Item -LiteralPath $node -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Test path contains a linked directory or file'
            }
        }
        $parent = [IO.Directory]::GetParent($node)
        if (!$parent) { break }
        $node = $parent.FullName
    }
    return $full
}

function Read-RegistryTree([Microsoft.Win32.RegistryKey]$Key) {
    if (!$Key) { return $null }
    $values = [ordered]@{}
    foreach ($name in @($Key.GetValueNames() | Sort-Object)) {
        $values[$name] = [ordered]@{
            kind = [string]$Key.GetValueKind($name)
            value = $Key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        }
    }
    $children = [ordered]@{}
    foreach ($name in @($Key.GetSubKeyNames() | Sort-Object)) {
        $child = $Key.OpenSubKey($name, $false)
        try { $children[$name] = Read-RegistryTree $child } finally { if ($child) { $child.Dispose() } }
    }
    return [ordered]@{ values = $values; children = $children }
}

function Get-ExternalState {
    $paths = @(
        'Software\Google\Chrome\NativeMessagingHosts\com.clipgo.host',
        'Software\Microsoft\Edge\NativeMessagingHosts\com.clipgo.host',
        'Software\Chromium\NativeMessagingHosts\com.clipgo.host',
        'Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.clipgo.host',
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipGo'
    )
    $registry = [ordered]@{}
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $view)
        try {
            foreach ($path in $paths) {
                $key = $base.OpenSubKey($path, $false)
                try { $registry[([string]$view + ':' + $path)] = Read-RegistryTree $key }
                finally { if ($key) { $key.Dispose() } }
            }
        } finally { $base.Dispose() }
    }
    $programs = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    $menu = Join-Path $programs 'ClipGo'
    $exists = Test-Path -LiteralPath $menu -PathType Container
    $menuItems = @()
    if ($exists) {
        $menuItems = @(Get-ChildItem -LiteralPath $menu -Force -Recurse | Sort-Object FullName | ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($menu.Length + 1)
                directory = $_.PSIsContainer
                hash = if ($_.PSIsContainer) { '' } else { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
            }
        })
    }
    return ([ordered]@{ registry = $registry; startMenu = [ordered]@{ exists = $exists; items = $menuItems } } | ConvertTo-Json -Depth 30 -Compress)
}

function Assert-ExternalState([string]$Stage) {
    Assert-Test ((Get-ExternalState) -ceq $before) ("External registration and Start menu unchanged after " + $Stage)
}

function Run-Installer {
    # NSIS consumes the whole remainder after /D=. Do not quote it or add switches
    # afterwards. This is an EXE invocation, not a shell command; '&' stays literal.
    Write-Host ('Installing isolated copy: ' + $app)
    $process = Start-Process -FilePath $Installer -ArgumentList ("/S /TEST /D=" + $app) -WindowStyle Hidden -Wait -PassThru
    try {
        $process.Refresh()
        Assert-Test ($process.ExitCode -eq 0) ('Silent installer exit code is zero (actual ' + $process.ExitCode + ')')
    } finally { $process.Dispose() }
}

function Read-Inventory {
    $inventoryPath = Join-Path $app 'installed-files.json'
    # PowerShell 5.1 emits a JSON root array as one pipeline object. Assign it
    # directly; @(... | ConvertFrom-Json) would introduce a nested array there.
    $items = Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Test ($items.Count -gt 10) 'Installed file inventory is populated'
    foreach ($relative in $items) {
        Assert-Test (([string]$relative) -and ![IO.Path]::IsPathRooted($relative)) ('Inventory path is relative: ' + $relative)
        $full = [IO.Path]::GetFullPath((Join-Path $app $relative))
        Assert-Test ($full.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase)) ('Inventory path stays within the test installation: ' + $relative)
        Assert-Test ($relative -notmatch '(^|[\\/])(config\.json|host\.log|personal-note\.txt)$' -and $relative -notlike '*.mp4') ('Inventory excludes user data: ' + $relative)
    }
    return $items
}

function Assert-Layout {
    $required = @(
        'python\python.exe', 'python\python312.dll', 'python\python312.zip',
        'bin\yt-dlp.exe', 'bin\ffmpeg.exe', 'bin\ffprobe.exe', 'bin\deno.exe',
        'clipgo_host.py', 'clipgo_host.bat', 'com.clipgo.host.json', 'installed-files.json',
        'plugins\yt_dlp_plugins\extractor\clipgo_tencent.py',
        'extension\manifest.json', 'extension\background.js', 'extension\common.js', 'extension\content.js',
        'extension\douyin-media.js', 'extension\douyin-page.js', 'extension\popup.html', 'extension\popup.js', 'extension\popup.css',
        'Uninstall.exe', 'ClipGoSetup.exe', 'open-guide.ps1', 'setup-locales.json', 'uninstall-runtime.ps1', 'ClipGo.ico', 'welcome.html'
    )
    foreach ($relative in $required) {
        Assert-Test (Test-Path -LiteralPath (Join-Path $app $relative) -PathType Leaf) ('Installed required file: ' + $relative)
    }
    foreach ($name in @('open-guide.ps1', 'setup-locales.json')) {
        Assert-Test ((Get-FileHash -LiteralPath (Join-Path $app $name)).Hash -eq (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name)).Hash) ('Installed setup UI matches current source: ' + $name)
    }
    Assert-Test ((Get-FileHash -LiteralPath (Join-Path $app 'ClipGoSetup.exe')).Hash -eq (Get-FileHash -LiteralPath (Join-Path $build 'ClipGoSetup.exe')).Hash) 'Installed setup launcher matches this build'
    foreach ($source in @(@('runtime', ''), @('extension', 'extension'))) {
        $sourceRoot = Join-Path $payload $source[0]
        if (!(Test-Path -LiteralPath $sourceRoot -PathType Container)) { continue }
        foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -File -Recurse) {
            $relative = $file.FullName.Substring($sourceRoot.Length + 1)
            $destination = if ($source[1]) { Join-Path (Join-Path $app $source[1]) $relative } else { Join-Path $app $relative }
            Assert-Test (Test-Path -LiteralPath $destination -PathType Leaf) ('Payload file exists: ' + $source[0] + '\' + $relative)
            Assert-Test ((Get-Item -LiteralPath $destination).Length -eq $file.Length) ('Payload length matches: ' + $source[0] + '\' + $relative)
        }
    }
    $nativePath = Join-Path $app 'com.clipgo.host.json'
    $native = Get-Content -LiteralPath $nativePath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Test ($native.path -eq (Join-Path $app 'clipgo_host.bat')) 'Native manifest points only to the isolated installation'
    $expectedOrigins = @('chrome-extension://mnhnajpalaecohikjibocdeamfmnpena/')
    $storeIds = Get-Content -LiteralPath (Join-Path $repo 'host\browser-stores.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($storeName in @('chrome', 'edge')) {
        if ($storeIds.$storeName) { $expectedOrigins += 'chrome-extension://' + $storeIds.$storeName + '/' }
    }
    $expectedOrigins = @($expectedOrigins | Sort-Object -Unique)
    Assert-Test ((@($native.allowed_origins | Sort-Object -Unique) -join ',') -eq ($expectedOrigins -join ',')) 'Native manifest contains only the configured extension IDs'
    $bytes = [IO.File]::ReadAllBytes($nativePath)
    Assert-Test (!($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)) 'Native manifest has no UTF-8 BOM'
    $extension = Get-Content -LiteralPath (Join-Path $app 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Test ($extension.version -eq $version) 'Installed extension version matches the source'
    $welcome = Get-Content -LiteralPath (Join-Path $app 'welcome.html') -Raw -Encoding UTF8
    $path = Join-Path $app 'extension'
    Assert-Test ($welcome.Contains([Net.WebUtility]::HtmlEncode($path))) 'Welcome page contains the HTML-escaped Chinese, space and ampersand path'
    Assert-Test (!$welcome.Contains('{{EXTENSION_PATH}}')) 'Welcome page has no unexpanded extension path placeholder'
    $match = [regex]::Match($welcome, '(?s)<code\s+id="extension-path"[^>]*>(.*?)</code>')
    Assert-Test ($match.Success -and [Net.WebUtility]::HtmlDecode($match.Groups[1].Value) -ceq $path) 'Selectable welcome path decodes to the exact installed directory'
}

try {
    Assert-TestPath $testRoot | Out-Null
    Assert-TestPath $app | Out-Null
    Assert-Test (!(Test-Path -LiteralPath $testRoot)) 'Unique test directory did not exist'
    Assert-Test ($app -ne (Join-Path $env:LOCALAPPDATA 'ClipGo')) 'Test installation is separate from the real user installation'
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $before = Get-ExternalState
    [IO.File]::WriteAllText((Join-Path $testRoot 'external-before.json'), $before, $utf8)

    Write-Host ('Waiting for a completed installer and SHA256 sidecar: ' + $Installer)
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ((Test-Path -LiteralPath $Installer -PathType Leaf) -and (Test-Path -LiteralPath ($Installer + '.sha256') -PathType Leaf)) {
            $expectedHash = ((Get-Content -LiteralPath ($Installer + '.sha256') -Raw).Trim() -split '\s+')[0]
            if ($expectedHash -match '^[0-9a-fA-F]{64}$' -and (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -eq $expectedHash) {
                $ready = $true
                break
            }
        }
        Start-Sleep -Seconds 2
    }
    Assert-Test $ready 'Installer build is complete and SHA256 matches its sidecar'
    Run-Installer
    Assert-Layout
    $ownedBefore = @(Read-Inventory)
    Assert-ExternalState 'first installation'

    $sentinels = [ordered]@{}
    $sentinels[(Join-Path $app 'config.json')] = '{"installerTest":true,"note":"保留设置"}'
    $sentinels[(Join-Path $app 'host.log')] = "Installer test log: 保留日志`r`n"
    $sentinels[(Join-Path $app '用户 视频\示例 & 保留.mp4')] = 'ClipGo test sentinel, not a real media file.'
    $sentinels[(Join-Path $app 'extension\personal-note.txt')] = 'Unknown user file must survive reinstall and uninstall.'
    $sentinelHashes = [ordered]@{}
    foreach ($path in $sentinels.Keys) {
        Assert-TestPath $path | Out-Null
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
        [IO.File]::WriteAllText($path, $sentinels[$path], $utf8)
        $sentinelHashes[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }

    Run-Installer
    Assert-Layout
    $ownedAfter = @(Read-Inventory)
    foreach ($path in $sentinelHashes.Keys) {
        Assert-Test ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $sentinelHashes[$path]) ('Reinstall preserves user sentinel: ' + [IO.Path]::GetFileName($path))
    }
    Assert-ExternalState 'reinstallation'

    Write-Host 'Uninstalling the isolated copy'
    $uninstaller = Assert-TestPath (Join-Path $app 'Uninstall.exe')
    # _?= must be last, unquoted. It disables NSIS self-copy so -Wait observes
    # the actual uninstall process, not a parent that exits immediately.
    $process = Start-Process -FilePath $uninstaller -ArgumentList ("/S _?=" + $app) -WindowStyle Hidden -Wait -PassThru
    try {
        $process.Refresh()
        Assert-Test ($process.ExitCode -eq 0) ('Silent uninstaller exit code is zero (actual ' + $process.ExitCode + ')')
    } finally { $process.Dispose() }
    foreach ($relative in @($ownedBefore + $ownedAfter + @('ClipGoSetup.exe', 'open-guide.ps1', 'setup-locales.json', 'uninstall-runtime.ps1', 'ClipGo.ico') | Sort-Object -Unique)) {
        Assert-Test (!(Test-Path -LiteralPath (Join-Path $app $relative))) ('Uninstall removes managed file: ' + $relative)
    }
    foreach ($path in $sentinelHashes.Keys) {
        Assert-Test (Test-Path -LiteralPath $path -PathType Leaf) ('Uninstall keeps user sentinel: ' + [IO.Path]::GetFileName($path))
        Assert-Test ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $sentinelHashes[$path]) ('Uninstall leaves sentinel bytes unchanged: ' + [IO.Path]::GetFileName($path))
    }
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        Assert-TestPath $uninstaller | Out-Null
        Remove-Item -LiteralPath $uninstaller -Force
        $report.notes += 'Removed the isolated Uninstall.exe after exit: _?= disables self-copy, so Windows may lock this one running file during uninstall.'
    }
    Assert-Test (!(Test-Path -LiteralPath $uninstaller)) 'Isolated uninstaller executable is removed after its process exits'
    Assert-ExternalState 'uninstallation'
    $report.status = 'passed'
    $exitCode = 0
} catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
    Write-Host ('INSTALLER TEST FAILED: ' + $report.error)
} finally {
    if ($before) {
        try {
            $after = Get-ExternalState
            $report.externalStateUnchanged = ($after -ceq $before)
            if ($after -cne $before) { $report.status = 'failed'; $exitCode = 1; $report.error += ' External registration or Start menu changed.' }
            [IO.File]::WriteAllText((Join-Path $testRoot 'external-after.json'), $after, $utf8)
        } catch { $report.status = 'failed'; $exitCode = 1; $report.error += ' Could not verify final external state: ' + $_.Exception.Message }
    }
    $report.finished = (Get-Date).ToString('o')
    $report.checks = $checks.ToArray()
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 30), $utf8)
    }
    Write-Host ('Installer test ' + $report.status + '; report: ' + $reportPath)
}
exit $exitCode
