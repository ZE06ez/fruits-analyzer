@echo off
setlocal
call "%~dp0stm32_env.cmd"
set "PROJECT_DIR=%USERPROFILE%\STM32-Workspace\fruits-analyzer-STM32\stm32\工程源码"
set "HEX_FILE=%PROJECT_DIR%\build\Debug\zhunong.hex"

if not exist "%HEX_FILE%" (
  echo ERROR: Firmware HEX not found. Run 07_build_debug.cmd first.
  exit /b 1
)

echo SAFETY CHECK:
echo - Tungsten lamps and their 12 V supply are disconnected or switched off.
echo - Motors, actuator, fan, and other high-power loads are disconnected.
echo - DAPLink SWDIO, SWCLK, GND, 3.3V/Vref, and optional NRST are correct.
echo - 5 V is NOT being used as the SWD voltage reference.
choice /M "All checks are complete and it is safe to flash"
if errorlevel 2 (
  echo Flash cancelled.
  exit /b 2
)

cd /d "%PROJECT_DIR%"
openocd -f interface/cmsis-dap.cfg -f target/stm32f4x.cfg -c "adapter speed 1000" -c "program build/Debug/zhunong.hex verify reset exit"
pause

