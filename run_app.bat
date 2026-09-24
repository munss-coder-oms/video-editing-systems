@echo off
rem ============================================================
rem  Starts the Video Editing app.
rem  Run setup_windows.bat once before the first use.
rem  If something is wrong, this window stays open and says why.
rem ============================================================
setlocal
cd /d "%~dp0"
set "ENV_NAME=video-editing"
set "LOGDIR=%LOCALAPPDATA%\video-editing-systems"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\launch.log"
set "FLAG=%LOGDIR%\window_ok.flag"
echo ==== %date% %time% run_app.bat in %CD% ====>> "%LOG%"

echo.
echo  Video Editing app is starting...
echo  The app window will open in a few seconds. This black window closes by itself.
echo.

call :find_conda
if not defined CONDA_BAT (
  echo [ERROR] Miniconda was not found. Install Miniconda, then run setup_windows.bat.
  echo Miniconda not found>> "%LOG%"
  goto :fail
)
echo conda: %CONDA_BAT%>> "%LOG%"

call "%CONDA_BAT%" activate %ENV_NAME% >> "%LOG%" 2>&1
if /i not "%CONDA_DEFAULT_ENV%"=="%ENV_NAME%" (
  echo [ERROR] The "%ENV_NAME%" environment is missing. Run setup_windows.bat first.
  echo activate failed, CONDA_DEFAULT_ENV=%CONDA_DEFAULT_ENV%>> "%LOG%"
  goto :fail
)

python -c "import PySide6.QtWidgets" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] The screen library PySide6 could not be loaded. Run setup_windows.bat again.
  goto :fail
)

if exist "%FLAG%" del "%FLAG%"
start "" pythonw -m app
echo started pythonw>> "%LOG%"

rem Wait up to 60 seconds for the app to report that its window is open.
for /l %%i in (1,1,60) do (
  if exist "%FLAG%" goto :ok
  ping -n 2 127.0.0.1 >nul
)
echo [ERROR] The app did not open its window within 60 seconds.
echo window flag not found after 60s>> "%LOG%"
echo App log: %LOGDIR%\app.log
goto :fail

:ok
echo window ok>> "%LOG%"
exit /b 0

:fail
echo.
echo  Log files are in: %LOGDIR%
echo  Please run check_setup.bat and send the result file (setup_check_result.txt).
echo.
if not defined NOPAUSE pause
exit /b 1

:find_conda
rem Finds conda.bat of Miniconda/Anaconda in the usual places.
if defined CONDA_EXE (
  for %%P in ("%CONDA_EXE%\..\..\condabin\conda.bat") do if exist "%%~fP" set "CONDA_BAT=%%~fP"
)
if defined CONDA_BAT exit /b 0
if defined CONDA if exist "%CONDA%\condabin\conda.bat" set "CONDA_BAT=%CONDA%\condabin\conda.bat"
if defined CONDA_BAT exit /b 0
for %%D in ("%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\anaconda3" "C:\miniconda3" "C:\anaconda3") do (
  if not defined CONDA_BAT if exist "%%~D\condabin\conda.bat" set "CONDA_BAT=%%~D\condabin\conda.bat"
)
if defined CONDA_BAT exit /b 0
for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
exit /b 0
