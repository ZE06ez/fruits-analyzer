@echo off
setlocal

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 launcher.py
  goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
  python launcher.py
  goto done
)

echo Python was not found.
echo Please install Python 3, or run the packaged EXE version.
pause
exit /b 1

:done
if errorlevel 1 (
  echo.
  echo The analyzer exited with an error.
  pause
)
