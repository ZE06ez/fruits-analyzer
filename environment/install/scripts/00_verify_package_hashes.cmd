@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "PACK_ROOT=%~dp0.."
set "FAILED=0"

goto main

:verify_one
set "REL=%~1"
set "EXPECTED=%~2"
set "ACTUAL="
if not exist "%PACK_ROOT%\%REL%" (
  echo [MISSING] %REL%
  set "FAILED=1"
  exit /b
)
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile "%PACK_ROOT%\%REL%" SHA256') do if not defined ACTUAL set "ACTUAL=%%H"
set "ACTUAL=!ACTUAL: =!"
if /i "!ACTUAL!"=="%EXPECTED%" (
  echo [OK] %REL%
) else (
  echo [FAILED] %REL%
  echo Expected: %EXPECTED%
  echo Actual:   !ACTUAL!
  set "FAILED=1"
)
exit /b

:main
call :verify_one "packages\Git-2.55.0.5-64-bit.exe" d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6
call :verify_one "packages\cmake-4.4.3-windows-x86_64.msi" f3b27c83979727b73540db53dbe610967656b4631746a94b17a1dd0329dd7868
call :verify_one "packages\python-3.13.15-amd64.exe" edec09c4853aeae9ac36efb8c9f95b6b8e2fee65eee56d9767a8b7c69c574403
call :verify_one "packages\VSCodeUserSetup-x64-latest.exe" 9250de3bcb5e415649f681f7dbf36225e68a813d36dafd76f9d3e5e575c8dfab
call :verify_one "portable\ninja-win-1.13.2.zip" 07fc8261b42b20e71d1720b39068c2e14ffcee6396b76fb7a795fb460b78dc65
call :verify_one "portable\xpack-openocd-0.12.0-7-win32-x64.zip" 6bfd3c97135aafef8affc9af1acf34fd0e2b9ca26044506f6abd7f95b7630052
call :verify_one "portable\arm-gnu-toolchain-14.3.rel1-mingw-w64-i686-arm-none-eabi.zip" 836ebe51fd71b6542dd7884c8fb2011192464b16c28e4b38fddc9350daba5ee8
call :verify_one "source\fruits-analyzer-STM32.zip" f8b0f46f01d9f679ca15c55c83bd7c39f787b3097ae320c3610dca480e3e2164
call :verify_one "python_packages\pyserial-3.5-py2.py3-none-any.whl" c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0
call :verify_one "vscode_extensions\ms-vscode.cpptools-latest.vsix" 8b93bda416f8cf2f256d1f1c74c73daf8ab8b47836dc7fac753b493705d47e31
call :verify_one "vscode_extensions\ms-vscode.cmake-tools-latest.vsix" 2a703f43af2b62b12c85394f4226f32cfd562390321fbb93c60811b488e292c0
call :verify_one "vscode_extensions\marus25.cortex-debug-latest.vsix" 183f74dce04b2cdc49695c8a5433288c6fa2477ac224df735eaa74ef6a5baa0d

echo.
if "%FAILED%"=="0" (
  echo ALL PACKAGE HASHES PASSED.
) else (
  echo ONE OR MORE PACKAGE HASHES FAILED. DO NOT INSTALL FAILED FILES.
)
pause
exit /b %FAILED%
