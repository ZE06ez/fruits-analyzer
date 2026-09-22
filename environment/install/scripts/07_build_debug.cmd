@echo off
setlocal
call "%~dp0stm32_env.cmd"
set "PROJECT_DIR=%USERPROFILE%\STM32-Workspace\fruits-analyzer-STM32\stm32\工程源码"

if not exist "%PROJECT_DIR%\CMakePresets.json" (
  echo ERROR: Project not found. Run 06_deploy_source.cmd first.
  exit /b 1
)

cd /d "%PROJECT_DIR%"
cmake --preset Debug
if errorlevel 1 exit /b 1
cmake --build build\Debug
if errorlevel 1 exit /b 1

echo.
echo Build completed. Expected files:
dir /b build\Debug\zhunong.*
pause

