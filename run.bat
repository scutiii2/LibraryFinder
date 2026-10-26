@echo off
setlocal

set "PROJECT=%~dp0"
set "VENV=%~dp0.venv"
set "PYTHON=py -3.11"

pushd "%PROJECT%" || exit /b 1

:prepare
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating Library-Finder virtual environment...
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 goto error
)

"%VENV%\Scripts\python.exe" -c "import library_finder, laya, fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo Installing Library-Finder, local Laya support, and the web UI...
    "%VENV%\Scripts\python.exe" -m pip install -e ".[dev,laya,web]"
    if errorlevel 1 goto error
)

echo.
echo Starting Library-Finder. The browser opens after Laya is ready.
echo Press Ctrl+C to stop the server, then R to restart or Q to quit.
echo If Windows asks "Terminate batch job (Y/N)?", answer N to keep this launcher open.
echo.
"%VENV%\Scripts\library-finder-web.exe"
set "EXIT_CODE=%ERRORLEVEL%"

:restart
echo.
echo Library-Finder has stopped. Restart reloads the application and Laya.
choice /c RQ /n /m "[R] Restart  [Q] Quit: "
if errorlevel 2 goto quit
if errorlevel 1 goto prepare
goto quit

:quit
popd
exit /b %EXIT_CODE%

:error
echo Failed to prepare Library-Finder. Check Python 3.11 and network access, then try again.
set "EXIT_CODE=1"
goto restart
