@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
set OUT=output\v2-check
if not exist %OUT% mkdir %OUT%
del /q %OUT%\done.txt 2>nul
echo [1/2] unittest ...
.venv\Scripts\python.exe -m unittest discover -s tests > %OUT%\unittest.log 2>&1
echo exit=%ERRORLEVEL% >> %OUT%\unittest.log
echo [2/2] desktop pet smoke（这一步请不要碰角色）...
.venv\Scripts\python.exe tools\smoke_pet.py > %OUT%\smoke.log 2>&1
echo exit=%ERRORLEVEL% >> %OUT%\smoke.log
echo done > %OUT%\done.txt
echo 完成，可以关闭此窗口。
