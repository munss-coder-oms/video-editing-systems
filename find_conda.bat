@echo off
rem ============================================================
rem  Finds conda.bat of Miniconda / Anaconda / Miniforge and sets CONDA_BAT.
rem  Used by setup_windows.bat, run_app.bat and check_setup.bat.
rem  No setlocal on purpose: CONDA_BAT must reach the calling file.
rem ============================================================
set "CONDA_BAT="
rem 1) The conda that setup_windows.bat used last time.
set "CONDA_SAVED=%LOCALAPPDATA%\video-editing-systems\conda_path.txt"
if exist "%CONDA_SAVED%" for /f "usebackq delims=" %%L in ("%CONDA_SAVED%") do if not defined CONDA_BAT if exist "%%L" set "CONDA_BAT=%%L"
if defined CONDA_BAT exit /b 0
rem 2) An already activated conda.
if defined CONDA_EXE for %%P in ("%CONDA_EXE%\..\..\condabin\conda.bat") do if exist "%%~fP" set "CONDA_BAT=%%~fP"
if defined CONDA_BAT exit /b 0
if defined CONDA if exist "%CONDA%\condabin\conda.bat" set "CONDA_BAT=%CONDA%\condabin\conda.bat"
if defined CONDA_BAT exit /b 0
rem 3) The usual install folders.
for %%D in ("%USERPROFILE%" "%LOCALAPPDATA%" "%LOCALAPPDATA%\Programs" "%ProgramData%" "C:" "D:") do for %%N in (miniconda3 anaconda3 miniforge3 mambaforge) do if not defined CONDA_BAT if exist "%%~D\%%N\condabin\conda.bat" set "CONDA_BAT=%%~D\%%N\condabin\conda.bat"
if defined CONDA_BAT exit /b 0
rem 4) conda records its folders here, wherever it was installed.
if exist "%USERPROFILE%\.conda\environments.txt" for /f "usebackq delims=" %%L in ("%USERPROFILE%\.conda\environments.txt") do if not defined CONDA_BAT if exist "%%L\condabin\conda.bat" set "CONDA_BAT=%%L\condabin\conda.bat"
if defined CONDA_BAT exit /b 0
rem 5) Installed programs list (registry) and conda folders on every drive.
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\find_conda.ps1"`) do if not defined CONDA_BAT if exist "%%P" set "CONDA_BAT=%%P"
if defined CONDA_BAT exit /b 0
rem 6) PATH.
for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
exit /b 0
