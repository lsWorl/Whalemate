@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
set OUT=output\v2-check
set CHECKS_EXIT=0
if not exist %OUT% mkdir %OUT%
del /q %OUT%\done.txt 2>nul
echo [1/2] unittest ...
.venv\Scripts\python.exe -m unittest discover -s tests > %OUT%\unittest.log 2>&1
set TEST_EXIT=%ERRORLEVEL%
echo exit=%TEST_EXIT% >> %OUT%\unittest.log
if not "%TEST_EXIT%"=="0" set CHECKS_EXIT=1
echo [2/2] desktop pet smoke（这一步请不要碰角色）...
.venv\Scripts\python.exe -X faulthandler tools\smoke_pet.py --output-dir %OUT%\pet > %OUT%\smoke.log 2>&1
set SMOKE_EXIT=%ERRORLEVEL%
echo exit=%SMOKE_EXIT% >> %OUT%\smoke.log
if not "%SMOKE_EXIT%"=="0" set CHECKS_EXIT=1
echo done > %OUT%\done.txt
echo 检查完成，退出码 %CHECKS_EXIT%。
exit /b %CHECKS_EXIT%
