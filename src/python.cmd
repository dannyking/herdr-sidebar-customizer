@echo off
rem Run a plugin entrypoint with the first Python 3.11+ available, like python.sh.
rem The py launcher comes with python.org installs; "python" may be the Microsoft
rem Store stub, which fails the version check below and is skipped.
setlocal
rem Read and write text as UTF-8, not the ANSI code page (settings hold symbols).
set "PYTHONUTF8=1"
set "CHECK=import sys; sys.exit(sys.version_info < (3, 11))"
if defined HERDR_SIDEBAR_PYTHON (
  "%HERDR_SIDEBAR_PYTHON%" -c "%CHECK%" >nul 2>&1
  if not errorlevel 1 goto override
)
py -3 -c "%CHECK%" >nul 2>&1
if not errorlevel 1 goto launcher
python -c "%CHECK%" >nul 2>&1
if not errorlevel 1 goto plain
echo Herdr Sidebar Customizer needs Python 3.11 or newer on PATH. 1>&2
exit /b 1
:override
"%HERDR_SIDEBAR_PYTHON%" %*
exit /b %errorlevel%
:launcher
py -3 %*
exit /b %errorlevel%
:plain
python %*
exit /b %errorlevel%
