@echo off
setlocal
set "FACTORY_LAUNCHER=%~dp0launch_factory.pyw"

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
where pyw >nul 2>nul
if errorlevel 1 goto run_with_py
start "" pyw -3 "%FACTORY_LAUNCHER%"
exit /b 0

:run_with_py
py -3 "%FACTORY_LAUNCHER%"
exit /b %errorlevel%

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
where pythonw >nul 2>nul
if errorlevel 1 goto run_with_python
start "" pythonw "%FACTORY_LAUNCHER%"
exit /b 0

:run_with_python
python "%FACTORY_LAUNCHER%"
exit /b %errorlevel%

:missing_python
echo AI Project Factory requires Python 3.10 or newer with Tkinter.
echo Install or repair Python, then double-click this file again.
pause
exit /b 1
