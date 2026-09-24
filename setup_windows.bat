@echo off
rem ============================================================
rem  Video Editing Systems - one-time setup (Windows)
rem  1) finds Miniconda  2) creates the "video-editing" env
rem  3) runs the automatic tests  4) makes a desktop shortcut
rem ============================================================
setlocal
cd /d "%~dp0"
set "ENV_NAME=video-editing"

call :find_conda
if not defined CONDA_BAT (
  echo.
  echo [ERROR] Miniconda was not found.
  echo         Install Miniconda first: https://www.anaconda.com/download/success
  echo         then run this file again.
  echo.
  pause
  exit /b 1
)
echo Using conda: %CONDA_BAT%

echo.
echo [1/3] Creating / updating the "%ENV_NAME%" environment (first time takes a few minutes)...
call "%CONDA_BAT%" env update -n %ENV_NAME% -f environment.yml --prune
if errorlevel 1 (
  echo [ERROR] Environment setup failed. See the messages above.
  pause
  exit /b 1
)

echo.
echo [2/3] Running automatic tests...
call "%CONDA_BAT%" run -n %ENV_NAME% --no-capture-output python -m pytest -q
if errorlevel 1 (
  echo [WARNING] Some tests failed. The app may still start, but please report the messages above.
)

echo.
echo [3/3] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Video Editing.lnk')); $s.TargetPath='%~dp0run_app.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"

echo.
echo Done. Double-click "Video Editing" on the desktop (or run_app.bat) to start.
pause
exit /b 0

:find_conda
if defined CONDA_EXE (
  for %%P in ("%CONDA_EXE%\..\..\condabin\conda.bat") do if exist "%%~fP" set "CONDA_BAT=%%~fP"
)
if defined CONDA_BAT exit /b 0
for %%D in ("%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\anaconda3" "C:\miniconda3" "C:\anaconda3") do (
  if not defined CONDA_BAT if exist "%%~D\condabin\conda.bat" set "CONDA_BAT=%%~D\condabin\conda.bat"
)
if defined CONDA_BAT exit /b 0
for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
exit /b 0
