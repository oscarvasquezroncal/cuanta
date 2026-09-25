@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0..\.." || exit /b 1
set "cuanta_name="
set "cuanta_email="
for /f "delims=" %%I in ('git config --get user.name') do set "cuanta_name=%%I"
for /f "delims=" %%I in ('git config --get user.email') do set "cuanta_email=%%I"
if not defined cuanta_name goto missing_identity
if not defined cuanta_email goto missing_identity
if not exist ".githooks\commit-msg" goto missing_hook
git config --local core.hooksPath .githooks
set "cuanta_exit=%errorlevel%"
if "%cuanta_exit%"=="0" echo Git hooks enabled in .githooks.
popd
exit /b %cuanta_exit%
:missing_identity
echo Set git user.name and user.email, then run scripts\git\setup.cmd again. 1>&2
popd
exit /b 1
:missing_hook
echo Missing .githooks\commit-msg. Restore the hook before setup. 1>&2
popd
exit /b 1
