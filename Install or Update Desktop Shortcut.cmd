@echo off
setlocal
set "FACTORY_DEPLOYER=%~dp0scripts\deploy_windows_desktop.py"

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%FACTORY_DEPLOYER%"
goto finished

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python "%FACTORY_DEPLOYER%"
goto finished

:missing_python
echo AI Project Factory requires Python 3.10 or newer with Tkinter.
echo Install or repair Python, then run this installer again.
set "FACTORY_EXIT=1"
goto pause_and_exit

:finished
set "FACTORY_EXIT=%errorlevel%"
if not "%FACTORY_EXIT%"=="0" goto pause_and_exit
echo.
echo The stable AI Project Factory shortcut is ready on your Desktop.

:pause_and_exit
echo.
pause
exit /b %FACTORY_EXIT%
