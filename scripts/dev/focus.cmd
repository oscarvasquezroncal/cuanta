@echo off
setlocal DisableDelayedExpansion
uv run --no-sync python "%~dp0focus.py" %*
exit /b %errorlevel%
