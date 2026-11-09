param([Parameter(Mandatory=$true)][string]$IconPng, [Parameter(Mandatory=$true)][string]$Output)
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object Drawing.Bitmap(164, 314)
$g = [Drawing.Graphics]::FromImage($bitmap)
$g.SmoothingMode = 'AntiAlias'
$g.TextRenderingHint = 'AntiAliasGridFit'
$rect = New-Object Drawing.Rectangle(0, 0, 164, 314)
$gradient = New-Object Drawing.Drawing2D.LinearGradientBrush($rect, [Drawing.Color]::FromArgb(37, 24, 58), [Drawing.Color]::FromArgb(107, 52, 139), 60)
$white = New-Object Drawing.SolidBrush([Drawing.Color]::White)
$muted = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(213, 194, 232))
$accent = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(55, 233, 97, 176))
$title = New-Object Drawing.Font('Segoe UI', 20, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
$small = New-Object Drawing.Font('Segoe UI', 10, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
$icon = [Drawing.Image]::FromFile($IconPng)
try {
    $g.FillRectangle($gradient, $rect)
    $g.FillEllipse($accent, -80, 190, 260, 260)
    $g.DrawImage($icon, 26, 32, 52, 52)
    $g.DrawString('ClipGo', $title, $white, 24, 100)
    $g.DrawString("YOUR VIDEOS.`nYOUR DEVICE.", $small, $muted, 26, 138)
    $g.DrawString("LOCAL SETUP`nChrome / Edge", $small, $white, 26, 261)
    $bitmap.Save($Output, [Drawing.Imaging.ImageFormat]::Bmp)
} finally {
    foreach ($resource in @($icon, $title, $small, $white, $muted, $accent, $gradient, $g, $bitmap)) { $resource.Dispose() }
}
