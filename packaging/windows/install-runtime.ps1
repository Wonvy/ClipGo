# Offline ClipGo runtime installation; compatible with Windows PowerShell 5.1.
# NSIS owns shortcuts and uninstall registration. This script never downloads.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PayloadDir,
    [string]$AppDir = (Join-Path $env:LOCALAPPDATA 'ClipGo'),
    [string]$LogPath = '',
    [switch]$SkipRegistration,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$ExtId = 'mnhnajpalaecohikjibocdeamfmnpena'
$HostName = 'com.clipgo.host'
$Utf8 = New-Object System.Text.UTF8Encoding $false
$Scratch = $null
$ActiveLog = $null
$LogLines = New-Object 'System.Collections.Generic.List[string]'
$OwnedFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

function Write-InstallLog([string]$Message) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message
    Write-Host $Message
    $LogLines.Add($line)
    if ($ActiveLog) { [IO.File]::AppendAllText($ActiveLog, $line + "`r`n", $Utf8) }
}

function Full-Directory([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[A-Za-z]:[\\/]') {
        throw '安装目录和文件包目录必须使用绝对路径。'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.StartsWith('\\') -or $full.TrimEnd('\') -eq [IO.Path]::GetPathRoot($full).TrimEnd('\')) {
        throw '不允许安装到网络路径或磁盘根目录。'
    }
    return $full.TrimEnd('\')
}

function Is-Within([string]$Path, [string]$Root) {
    return $Path.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparseAncestors([string]$Path) {
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw '安装路径不能包含符号链接或目录联接。'
            }
        }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
}

function Assert-SourceTree([string]$Root) {
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($Root)
    while ($stack.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $stack.Pop() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw '离线文件包不能包含符号链接或目录联接。'
            }
            if ($item.PSIsContainer) { $stack.Push($item.FullName) }
        }
    }
}

function Assert-Target([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not (Is-Within $full $AppDir)) { throw '安装目标超出了指定目录。' }
    Assert-NoReparseAncestors $full
    return $full
}

function Record-OwnedFile([string]$Path) {
    $full = Assert-Target $Path
    $OwnedFiles.Add($full.Substring($AppDir.Length + 1)) | Out-Null
}

function Copy-Tree([string]$Source, [string]$Destination, [bool]$SkipCache = $false) {
    $target = Assert-Target $Destination
    [IO.Directory]::CreateDirectory($target) | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($SkipCache -and ($item.Name -eq '__pycache__' -or $item.Extension -in '.pyc', '.pyo')) { continue }
        $out = Assert-Target (Join-Path $target $item.Name)
        if ($item.PSIsContainer) { Copy-Tree $item.FullName $out $SkipCache }
        else {
            Copy-Item -LiteralPath $item.FullName -Destination $out -Force
            Record-OwnedFile $out
        }
    }
}

function Quote-Argument([string]$Argument) {
    # Windows CommandLineToArgvW quoting, including trailing backslashes.
    $escaped = [regex]::Replace($Argument, '(\\*)"', '$1$1\"')
    return '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
}

function Invoke-CheckedTool([string]$Executable, [string[]]$Arguments, [string]$Label) {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $Utf8
    $start.StandardErrorEncoding = $Utf8
    $start.WorkingDirectory = $Scratch
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "$Label 无法启动。" }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(120000)) {
            $process.Kill()
            throw "$Label 自检超时。"
        }
        $output = $stdout.Result
        $errorOutput = $stderr.Result
        if ($process.ExitCode -ne 0) { throw "$Label 自检失败（退出码 $($process.ExitCode)）。" }
        return ($output + "`n" + $errorOutput).Trim()
    } finally {
        $process.Dispose()
    }
}

