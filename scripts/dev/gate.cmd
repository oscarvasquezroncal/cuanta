@echo off
setlocal DisableDelayedExpansion
uv run --no-sync python "%~dp0gate.py" %*
exit /b %errorlevel%
