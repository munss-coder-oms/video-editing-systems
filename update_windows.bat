@echo off
rem ============================================================
rem  Video Editing Systems - get the new version into this same folder (Windows)
rem    update_windows.bat [PID] [INSTALL_DIR]
rem  The AI helper runs this from its "..." menu (new version item). Double-click also works
rem  (then it updates the folder it is in; close the AI helper window first).
rem  1) waits until the app (PID) has closed  2) downloads the new version ZIP
rem  3) the new version's own update_windows.bat copies it into the folder (apply mode below):
rem     the code folders are replaced, other files stay
rem  4) runs setup_windows.bat for a quick update (no tests)  5) opens the app again
rem  The branch to download is the one line in update_source.txt.
rem  AIH_UPDATE_ZIP=file uses that ZIP instead of downloading (automatic checks, no internet).
rem ============================================================
setlocal
rem UTF-8 console so folder names with Korean letters work (same as setup_windows.bat).
chcp 65001 >nul
rem Apply mode: update_windows.bat apply NEW_FOLDER INSTALL_DIR copies the unpacked new version
rem into the folder. The update window of the version before calls the NEW version's file this
rem way, so each version decides itself which folders it replaces and which it keeps.
rem Later versions must keep these three arguments.
if /i "%~1"=="apply" goto :apply_mode
title AI helper update
set "WORK=%LOCALAPPDATA%\video-editing-systems\update"
set "UPD_ZIP=%WORK%\download.zip"
set "UPD_EXTRACT=%WORK%\extract"
set "UPD_APPLY=%WORK%\apply_update.bat"
set "UPD_PID=%~1"
set "INSTALL_DIR=%~2"
if not defined INSTALL_DIR set "INSTALL_DIR=%~dp0"
call :tidy_install_dir

rem cmd.exe reads a running .bat file line by line from its byte position, so this file must
rem never be replaced while it runs. The copy in the install folder hands the work to a copy
rem of itself in the temp folder (the app starts that temp copy directly, with AIH_UPDATE_COPY=1).
if /i "%~dp0"=="%WORK%\" goto :worker
if defined AIH_UPDATE_COPY goto :worker
if not exist "%WORK%" mkdir "%WORK%"
copy /y "%~f0" "%WORK%\update_windows.bat" >nul
if errorlevel 1 goto :self_copy_failed
set "AIH_UPDATE_COPY=1"
rem One line on purpose: once the copy returns, nothing more is read from this (replaced) file.
rem The exit code is given as a number: a bare "exit /b" left cmd /c with 0 after a refused update
rem (seen in the Windows check). "if errorlevel" is read when it runs, not when the line is read.
call "%WORK%\update_windows.bat" "%UPD_PID%" "%INSTALL_DIR%" & if errorlevel 1 (exit /b 1) else exit /b 0

:worker
set "AIH_UPDATE_COPY="
if not exist "%WORK%" mkdir "%WORK%"
cd /d "%WORK%"
echo.
echo  Getting the new version of the AI helper into this folder:
echo  "%INSTALL_DIR%"
echo.
if not exist "%INSTALL_DIR%\run_app.bat" goto :not_install
if not exist "%INSTALL_DIR%\setup_windows.bat" goto :not_install

rem PID of the app and AIH_UPDATE_WAIT (seconds, for automatic checks) must be plain numbers.
set "WAIT_MAX=60"
if defined AIH_UPDATE_WAIT set "WAIT_MAX=%AIH_UPDATE_WAIT%"
set "NOT_NUMBER="
for /f "delims=0123456789" %%A in ("%UPD_PID%%WAIT_MAX%") do set "NOT_NUMBER=1"
if defined NOT_NUMBER goto :usage

rem conda cannot replace files of an environment that a running app uses: wait until it has closed.
rem ping is the sleep (timeout fails when there is no keyboard input).
if not defined UPD_PID goto :no_pid
echo Waiting for the AI helper window to close...
set /a WAITED=0
:wait_pid
rem tasklist /fo csv gives "pythonw.exe","1234",... ; the second value is the PID.
set "APP_RUNNING="
for /f "tokens=2 delims=," %%P in ('tasklist /fi "PID eq %UPD_PID%" /fo csv /nh 2^>nul') do if "%%~P"=="%UPD_PID%" set "APP_RUNNING=1"
if not defined APP_RUNNING goto :app_closed
if %WAITED% geq %WAIT_MAX% goto :still_running
set /a WAITED+=1
ping -n 2 127.0.0.1 >nul
goto :wait_pid

:no_pid
if defined NOPAUSE goto :app_closed
echo Close the AI helper window first (DaVinci Resolve can stay open), then press a key here.
pause

:app_closed
call :cleanup
if not defined AIH_UPDATE_ZIP goto :download
if not exist "%AIH_UPDATE_ZIP%" echo [WARNING] AIH_UPDATE_ZIP is not a file. Downloading instead.
if not exist "%AIH_UPDATE_ZIP%" goto :download
echo Using the ZIP file given in AIH_UPDATE_ZIP.
copy /y "%AIH_UPDATE_ZIP%" "%UPD_ZIP%" >nul
if errorlevel 1 goto :no_zip
goto :unpack

