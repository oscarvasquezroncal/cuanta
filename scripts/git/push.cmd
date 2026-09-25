@echo off
setlocal DisableDelayedExpansion
uv run --no-sync python "%~dp0push.py" %*
exit /b %errorlevel%
