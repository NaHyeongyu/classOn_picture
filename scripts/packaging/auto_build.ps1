param(
    [string]$PythonWingetId = "Python.Python.3.11",
    [switch]$ForceWinget
)

$ErrorActionPreference = "Stop"

function Get-PythonCommand {
    param([switch]$AllowNone)
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Path
        }
    }
    if ($AllowNone) {
        return $null
    }
    throw "Python 3.10+ not found in PATH."
}

function Ensure-Python {
    param([string]$WingetId, [switch]$Force)
    $existing = Get-PythonCommand -AllowNone
    if ($existing -and -not $Force) {
        Write-Host "Python already available at: $existing"
        return $existing
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python is missing and winget is unavailable. Install Python 3.10+ manually, then rerun."
    }

    Write-Host "Installing Python via winget ($WingetId)..."
    $wingetArgs = @(
        "install",
        "--id", $WingetId,
        "-e",
        "--source", "winget",
        "--accept-package-agreements",
        "--accept-source-agreements"
    )
    $process = Start-Process -FilePath "winget" -ArgumentList $wingetArgs -NoNewWindow -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "winget installation failed with exit code $($process.ExitCode)."
    }
    return Get-PythonCommand
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirementsLite = Join-Path $scriptDir "requirements-lite.txt"
$distExe = Join-Path $repoRoot "dist\ClassOnFace.exe"

Push-Location $repoRoot
try {
    $pythonPath = Ensure-Python -WingetId $PythonWingetId -Force:$ForceWinget

    if (-not (Test-Path $venvDir)) {
        Write-Host "Creating virtual environment at $venvDir"
        & $pythonPath -m venv $venvDir
    } else {
        Write-Host "Virtual environment already exists at $venvDir"
    }

    if (-not (Test-Path $venvPython)) {
        throw "Virtual environment python not found at $venvPython"
    }

    Write-Host "Upgrading pip inside virtual environment"
    & $venvPython -m pip install --upgrade pip

    Write-Host "Installing packaging requirements"
    & $venvPython -m pip install -r $requirementsLite pyinstaller

    $pyinstallerExe = Join-Path $venvDir "Scripts\pyinstaller.exe"
    if (-not (Test-Path $pyinstallerExe)) {
        throw "PyInstaller executable not found at $pyinstallerExe"
    }

    Write-Host "Building Windows executable via PyInstaller"
    $pyArgs = @(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "ClassOnFace",
        "--add-data", "scripts/webui/static;webui/static",
        "scripts/web_ui.py"
    )
    & $pyinstallerExe @pyArgs

    if (Test-Path $distExe) {
        Write-Host "Build completed: $distExe"
    } else {
        throw "PyInstaller finished but $distExe not found."
    }
}
finally {
    Pop-Location
}
