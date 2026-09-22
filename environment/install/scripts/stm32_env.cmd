@echo off
set "STM32_TOOLS_ROOT=%LOCALAPPDATA%\STM32-Tools"
if exist "%STM32_TOOLS_ROOT%\arm-gnu-toolchain-14.3.rel1-mingw-w64-i686-arm-none-eabi\bin\arm-none-eabi-gcc.exe" (
  set "STM32_ARM_ROOT=%STM32_TOOLS_ROOT%\arm-gnu-toolchain-14.3.rel1-mingw-w64-i686-arm-none-eabi"
) else (
  set "STM32_ARM_ROOT=%STM32_TOOLS_ROOT%"
)
set "STM32_OPENOCD_ROOT=%STM32_TOOLS_ROOT%\xpack-openocd-0.12.0-7"
set "STM32_NINJA_ROOT=%STM32_TOOLS_ROOT%\ninja"
set "STM32_CMAKE_ROOT="
if exist "D:\Netease\cmake\bin\cmake.exe" set "STM32_CMAKE_ROOT=D:\Netease\cmake"
if not defined STM32_CMAKE_ROOT if exist "%ProgramFiles%\CMake\bin\cmake.exe" set "STM32_CMAKE_ROOT=%ProgramFiles%\CMake"
if defined STM32_CMAKE_ROOT (
  set "PATH=%STM32_CMAKE_ROOT%\bin;%STM32_ARM_ROOT%\bin;%STM32_OPENOCD_ROOT%\bin;%STM32_NINJA_ROOT%;%PATH%"
) else (
  set "PATH=%STM32_ARM_ROOT%\bin;%STM32_OPENOCD_ROOT%\bin;%STM32_NINJA_ROOT%;%PATH%"
)
