@echo off
setlocal
set "PACK_ROOT=%~dp0.."
set "TOOLS_ROOT=%LOCALAPPDATA%\STM32-Tools"

where tar.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: Windows tar.exe was not found.
  exit /b 1
)

if not exist "%TOOLS_ROOT%" mkdir "%TOOLS_ROOT%"
if not exist "%TOOLS_ROOT%\ninja" mkdir "%TOOLS_ROOT%\ninja"

if not exist "%TOOLS_ROOT%\ninja\ninja.exe" (
  echo Extracting Ninja...
  tar.exe -xf "%PACK_ROOT%\portable\ninja-win-1.13.2.zip" -C "%TOOLS_ROOT%\ninja"
) else echo Ninja already exists; skipped.

if not exist "%TOOLS_ROOT%\bin\arm-none-eabi-gcc.exe" if not exist "%TOOLS_ROOT%\arm-gnu-toolchain-14.3.rel1-mingw-w64-i686-arm-none-eabi\bin\arm-none-eabi-gcc.exe" (
  echo Extracting GNU Arm Toolchain...
  tar.exe -xf "%PACK_ROOT%\portable\arm-gnu-toolchain-14.3.rel1-mingw-w64-i686-arm-none-eabi.zip" -C "%TOOLS_ROOT%"
) else echo GNU Arm Toolchain already exists; skipped.

if not exist "%TOOLS_ROOT%\xpack-openocd-0.12.0-7\bin\openocd.exe" (
  echo Extracting OpenOCD...
  tar.exe -xf "%PACK_ROOT%\portable\xpack-openocd-0.12.0-7-win32-x64.zip" -C "%TOOLS_ROOT%"
) else echo OpenOCD already exists; skipped.

echo.
echo Portable tools are ready in:
echo %TOOLS_ROOT%
echo Run 03_stm32_shell.cmd next.
pause
