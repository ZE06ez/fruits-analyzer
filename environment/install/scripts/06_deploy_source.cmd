@echo off
setlocal
set "PACK_ROOT=%~dp0.."
set "WORK_ROOT=%USERPROFILE%\STM32-Workspace"
set "PROJECT_ROOT=%WORK_ROOT%\fruits-analyzer-STM32"

if exist "%PROJECT_ROOT%" (
  echo ERROR: Destination already exists. Nothing was overwritten:
  echo %PROJECT_ROOT%
  exit /b 1
)

if not exist "%WORK_ROOT%" mkdir "%WORK_ROOT%"
echo Extracting the STM32 branch source...
tar.exe -xf "%PACK_ROOT%\source\fruits-analyzer-STM32.zip" -C "%WORK_ROOT%"

if exist "%PROJECT_ROOT%\stm32\工程源码\CMakePresets.json" (
  echo Source deployed successfully:
  echo %PROJECT_ROOT%
) else (
  echo ERROR: Expected STM32 project was not found after extraction.
  exit /b 1
)
pause

