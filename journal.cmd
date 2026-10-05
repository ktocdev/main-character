@echo off
rem SPDX-License-Identifier: AGPL-3.0-or-later
rem
rem Friendly front door for Windows. From the repo root:
rem
rem   journal install         create .venv, install the lock file, make .env
rem   journal start           run the journal on http://127.0.0.1:8144
rem   journal restart         restart the running journal, as Settings' button does
rem   journal sandbox-reset   stop the 8145 sandbox and relaunch it from a clean first run
rem   journal sandbox         relaunch the 8145 sandbox, keeping what the last run left
rem
rem In PowerShell, type .\journal -- PowerShell does not run commands from the
rem current directory by bare name.
setlocal
cd /d "%~dp0"
set "VENV_PY=.venv\Scripts\python.exe"
set "SANDBOX=.claude\skills\sandbox-reset"

if /i "%~1"=="install"       goto install
if /i "%~1"=="start"         goto start
if /i "%~1"=="restart"       goto restart
if /i "%~1"=="sandbox-reset" goto sandbox_reset
if /i "%~1"=="sandbox"       goto sandbox
goto usage

:install
if exist "%VENV_PY%" goto install_deps
where python >nul 2>nul || (echo python not found -- install Python 3.12 or newer first & exit /b 1)
python -c "import sys; sys.exit(sys.version_info < (3, 12))" || (echo Python 3.12 or newer is required & exit /b 1)
echo creating .venv
python -m venv .venv || exit /b 1
:install_deps
echo installing dependencies -- large and slow the first time
"%VENV_PY%" -m pip install -r requirements.lock || exit /b 1
if not exist .env (
  copy /y .env.example .env >nul
  echo created .env -- uncomment ANTHROPIC_API_KEY in it and paste your key
)
echo.
echo installed. Run: .\journal start
exit /b 0

:start
if not exist "%VENV_PY%" (echo not installed yet -- run: .\journal install & exit /b 1)
"%VENV_PY%" server.py
exit /b %errorlevel%

:restart
if not exist "%VENV_PY%" (echo not installed yet -- run: .\journal install & exit /b 1)
"%VENV_PY%" scripts\restart_journal.py
exit /b %errorlevel%

:sandbox_reset
powershell -NoProfile -File "%SANDBOX%\stop-sandbox.ps1" || exit /b 1
powershell -NoProfile -File "%SANDBOX%\run-sandbox.ps1"
exit /b %errorlevel%

:sandbox
powershell -NoProfile -File "%SANDBOX%\run-sandbox.ps1" -Keep
exit /b %errorlevel%

:usage
echo usage: journal ^<command^>
echo.
echo   install         create .venv, install dependencies, make .env
echo   start           run the journal on http://127.0.0.1:8144
echo   restart         restart the running journal, as Settings' button does
echo   sandbox-reset   stop the 8145 sandbox and relaunch it from a clean first run
echo   sandbox         relaunch the 8145 sandbox, keeping the last run's state
exit /b 1
