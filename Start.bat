@echo off
setlocal EnableExtensions DisableDelayedExpansion
title App Launcher (portable)

REM ─────────────────────────────────────
REM Force UTF-8 for Python stdout/stderr so emoji logs don't crash
REM ─────────────────────────────────────
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM ─────────────────────────────────────
REM App root (this folder)
REM ─────────────────────────────────────
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

REM ─────────────────────────────────────
REM Virtualenv paths
REM ─────────────────────────────────────
set "VENV_DIR=%APP_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

REM ─────────────────────────────────────
REM 1) Pick a base Python to use for venv creation / fallback
REM    Priority:
REM      - PY_EXE (explicit override, ideally Python 3.11)
REM      - py
REM      - python
REM      - python3
REM ─────────────────────────────────────
set "PY_BASE="

if not "%PY_EXE%"=="" (
    set "PY_BASE=%PY_EXE%"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PY_BASE=py"
    ) else (
        where python >nul 2>&1
        if not errorlevel 1 (
            set "PY_BASE=python"
        ) else (
            where python3 >nul 2>&1
            if not errorlevel 1 (
                set "PY_BASE=python3"
            )
        )
    )
)

if "%PY_BASE%"=="" (
    echo [ERROR] No Python interpreter found.
    echo         Tried: PY_EXE, py, python, python3
    echo         Please install Python or add it to your PATH.
    pause
    goto :eof
)

REM ─────────────────────────────────────
REM 2) Ensure per-project venv exists; create if missing
REM    Prefer py -3.11 for creation if available.
REM ─────────────────────────────────────
if not exist "%VENV_PY%" (
    echo [INFO] No virtualenv found at "%VENV_DIR%".
    echo [INFO] Creating virtualenv...

    set "VENV_OK=0"

    REM If using the Python launcher, prefer 3.11 if present
    if /I "%PY_BASE%"=="py" (
        py -3.11 --version >nul 2>&1
        if not errorlevel 1 (
            echo [INFO] Using "py -3.11" to create venv.
            py -3.11 -m venv "%VENV_DIR%"
            if not errorlevel 1 set "VENV_OK=1"
        )
    )

    REM If venv not created yet, fall back to PY_BASE
    if "%VENV_OK%"=="0" (
        echo [INFO] Using "%PY_BASE%" to create venv.
        "%PY_BASE%" -m venv "%VENV_DIR%"
        if not errorlevel 1 set "VENV_OK=1"
    )

    if "%VENV_OK%"=="0" (
        echo [ERROR] Failed to create virtualenv in "%VENV_DIR%".
        echo [WARN]  Falling back to base Python: %PY_BASE%
        set "PY_CMD=%PY_BASE%"
    ) else (
        echo [INFO] Virtualenv created successfully.
        set "PY_CMD=%VENV_PY%"
    )
) else (
    REM venv already exists, use it
    set "PY_CMD=%VENV_PY%"
)

REM If for some reason PY_CMD still isn't set, fall back to base Python
if "%PY_CMD%"=="" (
    set "PY_CMD=%PY_BASE%"
)

REM ─────────────────────────────────────
REM 3) Ensure dependencies are installed in the venv
REM    - If requirements.txt exists
REM    - And Flask is not yet importable
REM    Then run pip install -r requirements.txt
REM ─────────────────────────────────────
if exist "%APP_DIR%requirements.txt" (
    echo [CHECK] Checking for Flask in current environment...
    "%PY_CMD%" -c "import flask" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] Flask not found. Installing dependencies from requirements.txt...
        "%PY_CMD%" -m pip install --upgrade pip
        "%PY_CMD%" -m pip install -r "%APP_DIR%requirements.txt"
        if errorlevel 1 (
            echo [WARN] pip install reported an error. The app may still fail to start.
        ) else (
            echo [INFO] Dependencies installed successfully.
        )
    ) else (
        echo [INFO] Flask already installed in this environment.
    )
) else (
    echo [INFO] No requirements.txt found. Skipping dependency install.
)

REM ─────────────────────────────────────
REM Default HOST/PORT if not provided by caller
REM (HTTP Server Manager can still override these)
REM ─────────────────────────────────────
if "%HOST%"=="" set "HOST=0.0.0.0"
if "%PORT%"=="" set "PORT=8002"

echo [INFO] APP_DIR=%APP_DIR%
echo [INFO] Using Python: %PY_CMD%
echo [INFO] HOST=%HOST%  PORT=%PORT%

REM ─────────────────────────────────────
REM 4) Main entrypoints
REM ─────────────────────────────────────
if exist "%APP_DIR%app.py" (
    echo [RUN] Starting app.py with "%PY_CMD%"  (HOST=%HOST% PORT=%PORT%)
    "%PY_CMD%" "%APP_DIR%app.py"
    goto :eof
)

if exist "%APP_DIR%run.py" (
    echo [RUN] Starting run.py with "%PY_CMD%"  (HOST=%HOST% PORT=%PORT%)
    "%PY_CMD%" "%APP_DIR%run.py"
    goto :eof
)

echo [INFO] No app.py or run.py found in "%APP_DIR%".
echo        Update Start.bat or set the correct command in your server manager.
pause

endlocal
