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
$requirementsFull = Join-Path $repoRoot "requirements.txt"
$distExe = Join-Path $repoRoot "dist\ClassOnFace.exe"
$modelCache = Join-Path $env:USERPROFILE ".insightface"

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

    Write-Host "Installing packaging requirements from $(Split-Path $requirementsFull -Leaf)"
    & $venvPython -m pip install -r $requirementsFull pyinstaller

    Write-Host "Prefetching InsightFace models to ensure they are bundled"
    $prefetch = @'
import numpy as np
try:
    from insightface.app import FaceAnalysis
except ImportError as exc:
    raise SystemExit(f"InsightFace import failed: {exc}")

app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=0, det_size=(640, 640))
dummy = np.zeros((640, 640, 3), dtype=np.uint8)
try:
    app.get(dummy)
except Exception:
    pass
'@
    $tmpPrefetch = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmpPrefetch -Value $prefetch -Encoding utf8
    & $venvPython $tmpPrefetch
    Remove-Item $tmpPrefetch -Force

    $pyinstallerExe = Join-Path $venvDir "Scripts\pyinstaller.exe"
    if (-not (Test-Path $pyinstallerExe)) {
        throw "PyInstaller executable not found at $pyinstallerExe"
    }

    Write-Host "Building Windows executable via PyInstaller"
    $staticDir = Join-Path $repoRoot "scripts\webui\static"
    $pyArgs = @(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "ClassOnFace",
        "--add-data", "$staticDir;webui/static"
    )

    if (Test-Path $modelCache) {
        Write-Host "Bundling InsightFace cache from $modelCache"
        $pyArgs += "--add-data"
        $pyArgs += "$modelCache;.insightface"
    } else {
        Write-Warning "InsightFace 캐시($modelCache) 없음. 최초 실행 시 모델을 다시 다운로드합니다."
    }

    $pyArgs += "scripts/web_ui.py"
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
