@echo off
setlocal DisableDelayedExpansion
uv run --no-sync python "%~dp0commit.py" %*
exit /b %errorlevel%
