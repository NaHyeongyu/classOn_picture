@echo off
setlocal

REM Create venv (optional)
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r scripts/packaging/requirements-lite.txt pyinstaller

REM Build one-file GUI executable
pyinstaller --noconfirm --clean ^
  --onefile --windowed ^
  --name ClassOnFace ^
  --add-data "scripts/webui/static;webui/static" ^
  scripts/web_ui.py

echo.
echo Build done. Find exe under dist\ClassOnFace.exe
endlocal
