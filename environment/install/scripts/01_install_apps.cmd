@echo off
setlocal
set "PACK_ROOT=%~dp0.."

echo [1/4] Installing Git for Windows...
start "" /wait "%PACK_ROOT%\packages\Git-2.55.0.5-64-bit.exe"

echo [2/4] Installing CMake...
echo In the installer, select the option to add CMake to PATH.
start "" /wait msiexec.exe /i "%PACK_ROOT%\packages\cmake-4.4.3-windows-x86_64.msi"

echo [3/4] Installing Python...
echo In the installer, enable Add python.exe to PATH.
start "" /wait "%PACK_ROOT%\packages\python-3.13.15-amd64.exe"

choice /M "Install Visual Studio Code"
if errorlevel 2 goto skip_vscode
echo [4/4] Installing Visual Studio Code...
start "" /wait "%PACK_ROOT%\packages\VSCodeUserSetup-x64-latest.exe"

:skip_vscode
echo.
echo Application installers finished.
echo Close this window, open a new terminal, then run 02_extract_tools.cmd.
pause

