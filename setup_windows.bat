@echo off
rem ============================================================
rem  Video Editing Systems - one-time setup (Windows)
rem  1) finds Miniconda  2) creates the "video-editing" env
rem  3) runs the automatic tests
rem  4) puts AI_Helper_Connect into DaVinci Resolve's Workspace - Scripts menu
rem  5) makes the desktop shortcut of the AI helper  6) opens the app once
rem  Run it again after downloading a new version.
rem ============================================================
setlocal
rem UTF-8 console so conda works with Korean Windows user names.
chcp 65001 >nul
set "PYTHONUTF8=1"
rem Ignore packages installed outside the environment (they can clash with its DLLs).
set "PYTHONNOUSERSITE=1"
cd /d "%~dp0"
set "ENV_NAME=video-editing"
set "STATE_DIR=%LOCALAPPDATA%\video-editing-systems"

if not exist "%~dp0environment.yml" goto :not_extracted

call "%~dp0find_conda.bat"
if not defined CONDA_BAT call :ask_conda
if not defined CONDA_BAT goto :no_conda
echo Using conda: %CONDA_BAT%
rem Earlier versions installed PySide6 with pip, which clashes with conda-forge DLLs.
rem Remove it first (does nothing on a fresh install).
call "%CONDA_BAT%" run -n %ENV_NAME% python -m pip uninstall -y PySide6 PySide6_Essentials PySide6_Addons shiboken6 >nul 2>&1

echo.
echo [1/4] Creating / updating the "%ENV_NAME%" environment. The first time takes a few minutes...
echo       If it asks in English to accept Terms of Service, type a and press Enter.
call "%CONDA_BAT%" env update -n %ENV_NAME% -f environment.yml --prune
if errorlevel 1 (
  echo [ERROR] Environment setup failed. See the messages above.
  if not defined NOPAUSE pause
  exit /b 1
)

rem Remember this conda so the app and check_setup.bat use the same one.
rem Saved only after the environment is ready, so a failed install never points them at a conda without it.
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
> "%STATE_DIR%\conda_path.txt" echo %CONDA_BAT%

echo.
echo [2/4] Running automatic tests...
call "%CONDA_BAT%" run -n %ENV_NAME% --no-capture-output python -m pytest -q
if errorlevel 1 (
  echo [WARNING] Some tests failed. Please run check_setup.bat and send the result file.
)

rem Files extracted from a downloaded ZIP carry a "from the internet" mark, which makes
rem Windows show a security warning every time. Clear it for this folder only.
set "ROOT=%~dp0"
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:ROOT -Recurse -File | Unblock-File" >nul 2>&1

echo.
echo [3/4] Adding AI_Helper_Connect to DaVinci Resolve - Workspace - Scripts...
rem Works even before Resolve is installed. The app also does this each time it starts.
call "%CONDA_BAT%" run -n %ENV_NAME% --no-capture-output python -m engine.resolve_link.install
if errorlevel 1 echo [WARNING] The Resolve script could not be installed now. The app will try again when it starts.

echo.
echo [4/4] Creating desktop shortcut...
rem The folder path goes through an environment variable so names with ' or ( ) still work.
rem The shortcut name has Korean letters, which a .bat file cannot hold safely,
rem so PowerShell builds it from character codes: 0xB3C4 0xC6B0 0xBBF8 = Korean "helper".
rem WScript.Shell can only save a shortcut under a name in the system's ANSI code page
rem (an English Windows turns the Korean letters into ???), so it is saved under an
rem English name first and then renamed, which works on every Windows language.
rem The old "Video Editing" shortcut of the previous version is removed.
set "SHORTCUT_OK=1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $old=Join-Path $d 'Video Editing.lnk'; if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }; $n='AI '+[char]0xB3C4+[char]0xC6B0+[char]0xBBF8+'.lnk'; $p=Join-Path $d $n; $t=Join-Path $d 'ai-helper-new.lnk'; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($t); $s.TargetPath=(Join-Path $env:ROOT 'run_app.bat'); $s.WorkingDirectory=$env:ROOT; $s.WindowStyle=1; $s.Save(); if (-not (Test-Path -LiteralPath $t)) { exit 1 }; if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }; Move-Item -LiteralPath $t -Destination $p; if (-not (Test-Path -LiteralPath $p)) { exit 1 }"
if errorlevel 1 set "SHORTCUT_OK="

echo.
if defined SHORTCUT_OK echo Done. From now on, double-click the new "AI ..." icon on the desktop (or run_app.bat) to start.
if not defined SHORTCUT_OK echo [WARNING] The desktop shortcut could not be created. Start the app with run_app.bat in this folder.
echo Then in DaVinci Resolve: open a project, then click Workspace - Scripts - AI_Helper_Connect.
echo The app will now open once so you can see it.
echo.
if not defined NOPAUSE call "%~dp0run_app.bat"
if not defined NOPAUSE pause
exit /b 0

:ask_conda
if defined NOPAUSE exit /b 0
echo.
echo Miniconda was not found automatically.
echo If Miniconda (or Anaconda / Miniforge) is installed, type or paste its folder,
echo for example D:\miniconda3 , and press Enter. Just press Enter to stop.
set "CONDA_DIR="
set /p "CONDA_DIR=Folder: "
if not defined CONDA_DIR exit /b 0
set "CONDA_DIR=%CONDA_DIR:"=%"
if exist "%CONDA_DIR%\condabin\conda.bat" set "CONDA_BAT=%CONDA_DIR%\condabin\conda.bat"
if not defined CONDA_BAT echo That folder has no condabin\conda.bat inside.
exit /b 0

:no_conda
echo.
echo [ERROR] Miniconda was not found.
echo         Install Miniconda first: https://www.anaconda.com/download/success
echo         If your Windows user name is Korean, install it to C:\miniconda3
echo         then run this file again.
echo.
if not defined NOPAUSE pause
exit /b 1

:not_extracted
echo.
echo [ERROR] Please extract the whole ZIP file first:
echo         right-click the ZIP file, choose "Extract All", then run
echo         setup_windows.bat inside the extracted folder.
echo.
if not defined NOPAUSE pause
exit /b 1
