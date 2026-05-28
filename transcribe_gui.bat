@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%transcribe_gui.py"

rem 优先用 Python 3.11（tkinterdnd2 / faster-whisper 安装在此版本）
set "PYTHON_CMD=py -3.11"
py -3.11 --version >nul 2>&1
if %errorlevel% neq 0 (
    rem 3.11 未找到，回退到默认 py，再回退到 python
    set "PYTHON_CMD=py"
    where py >nul 2>&1
    if %errorlevel% neq 0 set "PYTHON_CMD=python"
)

%PYTHON_CMD% "%PY_SCRIPT%"

if %errorlevel% neq 0 (
    echo.
    echo ================================================
    echo ERROR: Program failed to start or crashed.
    echo Please read the error messages shown above.
    echo.
    echo Common causes:
    echo   - Python 3.11 not found (install from python.org)
    echo   - Missing packages (try: pip install faster-whisper srt_equalizer srt tkinterdnd2)
    echo   - Wrong Python version (requires Python 3.11)
    echo.
    echo Current Python version:
    %PYTHON_CMD% --version
    echo.
    echo ================================================
    echo.
    pause
)

endlocal