:download
set "BRANCH="
if exist "%INSTALL_DIR%\update_source.txt" for /f "usebackq eol=# tokens=1" %%B in ("%INSTALL_DIR%\update_source.txt") do if not defined BRANCH set "BRANCH=%%B"
if not defined BRANCH set "BRANCH=main"
call :fetch "%BRANCH%"
if not errorlevel 1 goto :unpack
rem main is tried only when GitHub says the branch is gone (4 = not found, for example after it was
rem merged). A broken download (no internet, a GitHub error) stops here: main may be an older version.
if not errorlevel 4 goto :no_zip
if /i "%BRANCH%"=="main" goto :no_zip
echo [WARNING] The "%BRANCH%" version is no longer on GitHub. Trying the main version...
call :fetch main
if errorlevel 1 goto :no_zip

:unpack
echo Unpacking...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Expand-Archive -LiteralPath $env:UPD_ZIP -DestinationPath $env:UPD_EXTRACT -Force"
if errorlevel 1 goto :bad_zip
rem The ZIP holds one folder (video-editing-systems-<branch>) with the whole program inside.
set "NEWNAME="
set "EXTRA_ITEM="
for /d %%D in ("%UPD_EXTRACT%\*") do if defined NEWNAME (set "EXTRA_ITEM=1") else (set "NEWNAME=%%~nxD")
if not defined NEWNAME goto :bad_zip
if defined EXTRA_ITEM goto :bad_zip
set "NEW=%UPD_EXTRACT%\%NEWNAME%"
if not exist "%NEW%\environment.yml" goto :bad_zip
if not exist "%NEW%\setup_windows.bat" goto :bad_zip
if not exist "%NEW%\app\__main__.py" goto :bad_zip
rem Never put an older version over this one (__version__ in engine\__init__.py; the same one is fine).
rem 3 = older, 2 = no version number in the ZIP.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; function V($d) { $f = Join-Path (Join-Path $d 'engine') '__init__.py'; if (-not (Test-Path -LiteralPath $f)) { return $null }; $m = [regex]::Match([IO.File]::ReadAllText($f), '__version__\s*=\s*[\x22\x27]([0-9]+(\.[0-9]+)+)[\x22\x27]'); if ($m.Success) { return [version]$m.Groups[1].Value }; return $null }; $old = V $env:INSTALL_DIR; $new = V $env:NEW; if (-not $old) { exit 0 }; if (-not $new) { exit 2 }; if ($new -lt $old) { Write-Host ('  Downloaded version ' + $new + ', version in this folder ' + $old); exit 3 }; exit 0"
if errorlevel 3 goto :older_zip
if errorlevel 1 goto :bad_zip
rem The new version copies itself in (apply mode), so it must have that part.
if not exist "%NEW%\update_windows.bat" goto :bad_zip
findstr /b /l /c:":apply_mode" "%NEW%\update_windows.bat" >nul 2>&1
if errorlevel 1 goto :bad_zip

echo Copying the new version into the folder...
rem The new version's own update_windows.bat does the copying, so a folder added in a later
rem version arrives too. It runs as a separate file in the temp folder: neither this running
rem file nor the one in the install folder is read while they are replaced.
copy /y "%NEW%\update_windows.bat" "%UPD_APPLY%" >nul
if errorlevel 1 goto :copy_failed
call "%UPD_APPLY%" apply "%NEW%" "%INSTALL_DIR%"
if errorlevel 1 goto :copy_failed

echo.
echo Running setup_windows.bat for a quick update...
rem NOPAUSE for setup only: this window opens the app and pauses once at the end.
set "AIH_UPDATE=1"
set "KEEP_NOPAUSE=%NOPAUSE%"
set "NOPAUSE=1"
call "%INSTALL_DIR%\setup_windows.bat"
set "SETUP_RC=%errorlevel%"
set "NOPAUSE=%KEEP_NOPAUSE%"
set "AIH_UPDATE="
call :cleanup
if not "%SETUP_RC%"=="0" goto :setup_failed

echo.
echo ============================================================
echo  Update finished. The new version is in the same folder.
echo ============================================================
if defined NOPAUSE exit /b 0
echo  The AI helper window opens again now.
echo  In DaVinci Resolve press Workspace - Scripts - AI_Helper_Connect once more.
echo.
rem One line on purpose: a later update may replace this temp copy while this window waits here.
call "%INSTALL_DIR%\run_app.bat" & pause & exit /b 0

