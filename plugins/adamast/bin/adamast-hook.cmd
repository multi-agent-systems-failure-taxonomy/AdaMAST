@echo off
setlocal

rem PowerShell 7 exports a PSModulePath that can prevent Windows PowerShell
rem 5.1 from loading its built-in security module. Let the child reconstruct
rem its native defaults before applying the process-local execution policy.
set "PSModulePath="

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
  -File "%~dp0adamast-hook.ps1" ^
  -HostName "%~1" ^
  -EventName "%~2"

exit /b %ERRORLEVEL%
