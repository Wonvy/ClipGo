# Local setup UI; Windows PowerShell 5.1. No browser profile or policy changes.
[CmdletBinding()]
param(
    [string]$AppDir = '',
    [ValidateSet('', 'zh_CN', 'zh_TW', 'en', 'ja', 'ko')][string]$Language = '',
    [switch]$NoLaunch,
    [string]$PreviewImage = ''
)
$ErrorActionPreference = 'Stop'
if (!$AppDir) { $AppDir = $PSScriptRoot }
$AppDir = [IO.Path]::GetFullPath($AppDir)
$extensionPath = Join-Path $AppDir 'extension'

function Get-SetupBrowsers {
    foreach ($spec in @(
        @{ Name = 'Google Chrome'; Exe = 'chrome.exe'; Relative = 'Google\Chrome\Application\chrome.exe'; Url = 'chrome://extensions/'; Kind = 'chrome' },
        @{ Name = 'Microsoft Edge'; Exe = 'msedge.exe'; Relative = 'Microsoft\Edge\Application\msedge.exe'; Url = 'edge://extensions/'; Kind = 'edge' }
    )) {
        $paths = @()
        foreach ($hive in @('HKCU:', 'HKLM:')) {
            $key = "$hive\Software\Microsoft\Windows\CurrentVersion\App Paths\$($spec.Exe)"
            if (Test-Path -LiteralPath $key) { $paths += [string](Get-Item -LiteralPath $key).GetValue('') }
        }
        foreach ($root in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
            if ($root) { $paths += Join-Path $root $spec.Relative }
        }
        $found = $paths | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
        if ($found) { [pscustomobject]@{ Name = $spec.Name; Path = $found; Url = $spec.Url; Kind = $spec.Kind } }
    }
}

function Get-SetupBrowserIndex($Browsers) {
    $key = 'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice'
    $choice = Get-ItemProperty -LiteralPath $key -Name ProgId -ErrorAction SilentlyContinue
    if ($choice -and $choice.ProgId -like 'MSEdgeHTM*') {
        for ($i = 0; $i -lt $Browsers.Count; $i++) { if ($Browsers[$i].Kind -eq 'edge') { return $i } }
    }
    return 0
}

function Open-SetupBrowser($Browser) {
    if (!(Test-Path -LiteralPath (Join-Path $extensionPath 'manifest.json') -PathType Leaf)) { throw 'missingExtension' }
    if (!$Browser -or !(Test-Path -LiteralPath $Browser.Path -PathType Leaf)) { throw 'missingBrowser' }
    # Interactive page requested by the recipient. Never force-load the extension.
    Start-Process -FilePath $Browser.Path -ArgumentList @('--new-window', 'about:blank') | Out-Null
}

function Open-SetupFolder {
    if (!(Test-Path -LiteralPath $extensionPath -PathType Container)) { throw 'missingExtension' }
    Start-Process -FilePath (Join-Path $env:WINDIR 'explorer.exe') -ArgumentList ('"' + $extensionPath + '"') | Out-Null
}

