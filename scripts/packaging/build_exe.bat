@echo off
setlocal

if not exist .venv (
  python -m venv .venv
)

call .venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

python -c "import numpy as np; from insightface.app import FaceAnalysis; app=FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); app.prepare(ctx_id=0, det_size=(640,640)); app.get(np.zeros((640,640,3), dtype='uint8'))"

set "STATIC_DIR=%CD%\scripts\webui\static"
set "MODEL_CACHE=%USERPROFILE%\.insightface"
set "PYINSTALLER_ARGS=--noconfirm --clean --onefile --windowed --name ClassOnFace"
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --add-data "%STATIC_DIR%;webui/static""
set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --collect-all src"

if exist "%MODEL_CACHE%" (
  echo Bundling InsightFace cache from "%MODEL_CACHE%"
  set "PYINSTALLER_ARGS=%PYINSTALLER_ARGS% --add-data "%MODEL_CACHE%;.insightface""
) else (
  echo Warning: InsightFace cache not found at "%MODEL_CACHE%". Models will download on first run.
)

pyinstaller %PYINSTALLER_ARGS% scripts/web_ui.py

echo.
echo Build done. Find exe under dist\ClassOnFace.exe
endlocal
