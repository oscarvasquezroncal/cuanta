@echo off
setlocal DisableDelayedExpansion
uv run --no-sync python "%~dp0trial.py" %*
exit /b %errorlevel%