if ($NoLaunch) { return }
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
$strings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'setup-locales.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (!$Language) {
    $ini = Join-Path $AppDir 'installer-language.ini'
    $ids = @{ '1033' = 'en'; '2052' = 'zh_CN'; '1028' = 'zh_TW'; '1041' = 'ja'; '1042' = 'ko' }
    if (Test-Path -LiteralPath $ini) {
        $match = [regex]::Match((Get-Content -LiteralPath $ini -Raw), '(?m)^Language=(\d+)')
        if ($match.Success) { $Language = $ids[$match.Groups[1].Value] }
    }
    if (!$Language) {
        $culture = [Globalization.CultureInfo]::CurrentUICulture.Name
        $Language = if ($culture -match '^zh-(TW|HK|Hant)') { 'zh_TW' } elseif ($culture -match '^zh') { 'zh_CN' } elseif ($culture -match '^ja') { 'ja' } elseif ($culture -match '^ko') { 'ko' } else { 'en' }
    }
}
$script:setupLanguage = $Language
$browsers = @(Get-SetupBrowsers)
$script:selectedBrowser = if ($browsers.Count) { $browsers[(Get-SetupBrowserIndex $browsers)] } else { $null }
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
 Title="ClipGo" Width="450" SizeToContent="Height" ResizeMode="NoResize" WindowStartupLocation="CenterScreen"
 Background="#FAF9FC" FontFamily="Segoe UI, Microsoft YaHei UI" FontSize="13" Foreground="#292431" UseLayoutRounding="True">
 <Window.Resources>
  <Style TargetType="Button">
   <Setter Property="Background" Value="Transparent"/><Setter Property="Foreground" Value="#716779"/>
   <Setter Property="Padding" Value="12,10"/><Setter Property="Cursor" Value="Hand"/><Setter Property="BorderThickness" Value="0"/>
   <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button">
    <Border x:Name="surface" Background="{TemplateBinding Background}" CornerRadius="9" Padding="{TemplateBinding Padding}">
     <TextBlock Text="{TemplateBinding Content}" Foreground="{TemplateBinding Foreground}" TextAlignment="Center" TextWrapping="Wrap"/>
    </Border><ControlTemplate.Triggers>
     <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="surface" Property="Opacity" Value="0.75"/></Trigger>
     <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="surface" Property="BorderThickness" Value="2"/><Setter TargetName="surface" Property="BorderBrush" Value="#C6ACE4"/></Trigger>
     <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.4"/></Trigger>
    </ControlTemplate.Triggers>
   </ControlTemplate></Setter.Value></Setter>
  </Style>
  <Style TargetType="RadioButton">
   <Setter Property="Foreground" Value="#7D7484"/><Setter Property="Cursor" Value="Hand"/>
   <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="RadioButton">
    <Border x:Name="pill" Background="Transparent" CornerRadius="8" Padding="12,10">
     <ContentPresenter HorizontalAlignment="Center"/>
    </Border><ControlTemplate.Triggers>
     <Trigger Property="IsChecked" Value="True"><Setter TargetName="pill" Property="Background" Value="White"/><Setter Property="Foreground" Value="#7544A6"/><Setter Property="FontWeight" Value="SemiBold"/></Trigger>
     <Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="pill" Property="BorderThickness" Value="1"/><Setter TargetName="pill" Property="BorderBrush" Value="#B699D3"/></Trigger>
     <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.35"/></Trigger>
    </ControlTemplate.Triggers>
   </ControlTemplate></Setter.Value></Setter>
  </Style>
 </Window.Resources>
 <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
  <StackPanel Margin="26,22,26,22">
   <DockPanel Margin="0,0,0,20">
    <TextBlock Text="ClipGo" FontSize="19" FontWeight="Bold" Foreground="#854DB3"/>
    <TextBlock x:Name="Installed" TextAlignment="Right" VerticalAlignment="Center" Foreground="#8C8393" FontSize="11"/>
   </DockPanel>
   <TextBlock x:Name="Heading" FontSize="24" FontWeight="SemiBold" TextWrapping="Wrap"/>
   <TextBlock x:Name="Intro" Margin="0,10,0,20" Foreground="#83798D" TextWrapping="Wrap" LineHeight="21"/>
   <Border Background="#EFEBF4" CornerRadius="10" Padding="3" Margin="0,0,0,14">
    <UniformGrid Columns="2"><RadioButton x:Name="Chrome" Content="Chrome" GroupName="browser"/><RadioButton x:Name="Edge" Content="Edge" GroupName="browser"/></UniformGrid>
   </Border>
   <Button x:Name="Open" Background="#8750BA" Foreground="White" Padding="14,13"/>
   <TextBlock x:Name="Status" TextWrapping="Wrap" Foreground="#8C7B9C" FontSize="11" Margin="0,10,0,18" LineHeight="18"/>
   <Border Background="White" CornerRadius="12" Padding="18" Margin="0,0,0,16">
    <TextBlock x:Name="Steps" TextWrapping="Wrap" LineHeight="22"/>
   </Border>
   <TextBlock x:Name="PathLabel" Foreground="#8C8393" FontSize="11" Margin="0,0,0,7"/>
   <TextBox x:Name="Path" IsReadOnly="True" BorderThickness="0" Background="Transparent" Foreground="#6F637A" FontSize="11" TextWrapping="Wrap"/>
   <UniformGrid Columns="2" Margin="0,6,0,14"><Button x:Name="Copy"/><Button x:Name="Folder"/></UniformGrid>
   <TextBlock x:Name="Note" TextWrapping="Wrap" FontSize="11" Foreground="#9A909F" LineHeight="18"/>
   <UniformGrid Columns="5" Margin="0,18,0,0" x:Name="Languages">
    <Button Content="简体" Tag="zh_CN" Padding="2,7" FontSize="11"/>
    <Button Content="繁體" Tag="zh_TW" Padding="2,7" FontSize="11"/>
    <Button Content="EN" Tag="en" Padding="2,7" FontSize="11"/>
    <Button Content="日本語" Tag="ja" Padding="2,7" FontSize="11"/>
    <Button Content="한국어" Tag="ko" Padding="2,7" FontSize="11"/>
   </UniformGrid>
  </StackPanel>
 </ScrollViewer>
