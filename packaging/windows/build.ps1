param([string]$Compiler, [switch]$LocalSetup)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$build = Join-Path $repo 'build\windows'
$cache = Join-Path $build 'cache'
$payload = Join-Path $build 'payload'
$dist = Join-Path $repo 'dist'
$utf8 = New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $cache, $dist | Out-Null

function Clear-BuildDirectory([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (!$resolved.StartsWith($build + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe build cleanup path' }
    if (Test-Path -LiteralPath $resolved) {
        if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked build directory' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Path $resolved -Force | Out-Null
}

function Copy-CleanTree([string]$Source, [string]$Destination) {
    $sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd('\')
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -File -Recurse) {
        $relative = $file.FullName.Substring($sourceRoot.Length + 1)
        if ($relative -match '(^|\\)__pycache__(\\|$)' -or $file.Extension -eq '.pyc') { continue }
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked payload file' }
        $destinationPath = Join-Path $Destination $relative
        New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($destinationPath)) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destinationPath -Force
    }
}

$locks = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-lock.json') -Raw | ConvertFrom-Json
foreach ($asset in $locks) {
    $path = Join-Path $cache $asset.file
    if (!(Test-Path -LiteralPath $path)) {
        Write-Output ('Downloading ' + $asset.name + ' ' + $asset.version)
        & curl.exe -fL --retry 3 --silent --show-error --output $path $asset.url
        if ($LASTEXITCODE -ne 0) { throw ('Download failed: ' + $asset.name) }
    }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $asset.sha256) {
        throw ('SHA256 mismatch, remove the cached file and retry: ' + $path)
    }
}

Clear-BuildDirectory $payload
New-Item -ItemType Directory -Force -Path "$payload\runtime\python", "$payload\runtime\bin", "$payload\host" | Out-Null
Expand-Archive -LiteralPath "$cache\python.zip" -DestinationPath "$payload\runtime\python" -Force
Expand-Archive -LiteralPath "$cache\deno.zip" -DestinationPath "$payload\runtime\bin" -Force
$ffmpeg = Join-Path $build 'ffmpeg'
Clear-BuildDirectory $ffmpeg
Expand-Archive -LiteralPath "$cache\ffmpeg.zip" -DestinationPath $ffmpeg
foreach ($name in 'ffmpeg.exe', 'ffprobe.exe') {
    $binary = @(Get-ChildItem -LiteralPath $ffmpeg -Recurse -File -Filter $name)
    if ($binary.Count -ne 1) { throw ('Missing or ambiguous FFmpeg file: ' + $name) }
    Copy-Item -LiteralPath $binary[0].FullName -Destination "$payload\runtime\bin\$name"
}
Copy-Item -LiteralPath "$cache\yt-dlp.exe" -Destination "$payload\runtime\bin\yt-dlp.exe"
Copy-Item -LiteralPath "$repo\host\clipgo_host.py" -Destination "$payload\host\clipgo_host.py"
Copy-Item -LiteralPath "$repo\host\browser-stores.json" -Destination "$payload\host\browser-stores.json"
Copy-CleanTree "$repo\host\plugins" "$payload\host\plugins"
Copy-CleanTree "$repo\extension" "$payload\extension"
Copy-CleanTree "$PSScriptRoot\licenses" "$payload\licenses"
Copy-Item -LiteralPath "$PSScriptRoot\THIRD-PARTY-NOTICES.txt" -Destination "$payload\licenses\THIRD-PARTY-NOTICES.txt"
Copy-Item -LiteralPath "$PSScriptRoot\runtime-lock.json" -Destination "$payload\licenses\runtime-lock.json"
Copy-Item -LiteralPath "$PSScriptRoot\welcome.html" -Destination "$payload\welcome.html"
Copy-Item -LiteralPath "$payload\runtime\python\LICENSE.txt" -Destination "$payload\licenses\Python-LICENSE.txt" -Force
foreach ($notice in Get-ChildItem -LiteralPath $ffmpeg -File -Recurse | Where-Object Name -Match '^(LICENSE|README)') {
    Copy-Item -LiteralPath $notice.FullName -Destination (Join-Path "$payload\licenses" ('FFmpeg-' + $notice.Name)) -Force
}

$manifest = Get-Content -LiteralPath "$payload\extension\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $manifest.version
$icon = Join-Path $build 'ClipGo.ico'
# ICO can contain the existing PNG images directly, preserving the extension artwork.
$sizes = @(16, 32, 48, 128)
$images = @($sizes | ForEach-Object { ,([IO.File]::ReadAllBytes("$repo\extension\icons\icon$_.png")) })
$stream = [IO.File]::Create($icon)
$writer = New-Object IO.BinaryWriter $stream
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $writer.Write([byte]$sizes[$i]); $writer.Write([byte]$sizes[$i])
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($bytes in $images) { $writer.Write([byte[]]$bytes) }
} finally { $writer.Dispose(); $stream.Dispose() }

if (!$Compiler) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'NSIS\Unicode\makensis.exe')
    )
    $Compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (!$Compiler -or !(Test-Path -LiteralPath $Compiler)) { throw 'NSIS is required. Supply -Compiler with the path to makensis.exe.' }