function Test-Tools([string]$RuntimeRoot) {
    $checks = @(
        @('python\python.exe', @('--version'), 'Python', '^Python 3\.'),
        @('bin\yt-dlp.exe', @('--version'), 'yt-dlp', '^\d{4}\.\d{2}\.\d{2}'),
        @('bin\ffmpeg.exe', @('-version'), 'FFmpeg', '^ffmpeg version '),
        @('bin\ffprobe.exe', @('-version'), 'FFprobe', '^ffprobe version '),
        @('bin\deno.exe', @('--version'), 'Deno', '^deno \d+\.')
    )
    foreach ($check in $checks) {
        $output = Invoke-CheckedTool (Join-Path $RuntimeRoot $check[0]) $check[1] $check[2]
        $first = ($output -split '\r?\n')[0]
        if ($first -notmatch $check[3]) { throw "$($check[2]) 返回了无效的版本信息。" }
        # Only the version banner is logged; never echo arbitrary subprocess output.
        Write-InstallLog ($first.Substring(0, [Math]::Min(180, $first.Length)))
    }
}

function Test-NativeHost([string]$HostScript, [string]$RuntimeRoot, [string]$ExpectedVersion) {
    $probe = Join-Path $Scratch 'native-probe.py'
    $code = @'
import json, os, pathlib, struct, subprocess, sys, tempfile

bootstrap = '''
import importlib.util, os, sys
sys.dont_write_bytecode = True
script, binaries, scratch = sys.argv[1:]
spec = importlib.util.spec_from_file_location('clipgo_install_probe', script)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
host.BIN_DIR = binaries
host.YTDLP = os.path.join(binaries, 'yt-dlp.exe')
host.FFMPEG = os.path.join(binaries, 'ffmpeg.exe')
host.DENO = os.path.join(binaries, 'deno.exe')
host.CONFIG_FILE = os.path.join(scratch, 'config.json')
host.LOG_FILE = os.path.join(scratch, 'host.log')
host.DEFAULT_DIR = os.path.join(scratch, 'downloads')
host.main()
'''

script, binaries, expected = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix='native-', dir=os.path.dirname(__file__)) as scratch:
    message = json.dumps({'action': 'ping', 'rid': 'installer'}).encode('utf-8')
    result = subprocess.run(
        [sys.executable, '-B', '-c', bootstrap, script, binaries, scratch],
        input=struct.pack('@I', len(message)) + message,
        capture_output=True, timeout=100, creationflags=0x08000000,
    )
    if result.returncode or len(result.stdout) < 4:
        raise SystemExit(1)
    length = struct.unpack('@I', result.stdout[:4])[0]
    if length > 1024 * 1024 or len(result.stdout) != length + 4:
        raise SystemExit(1)
    response = json.loads(result.stdout[4:])
    if (response.get('type') != 'pong' or response.get('rid') != 'installer'
            or response.get('version') != expected or not response.get('ffmpeg')
            or not response.get('deno') or not response.get('ytdlp')
            or response['ytdlp'].startswith('error:')):
        raise SystemExit(1)
print('Native host ' + expected + ' OK')
'@
    [IO.File]::WriteAllText($probe, $code, $Utf8)
    $output = Invoke-CheckedTool (Join-Path $RuntimeRoot 'python\python.exe') @(
        '-B', $probe, $HostScript, (Join-Path $RuntimeRoot 'bin'), $ExpectedVersion
    ) '本地助手通信'
    if ($output -ne "Native host $ExpectedVersion OK") { throw '本地助手通信自检失败。' }
    Write-InstallLog $output
}

