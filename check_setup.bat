@echo off
rem ============================================================
rem  Checks the installation and writes setup_check_result.txt
rem  in this folder. Send that file if the app does not start.
rem ============================================================
setlocal
cd /d "%~dp0"
set "ENV_NAME=video-editing"
set "RESULT=%~dp0setup_check_result.txt"

echo Checking the installation. This takes about 30 seconds...
echo.

call :find_conda
if not defined CONDA_BAT (
  > "%RESULT%" echo [PROBLEM] Miniconda was not found. Install Miniconda, then run setup_windows.bat.
  set "RC=1"
  goto :show
)

call "%CONDA_BAT%" activate %ENV_NAME% >nul 2>&1
if /i not "%CONDA_DEFAULT_ENV%"=="%ENV_NAME%" (
  > "%RESULT%" echo [PROBLEM] conda found at %CONDA_BAT% but the "%ENV_NAME%" environment is missing. Run setup_windows.bat.
  >> "%RESULT%" echo.
  call "%CONDA_BAT%" env list >> "%RESULT%" 2>&1
  set "RC=1"
  goto :show
)

python -m app.diagnose "%RESULT%"
set "RC=%errorlevel%"
>> "%RESULT%" echo.
>> "%RESULT%" echo conda: %CONDA_BAT%
if exist "%LOCALAPPDATA%\video-editing-systems\app.log" (
  >> "%RESULT%" echo.
  >> "%RESULT%" echo ---- last lines of app.log ----
  powershell -NoProfile -Command "Get-Content -Encoding UTF8 -Tail 30 '%LOCALAPPDATA%\video-editing-systems\app.log'" >> "%RESULT%" 2>&1
)

:show
echo.
echo Result saved to: %RESULT%
if not defined NOPAUSE start "" notepad "%RESULT%"
if not defined NOPAUSE pause
exit /b %RC%

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
