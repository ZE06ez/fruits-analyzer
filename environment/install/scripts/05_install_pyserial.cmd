@echo off
setlocal
set "PACK_ROOT=%~dp0.."
set "PYTHON_EXE=python"

where python >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\anaconda\python.exe" (
    set "PYTHON_EXE=%USERPROFILE%\anaconda\python.exe"
  ) else (
    echo ERROR: Python was not found. Open a new terminal after installing Python.
    exit /b 1
  )
)

"%PYTHON_EXE%" -m pip install --no-index --find-links="%PACK_ROOT%\python_packages" pyserial
"%PYTHON_EXE%" -m pip show pyserial
pause