try {
    $PayloadDir = Full-Directory $PayloadDir
    $AppDir = Full-Directory $AppDir
    Assert-NoReparseAncestors $PayloadDir
    Assert-NoReparseAncestors $AppDir
    if ((Is-Within $AppDir $PayloadDir) -or (Is-Within $PayloadDir $AppDir)) {
        throw '安装目录与离线文件包目录不能互相包含。'
    }
    foreach ($protectedRoot in @($env:WINDIR, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:USERPROFILE, $env:LOCALAPPDATA)) {
        if ($protectedRoot -and $AppDir.Equals([IO.Path]::GetFullPath($protectedRoot).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
            throw '不能将系统目录或用户目录本身作为安装目录。'
        }
    }
    if (-not $ValidateOnly) {
        # Start the log as soon as the destination has passed safety checks,
        # so missing payloads, busy processes, and preflight failures are logged.
        if (-not $LogPath) { $LogPath = Join-Path $AppDir 'install.log' }
        $LogPath = Assert-Target $LogPath
        if ([IO.Path]::GetExtension($LogPath) -ne '.log') { throw '安装日志必须是安装目录中的 .log 文件。' }
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($LogPath)) | Out-Null
        [IO.File]::AppendAllText($LogPath, '', $Utf8)
        $ActiveLog = $LogPath
    }
    if (-not (Test-Path -LiteralPath $PayloadDir -PathType Container)) { throw '找不到离线文件包目录。' }
    Assert-SourceTree $PayloadDir
    $required = @(
        'host\clipgo_host.py', 'host\plugins\yt_dlp_plugins\extractor\clipgo_tencent.py',
        'extension\manifest.json', 'extension\background.js', 'extension\common.js', 'extension\content.js',
        'extension\douyin-media.js', 'extension\douyin-page.js', 'extension\popup.html', 'extension\popup.js', 'extension\popup.css',
        'extension\icons\icon16.png', 'extension\icons\icon32.png', 'extension\icons\icon48.png', 'extension\icons\icon128.png',
        'runtime\python\python.exe', 'runtime\bin\yt-dlp.exe', 'runtime\bin\ffmpeg.exe', 'runtime\bin\ffprobe.exe', 'runtime\bin\deno.exe',
        'welcome.html'
    )
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $PayloadDir $relative) -PathType Leaf)) {
            throw "离线文件包不完整：缺少 $relative"
        }
    }
    $licenses = Join-Path $PayloadDir 'licenses'
    if (-not (Test-Path -LiteralPath $licenses -PathType Container) -or -not @(Get-ChildItem -LiteralPath $licenses -File -Recurse).Count) {
        throw '离线文件包缺少第三方许可文件。'
    }
    $manifest = Get-Content -LiteralPath (Join-Path $PayloadDir 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $source = Get-Content -LiteralPath (Join-Path $PayloadDir 'host\clipgo_host.py') -Raw -Encoding UTF8
    $match = [regex]::Match($source, '(?m)^VERSION\s*=\s*["'']([^"'']+)["'']')
    if (-not $match.Success -or $manifest.version -ne $match.Groups[1].Value -or $manifest.version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') {
        throw '扩展与本地助手的版本不一致。'
    }
    $Version = [string]$manifest.version
    $allowedIds = @($ExtId)
    $storeIdsPath = Join-Path $PayloadDir 'host\browser-stores.json'
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
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash([Convert]::FromBase64String($manifest.key)) } finally { $sha.Dispose() }
    $actualId = -join ($hash[0..15] | ForEach-Object { [char](97 + ($_ -shr 4)); [char](97 + ($_ -band 15)) })
    if ($actualId -ne $ExtId) { throw '扩展公钥与固定扩展 ID 不匹配。' }

    if (-not $ValidateOnly) {
        foreach ($process in Get-Process -Name 'yt-dlp', 'ffmpeg', 'ffprobe', 'deno', 'python', 'pythonw' -ErrorAction SilentlyContinue) {
            $processPath = $null
            try { $processPath = $process.Path } catch { }
            if ($processPath -and (Is-Within ([IO.Path]::GetFullPath($processPath)) $AppDir)) {
                throw 'ClipGo 正在下载或处理视频，请结束当前下载并关闭扩展弹窗后重试安装。'
            }
        }
    }
    $Scratch = Join-Path ([IO.Path]::GetTempPath()) ('clipgo-install-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($Scratch) | Out-Null
    Write-InstallLog "正在校验 ClipGo $Version 离线文件包…"
    $runtime = Join-Path $PayloadDir 'runtime'
    Test-Tools $runtime
    Test-NativeHost (Join-Path $PayloadDir 'host\clipgo_host.py') $runtime $Version
    if ($ValidateOnly) {
        Write-InstallLog '校验通过；未安装、未写入注册表。'
        exit 0
    }

    Write-InstallLog '正在安装程序文件；保留现有设置和下载内容…'
    Copy-Tree (Join-Path $runtime 'python') (Join-Path $AppDir 'python')
    Copy-Tree (Join-Path $runtime 'bin') (Join-Path $AppDir 'bin')
    Copy-Tree (Join-Path $PayloadDir 'host\plugins') (Join-Path $AppDir 'plugins') $true
    Copy-Tree (Join-Path $PayloadDir 'extension') (Join-Path $AppDir 'extension')
    Copy-Tree $licenses (Join-Path $AppDir 'licenses')
    Copy-Item -LiteralPath (Join-Path $PayloadDir 'host\clipgo_host.py') -Destination (Assert-Target (Join-Path $AppDir 'clipgo_host.py')) -Force
    Record-OwnedFile (Join-Path $AppDir 'clipgo_host.py')
    $launcher = "@echo off`r`n`"%~dp0python\python.exe`" `"%~dp0clipgo_host.py`" %*`r`n"
    [IO.File]::WriteAllText((Assert-Target (Join-Path $AppDir 'clipgo_host.bat')), $launcher, [Text.Encoding]::ASCII)
    Record-OwnedFile (Join-Path $AppDir 'clipgo_host.bat')
    $welcome = Get-Content -LiteralPath (Join-Path $PayloadDir 'welcome.html') -Raw -Encoding UTF8
    $welcome = $welcome.Replace('{{EXTENSION_PATH}}', [Net.WebUtility]::HtmlEncode((Join-Path $AppDir 'extension')))
    $welcome = $welcome.Replace('{{APP_DIR}}', [Net.WebUtility]::HtmlEncode($AppDir)).Replace('{{VERSION}}', [Net.WebUtility]::HtmlEncode($Version))
    [IO.File]::WriteAllText((Assert-Target (Join-Path $AppDir 'welcome.html')), $welcome, $Utf8)
    Record-OwnedFile (Join-Path $AppDir 'welcome.html')
    $nativeManifest = [ordered]@{
        name = $HostName; description = 'ClipGo download helper'
        path = (Join-Path $AppDir 'clipgo_host.bat'); type = 'stdio'
        allowed_origins = @($allowedIds | Select-Object -Unique | ForEach-Object { "chrome-extension://$_/" })
    }
    $manifestPath = Assert-Target (Join-Path $AppDir "$HostName.json")
    [IO.File]::WriteAllText($manifestPath, ($nativeManifest | ConvertTo-Json -Depth 4), $Utf8)
    Record-OwnedFile $manifestPath
    Test-Tools $AppDir
    Test-NativeHost (Join-Path $AppDir 'clipgo_host.py') $AppDir $Version
    $ownershipPath = Assert-Target (Join-Path $AppDir 'installed-files.json')
    Record-OwnedFile $ownershipPath
    [IO.File]::WriteAllText($ownershipPath, (ConvertTo-Json -InputObject @($OwnedFiles | Sort-Object)), $Utf8)
    if (-not $SkipRegistration) {
        foreach ($key in @(
            'HKCU:\Software\Google\Chrome\NativeMessagingHosts',
            'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts',
            'HKCU:\Software\Chromium\NativeMessagingHosts',
            'HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts'
        )) {
            New-Item -Path "$key\$HostName" -Value $manifestPath -Force | Out-Null
        }
        Write-InstallLog '已为当前用户注册本地助手，无需管理员权限。'
    }
    Write-InstallLog "ClipGo $Version 安装完成。"
    exit 0
} catch {
    $message = '安装失败：' + $_.Exception.Message
    try { Write-InstallLog $message } catch { Write-Host $message }
    exit 1
} finally {
    if ($Scratch -and (Test-Path -LiteralPath $Scratch)) {
        # Scratch was generated here, is under TEMP, and is never the install root.
        $scratchFull = [IO.Path]::GetFullPath($Scratch)
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ((Is-Within $scratchFull $tempRoot) -and [IO.Path]::GetFileName($scratchFull).StartsWith('clipgo-install-')) {
            Remove-Item -LiteralPath $scratchFull -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
