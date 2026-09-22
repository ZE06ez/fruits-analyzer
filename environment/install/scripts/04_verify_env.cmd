@echo off
setlocal
call "%~dp0stm32_env.cmd"

echo ===== Git =====
where git
git --version
echo.

echo ===== CMake =====
where cmake
cmake --version
echo.

echo ===== Ninja =====
where ninja
ninja --version
echo.

echo ===== GNU Arm GCC =====
where arm-none-eabi-gcc
arm-none-eabi-gcc --version
echo.

echo ===== GNU Arm GDB =====
where arm-none-eabi-gdb
arm-none-eabi-gdb --version
echo.

echo ===== OpenOCD =====
where openocd
openocd --version
echo.

echo ===== Python =====
where python
python --version
echo.

echo Verification finished. Review any errors above.
pause

