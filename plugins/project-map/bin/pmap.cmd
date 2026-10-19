@echo off
rem Method Project Map CLI bundled with the plugin
set "PMAP_LIB=%~dp0..\lib"
where py >nul 2>nul
if errorlevel 1 (set "PMAP_PY=python") else (set "PMAP_PY=py -3")
if exist "%USERPROFILE%\.pmap\venv\Scripts\python.exe" set "PMAP_PY="%USERPROFILE%\.pmap\venv\Scripts\python.exe""
%PMAP_PY% -I -c "import sys;sys.path.insert(0,sys.argv.pop(1));sys.argv[0]='pmap';from pmap.cli import main;sys.exit(main())" "%PMAP_LIB%" %*
