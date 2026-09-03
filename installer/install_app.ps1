# Codexifyr WebLab installer copy helper v4.2.3
# No command-line path arguments are used intentionally. Windows PowerShell can
# split quoted native arguments containing spaces/trailing backslashes in some
# launch situations. Deriving the paths here avoids that class of installer bug.

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceRoot = Split-Path -Parent $ScriptDir
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$AppRoot = Join-Path $env:LOCALAPPDATA 'Codexifyr WebLab'
$AppDir = Join-Path $AppRoot 'App'
$DataDir = Join-Path $AppRoot 'Data'
$Stage = Join-Path $env:TEMP ('CodexifyrInstall-' + [guid]::NewGuid().ToString('N'))

$skipDirs = @('.venv', '.buildvenv', 'runtime', 'dist', 'build', '__pycache__', '.git', '.idea', '.vscode')
$skipFiles = @('*.pyc', '*.pyo')

function Copy-CodexifyrTree {
    param([string]$From, [string]$To)
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    Get-ChildItem -LiteralPath $From -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($skipDirs -contains $_.Name) { return }
            $childTo = Join-Path $To $_.Name
            Copy-CodexifyrTree -From $_.FullName -To $childTo
        } else {
            foreach ($pattern in $skipFiles) {
                if ($_.Name -like $pattern) { return }
            }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $To $_.Name) -Force
        }
    }
}

try {
    New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null

    Write-Host 'Preparing application files...'
    Copy-CodexifyrTree -From $SourceRoot -To $Stage

    $required = @(
        'desktop_app.py',
        'requirements.txt',
        'backend\server.py',
        'frontend\index.html',
        'frontend\assets\favicon.ico'
    )
    foreach ($rel in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $Stage $rel))) {
            throw "Installer source is incomplete. Missing: $rel"
        }
    }

    # Stop only an older Codexifyr desktop process launched from this install folder.
    try {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                ($_.CommandLine -like "*$AppDir*") -and
                ($_.CommandLine -like '*desktop_app.py*')
            } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 400
    } catch { }

    if (Test-Path -LiteralPath $AppDir) {
        Write-Host 'Replacing previous application files...'
        $removed = $false
        for ($i=0; $i -lt 5 -and -not $removed; $i++) {
            try {
                Remove-Item -LiteralPath $AppDir -Recurse -Force
                $removed = $true
            } catch {
                Start-Sleep -Milliseconds (500 * ($i + 1))
            }
        }
        if (-not $removed -and (Test-Path -LiteralPath $AppDir)) {
            throw 'Could not replace the previous App folder. Close Codexifyr and try again.'
        }
    }

    Move-Item -LiteralPath $Stage -Destination $AppDir
    Write-Host 'Application files copied successfully.'
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('INSTALL COPY ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