:apply_mode
rem Called as: update_windows.bat apply NEW_FOLDER INSTALL_DIR (see the top of this file).
rem Exit code 0 = copied, 1 = not (the calling window says so).
set "NEW=%~2"
set "INSTALL_DIR=%~3"
if not defined NEW exit /b 1
if not defined INSTALL_DIR exit /b 1
call :tidy_install_dir
if not exist "%NEW%\app\__main__.py" exit /b 1
if not exist "%INSTALL_DIR%\run_app.bat" exit /b 1
rem Code folders are mirrored (/MIR), so files removed in the new version disappear too
rem (an old test file would otherwise still run). .github holds the automatic checks the tests read.
rem Nothing else in the folder is deleted: tools\ffmpeg (FFmpeg you put there yourself) is kept
rem with /XD, and samples (your own videos) only gets the new files, nothing removed.
rem A new top-level folder must be added to this list (tests/test_updater.py checks it).
set "COPY_FAILED="
for %%F in (app engine resolve_scripts tests docs .github) do call :mirror %%F
call :mirror tools "/XD ffmpeg"
call :merge samples
if defined COPY_FAILED exit /b 1
rem Files at the top of the folder are copied without deleting anything (your own files stay).
robocopy "%NEW%" "%INSTALL_DIR%" /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 exit /b 1
exit /b 0

:mirror
rem The second argument is an optional robocopy option in quotes ("/XD name"): excluded folders are not deleted.
if not exist "%NEW%\%~1\" exit /b 0
echo   %~1
robocopy "%NEW%\%~1" "%INSTALL_DIR%\%~1" /MIR /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP %~2
if errorlevel 8 set "COPY_FAILED=1"
exit /b 0

:merge
rem Only the files at the top of this folder, and nothing is deleted.
if not exist "%NEW%\%~1\" exit /b 0
echo   %~1
robocopy "%NEW%\%~1" "%INSTALL_DIR%\%~1" /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 set "COPY_FAILED=1"
exit /b 0

:fetch
rem The branch name goes through an environment variable; PowerShell checks it and builds the address.
rem Exit code 4: GitHub has no such branch (404). Any other problem is 1.
set "UPD_BRANCH=%~1"
echo Downloading the "%~1" version from GitHub...
if exist "%UPD_ZIP%" del /f /q "%UPD_ZIP%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $b = $env:UPD_BRANCH.Trim([char]0xFEFF, [char]32); if ($b -notmatch '^[A-Za-z0-9._/-]+$') { throw ('bad branch name: ' + $b) }; $u = 'https://github.com/munss-coder-oms/video-editing-systems/archive/refs/heads/' + $b + '.zip'; try { Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $env:UPD_ZIP } catch { $r = $_.Exception.Response; if ($r -and [int]$r.StatusCode -eq 404) { exit 4 }; throw }"
if errorlevel 5 exit /b 1
if errorlevel 4 exit /b 4
if errorlevel 1 exit /b 1
if not exist "%UPD_ZIP%" exit /b 1
exit /b 0

:cleanup
if exist "%UPD_ZIP%" del /f /q "%UPD_ZIP%" >nul 2>&1
if exist "%UPD_EXTRACT%" rmdir /s /q "%UPD_EXTRACT%" >nul 2>&1
if exist "%UPD_APPLY%" del /f /q "%UPD_APPLY%" >nul 2>&1
exit /b 0

:tidy_install_dir
rem No trailing backslash: "C:\x\" would escape the closing quote for robocopy.
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
for %%I in ("%INSTALL_DIR%") do set "INSTALL_DIR=%%~fI"
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
exit /b 0

:usage
echo [ERROR] Usage: update_windows.bat [PID] [INSTALL_DIR]
echo         PID is the number of the running AI helper, or leave everything out.
echo         Nothing was changed.
goto :fail

:not_install
echo [ERROR] This is not the AI helper folder (run_app.bat or setup_windows.bat is missing).
echo         Nothing was changed.
goto :fail

:still_running
echo [ERROR] The AI helper window is still open after %WAIT_MAX% seconds.
echo         Close it and run update_windows.bat again. Nothing was changed.
goto :fail

:no_zip
echo [ERROR] Could not download the new version. Check the internet connection and try again.
echo         Nothing was changed.
goto :fail

:bad_zip
echo [ERROR] The downloaded file is not a complete version of this program.
echo         Nothing was changed.
goto :fail

:older_zip
echo [ERROR] The downloaded version is older than the one in this folder, so it was not put in.
echo         Nothing was changed.
goto :fail

:copy_failed
echo [ERROR] Copying the new version into the folder failed (see the messages above).
echo         Close programs that use files in this folder and run update_windows.bat again,
echo         or download the ZIP again and run setup_windows.bat in the new folder.
goto :fail

:setup_failed
echo [ERROR] The new files are in the folder, but setup_windows.bat reported a problem (see above).
echo         Run setup_windows.bat in the folder once more. If it still fails,
echo         run check_setup.bat and send the result file.
goto :fail

:self_copy_failed
echo [ERROR] Could not copy update_windows.bat to the temp folder:
echo         "%WORK%"
echo         Nothing was changed.
goto :fail

:fail
call :cleanup
echo.
if defined UPD_PID echo  Start the AI helper again with the "AI ..." icon on the desktop.
if defined NOPAUSE exit /b 1
rem One line on purpose: a later update may replace this temp copy while this window waits here.
pause & exit /b 1