</Window>
"@
$reader = New-Object Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$window.MaxHeight = [Windows.SystemParameters]::WorkArea.Height - 32
$window.MaxWidth = [Windows.SystemParameters]::WorkArea.Width - 16
$controls = @{}
foreach ($name in @('Installed','Heading','Intro','Chrome','Edge','Open','Status','Steps','PathLabel','Path','Copy','Folder','Note','Languages')) { $controls[$name] = $window.FindName($name) }
$controls.Path.Text = $extensionPath
$controls.Path.ToolTip = $extensionPath
function Update-SetupText {
    $script:t = $strings.($script:setupLanguage)
    $controls.Heading.Text = $t.heading
    $controls.Installed.Text = $t.installed
    $controls.Intro.Text = $t.intro
    $controls.Open.Content = $t.open
    $controls.Copy.Content = $t.copy
    $controls.Folder.Content = $t.folder
    $controls.Note.Text = $t.note
    $controls.PathLabel.Text = $t.path
    $kind = if ($script:selectedBrowser) { $script:selectedBrowser.Kind } else { 'chrome' }
    $controls.Steps.Text = $t.($kind + 'Steps')
    $controls.Open.IsEnabled = $null -ne $script:selectedBrowser
    $controls.Status.Text = if ($script:selectedBrowser) { $t.ready } else { $t.missingBrowser }
    foreach ($button in $controls.Languages.Children) { $button.Background = if ($button.Tag -eq $script:setupLanguage) { '#EFEBF4' } else { 'Transparent' } }
}
foreach ($name in @('chrome','edge')) {
    $choice = $controls[$name]
    $choice.Tag = $name
    $choice.IsEnabled = @($browsers | Where-Object Kind -eq $name).Count -gt 0
    $choice.IsChecked = $script:selectedBrowser -and $script:selectedBrowser.Kind -eq $name
    $choice.add_Checked({ param($sender, $eventArgs)
        $script:selectedBrowser = $browsers | Where-Object Kind -eq $sender.Tag | Select-Object -First 1
        Update-SetupText
    })
}
foreach ($button in $controls.Languages.Children) {
    $button.add_Click({ param($sender, $eventArgs) $script:setupLanguage = $sender.Tag; Update-SetupText })
}
$controls.Open.add_Click({
    try {
        [Windows.Clipboard]::SetText($script:selectedBrowser.Url)
        Open-SetupBrowser $script:selectedBrowser
        $controls.Status.Text = $t.addressCopied
    } catch { $controls.Status.Text = $t.openFailed + ' ' + $script:selectedBrowser.Url }
})
$controls.Copy.add_Click({
    try { [Windows.Clipboard]::SetText($extensionPath); $controls.Status.Text = $t.copied }
    catch { $controls.Path.Focus(); $controls.Path.SelectAll(); $controls.Status.Text = $t.clipboardFailed }
})
$controls.Folder.add_Click({ try { Open-SetupFolder } catch { $controls.Status.Text = $t.openFailed } })
Update-SetupText
if ($PreviewImage) {
    # Render our own view for layout checks without browser or clipboard actions.
    $window.add_ContentRendered({
        $image = New-Object Windows.Media.Imaging.RenderTargetBitmap([int]$window.ActualWidth, [int]$window.ActualHeight, 96, 96, [Windows.Media.PixelFormats]::Pbgra32)
        $image.Render($window)
        $encoder = New-Object Windows.Media.Imaging.PngBitmapEncoder
        $encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($image))
        $file = [IO.File]::Create($PreviewImage)
        try { $encoder.Save($file) } finally { $file.Dispose() }
        $window.Close()
    })
}
[void]$window.ShowDialog()