Write-Output 'Validating offline payload'
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PSScriptRoot\install-runtime.ps1" -PayloadDir $payload -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'Payload validation failed' }

$output = Join-Path $dist "ClipGo-$version-Windows-x64-Setup.exe"
if ($LocalSetup) { $output = Join-Path $dist "ClipGo-$version-Local-Windows-x64-Setup.exe" }
$guideExe = Join-Path $build 'ClipGoSetup.exe'
$sidebar = Join-Path $build 'setup-sidebar.bmp'
& "$PSScriptRoot\make-artwork.ps1" -IconPng "$repo\extension\icons\icon128.png" -Output $sidebar
$compilerCSharp = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compilerCSharp /nologo /target:winexe /platform:x64 /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$guideExe" "$PSScriptRoot\SetupGuide.cs"
if ($LASTEXITCODE -ne 0) { throw 'Setup guide compilation failed' }
$languages = Get-Content -LiteralPath "$PSScriptRoot\installer-locales.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$languageLines = foreach ($language in $languages.PSObject.Properties) {
    foreach ($entry in $language.Value.PSObject.Properties) {
        $value = $entry.Value.Replace('$', '$$').Replace('"', '$\"').Replace("`r", '').Replace("`n", '$\r$\n')
        'LangString ' + $entry.Name + ' ${LANG_' + $language.Name.ToUpperInvariant() + '} "' + $value + '"'
    }
}
$languageFile = Join-Path $build 'installer-languages.nsh'
[IO.File]::WriteAllText($languageFile, ($languageLines -join "`r`n"), $utf8)
$compilerOptions = @('/NOCONFIG', '/INPUTCHARSET', 'UTF8', '/V2')
$modeHelp = & $Compiler /CMDHELP OutFileMode 2>&1 | Out-String
if ($modeHelp -match 'OutFileMode\s+.*aio') { $compilerOptions += '/XOutFileMode aio' }
& $Compiler @compilerOptions "/DVERSION=$version" "/DPAYLOAD=$payload" "/DOUTPUT=$output" "/DICON=$icon" "/DGUIDEEXE=$guideExe" "/DSIDEBAR=$sidebar" "/DLANGFILE=$languageFile" "$PSScriptRoot\installer.nsi"
if ($LASTEXITCODE -ne 0) { throw 'NSIS compilation failed' }
$hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($output + '.sha256', $hash + '  ' + [IO.Path]::GetFileName($output) + "`r`n", $utf8)
Write-Output ('Built: ' + $output)
Write-Output ('SHA256: ' + $hash)
