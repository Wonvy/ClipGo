param([Parameter(Mandatory = $true)][string]$AppDir)
$ErrorActionPreference = 'Stop'
try {
    $root = [IO.Path]::GetFullPath($AppDir).TrimEnd('\')
    $scriptRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
    if ($root -ne $scriptRoot -or $root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) {
        throw 'Invalid installation directory'
    }
    $prefix = $root + '\'
    $manifestPath = Join-Path $root 'installed-files.json'
    if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Installation inventory is missing' }
    foreach ($process in (Get-Process python, pythonw, yt-dlp, ffmpeg, ffprobe, deno -ErrorAction SilentlyContinue)) {
        if ($process.Path -and $process.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Close active ClipGo downloads before uninstalling'
        }
    }
    $relativeFiles = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $files = foreach ($relative in $relativeFiles) {
        if (![string]::IsNullOrWhiteSpace($relative) -and ![IO.Path]::IsPathRooted($relative)) {
            $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
            if (!$path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe installation inventory' }
            if ([IO.Path]::GetFileName($path) -in @('config.json', 'host.log')) { continue }
            $parent = [IO.Path]::GetDirectoryName($path)
            while ($parent.Length -ge $root.Length) {
                if (Test-Path -LiteralPath $parent) {
                    if ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked installation directory' }
                }
                if ($parent -eq $root) { break }
                $parent = [IO.Path]::GetDirectoryName($parent)
            }
            $path
        } else { throw 'Invalid installation inventory entry' }
    }
    # Remove only registrations that still point to this installation.
    foreach ($key in @('Software\Google\Chrome', 'Software\Microsoft\Edge', 'Software\Chromium', 'Software\BraveSoftware\Brave-Browser')) {
        $regPath = 'HKCU:\' + $key + '\NativeMessagingHosts\com.clipgo.host'
        if (Test-Path -LiteralPath $regPath) {
            $value = (Get-Item -LiteralPath $regPath).GetValue('')
            if ($value -eq (Join-Path $root 'com.clipgo.host.json')) { Remove-Item -LiteralPath $regPath -Force }
        }
    }
    foreach ($path in $files) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force }
    }
    if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath -Force }
    # No recursive deletion: unknown files, settings and saved videos remain untouched.
    $directories = @($files | ForEach-Object { [IO.Path]::GetDirectoryName($_) } | Sort-Object -Unique -Descending)
    foreach ($directory in $directories) {
        while ($directory.Length -gt $root.Length -and $directory.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            if ((Test-Path -LiteralPath $directory -PathType Container) -and !(Get-ChildItem -LiteralPath $directory -Force | Select-Object -First 1)) {
                Remove-Item -LiteralPath $directory -Force
            }
            $directory = [IO.Path]::GetDirectoryName($directory)
        }
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
