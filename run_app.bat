@echo off
rem Starts the desktop app. Run setup_windows.bat once before the first use.
setlocal
cd /d "%~dp0"
set "ENV_NAME=video-editing"

call :find_conda
if not defined CONDA_BAT (
  echo [ERROR] Miniconda was not found. Run setup_windows.bat first.
  pause
  exit /b 1
)

call "%CONDA_BAT%" activate %ENV_NAME%
if errorlevel 1 (
  echo [ERROR] The "%ENV_NAME%" environment does not exist. Run setup_windows.bat first.
  pause
  exit /b 1
)
start "" pythonw -m app
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
