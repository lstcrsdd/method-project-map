@echo off
rem Method Project Map installer for Windows.
rem   install.bat            install the pmap command and connect Claude Code
rem   install.bat --codex    connect Codex instead;   --all   both;   --none   only the pmap command
rem Undo:  "%USERPROFILE%\.pmap\bin\pmap.cmd" disconnect --all
setlocal
set "HERE=%~dp0"
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
echo Method Project Map needs Python 3.9 or newer: https://www.python.org/downloads/
exit /b 1
:havepy
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 goto oldpy
set "MODE=%~1"
if "%MODE%"=="" set "MODE=--claude"
if "%MODE%"=="--none" goto runtime
%PY% -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "%HERE%." connect %MODE%
goto done
:runtime
%PY% -I -c "import sys; sys.path.insert(0, sys.argv[1]); from pmap.connect import install_runtime; print('Installed:', install_runtime())" "%HERE%."
:done
%PY% -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "%HERE%." shortcut
echo.
echo Done. Open Method Project Map from the Start menu, or run:  "%USERPROFILE%\.pmap\bin\pmap.cmd" ui
echo Tip: add %USERPROFILE%\.pmap\bin to PATH to type just "pmap".
endlocal
exit /b 0
:oldpy
echo Method Project Map needs Python 3.9 or newer.
exit /b 1
