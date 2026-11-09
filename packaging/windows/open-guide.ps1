$ErrorActionPreference = 'Stop'
$guide = Join-Path $PSScriptRoot 'welcome.html'
$candidates = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
)
$browser = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($browser) {
    $extensions = if ([IO.Path]::GetFileName($browser) -eq 'chrome.exe') { 'chrome://extensions/' } else { 'edge://extensions/' }
    $guideUri = ([Uri]$guide).AbsoluteUri
    # These are interactive pages the recipient needs for the last installation step.
    Start-Process -FilePath $browser -ArgumentList @($extensions, $guideUri)
} else {
    Start-Process -FilePath $guide
}
