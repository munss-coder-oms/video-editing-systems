@echo off
rem ============================================================
rem  Checks the installation and writes setup_check_result.txt
rem  in this folder. Send that file if the app does not start.
rem ============================================================
setlocal
rem UTF-8 console so conda activation works with Korean Windows user names.
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "ENV_NAME=video-editing"
set "RESULT=%~dp0setup_check_result.txt"

echo Checking the installation. This takes about 30 seconds...
echo.
rem Remove the previous result so an old file is never mistaken for this run.
if exist "%RESULT%" del /f /q "%RESULT%" >nul 2>&1

call :find_conda
if not defined CONDA_BAT (
  > "%RESULT%" echo [PROBLEM] Miniconda was not found. Install Miniconda, then run setup_windows.bat.
  set "RC=1"
  goto :show
)

call "%CONDA_BAT%" activate %ENV_NAME% >nul 2>&1
if /i not "%CONDA_DEFAULT_ENV%"=="%ENV_NAME%" (
  > "%RESULT%" echo [PROBLEM] conda was found but the "%ENV_NAME%" environment is missing. Run setup_windows.bat.
  >> "%RESULT%" echo.
  call "%CONDA_BAT%" env list >> "%RESULT%" 2>&1
  set "RC=1"
  goto :show
)

rem diagnose writes the whole result file itself (UTF-8), including the tail of app.log.
rem Use the environment's own python.exe so a different python on PATH cannot be picked.
set "PY=python"
if exist "%CONDA_PREFIX%\python.exe" set "PY=%CONDA_PREFIX%\python.exe"
"%PY%" -m app.diagnose "%RESULT%"
set "RC=%errorlevel%"
if exist "%RESULT%" goto :show

rem The check program itself could not start (or crashed at once): write what we know.
rem Top-level lines only, so paths with ")" such as "Program Files (x86)" cannot break the script.
set "RC=1"
> "%RESULT%" echo [PROBLEM] The check program stopped before writing a result. Please send this file.
>> "%RESULT%" echo conda: %CONDA_BAT%
>> "%RESULT%" echo environment: %CONDA_PREFIX%
>> "%RESULT%" echo python: %PY%
>> "%RESULT%" echo.
>> "%RESULT%" echo ---- where python ----
where python >> "%RESULT%" 2>&1
>> "%RESULT%" echo.
>> "%RESULT%" echo ---- launch.log ----
powershell -NoProfile -Command "Get-Content -Tail 30 -LiteralPath (Join-Path $env:LOCALAPPDATA 'video-editing-systems\launch.log') -ErrorAction SilentlyContinue" >> "%RESULT%" 2>&1

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
rem Miniconda records its install folder here, wherever it was installed.
if exist "%USERPROFILE%\.conda\environments.txt" for /f "usebackq delims=" %%L in ("%USERPROFILE%\.conda\environments.txt") do if not defined CONDA_BAT if exist "%%L\condabin\conda.bat" set "CONDA_BAT=%%L\condabin\conda.bat"
if defined CONDA_BAT exit /b 0
for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
exit /b 0
