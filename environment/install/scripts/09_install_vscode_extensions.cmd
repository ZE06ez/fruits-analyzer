@echo off
setlocal
set "PACK_ROOT=%~dp0.."

where code >nul 2>nul
if errorlevel 1 (
  echo ERROR: VS Code command was not found. Install VS Code and open a new terminal first.
  exit /b 1
)

code --install-extension "%PACK_ROOT%\vscode_extensions\ms-vscode.cpptools-latest.vsix" --force
if errorlevel 1 exit /b 1
code --install-extension "%PACK_ROOT%\vscode_extensions\ms-vscode.cmake-tools-latest.vsix" --force
if errorlevel 1 exit /b 1
code --install-extension "%PACK_ROOT%\vscode_extensions\marus25.cortex-debug-latest.vsix" --force
if errorlevel 1 exit /b 1

echo VS Code extensions installed.
code --list-extensions
pause

