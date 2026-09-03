# Codexifyr WebLab shell integration helper v4.2.3
# No command-line path parameters are used. Paths are derived here so spaces in
# Windows user/profile paths cannot be split by cmd.exe / PowerShell quoting.

$ErrorActionPreference = 'Stop'

$AppRoot = Join-Path $env:LOCALAPPDATA 'Codexifyr WebLab'
$AppDir = Join-Path $AppRoot 'App'
$Launcher = Join-Path $AppRoot 'Launch Codexifyr.vbs'
$Icon = Join-Path $AppDir 'frontend\assets\favicon.ico'

try {
    $w = New-Object -ComObject WScript.Shell
    $desktop = $w.SpecialFolders.Item('Desktop')
    $programs = $w.SpecialFolders.Item('Programs')

    if ([string]::IsNullOrWhiteSpace($desktop)) {
        $desktop = [Environment]::GetFolderPath('Desktop')
    }
    if ([string]::IsNullOrWhiteSpace($programs)) {
        $programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    }

    $menu = Join-Path $programs 'Codexifyr WebLab'
    New-Item -ItemType Directory -Path $menu -Force | Out-Null

    $desktopLink = Join-Path $desktop 'Codexifyr WebLab.lnk'
    $startLink = Join-Path $menu 'Codexifyr WebLab.lnk'
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'

    foreach ($link in @($desktopLink, $startLink)) {
        $s = $w.CreateShortcut($link)
        $s.TargetPath = $wscript
        $s.Arguments = '"' + $Launcher + '"'
        $s.WorkingDirectory = $AppDir
        if (Test-Path -LiteralPath $Icon) {
            $s.IconLocation = $Icon + ',0'
        }
        $s.Description = 'Codexifyr WebLab'
        $s.Save()
    }

    $reg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexifyrWebLab'
    New-Item -Path $reg -Force | Out-Null
    New-ItemProperty -Path $reg -Name DisplayName -Value 'Codexifyr WebLab' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $reg -Name DisplayVersion -Value '4.2.3' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $reg -Name Publisher -Value 'Codexifyr' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $reg -Name DisplayIcon -Value $Icon -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $reg -Name InstallLocation -Value $AppDir -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $reg -Name UninstallString -Value ('"' + (Join-Path $AppRoot 'UNINSTALL CODEXIFYR.bat') + '"') -PropertyType String -Force | Out-Null

    Write-Host 'Desktop shortcut, Start Menu shortcut, and uninstall entry created successfully.'
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('WINDOWS SHELL SETUP ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
