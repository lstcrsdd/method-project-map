@echo off
rem Open the interactive Method Project Map in the browser (Windows). Double-click it, or:
rem   pmap-ui.bat                    the last project you opened, or choose a folder
rem   pmap-ui.bat PROJECT_FOLDER     that project
rem Running it again reuses the open map; the map stops by itself a while after its browser tab is closed.
setlocal
set "HERE=%~dp0"
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY set "PY=python"
if exist "%USERPROFILE%\.pmap\venv\Scripts\python.exe" set PY="%USERPROFILE%\.pmap\venv\Scripts\python.exe"
%PY% -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "%HERE%." ui %*
if errorlevel 1 pause
endlocal
