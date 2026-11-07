@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
set OUT=output\v2-check
if not exist %OUT% mkdir %OUT%
if not exist output\dsh-live mkdir output\dsh-live
del /q %OUT%\done.txt output\dsh-live\done.txt 2>nul
echo [1/3] unittest ...
.venv\Scripts\python.exe -m unittest discover -s tests > %OUT%\unittest.log 2>&1
echo exit=%ERRORLEVEL% >> %OUT%\unittest.log
echo [2/3] desktop pet smoke（这一步请不要碰角色）...
.venv\Scripts\python.exe tools\smoke_pet.py > %OUT%\smoke.log 2>&1
echo exit=%ERRORLEVEL% >> %OUT%\smoke.log
echo done > %OUT%\done.txt
echo [3/3] 干活实测（真实模型，少量词元）...
.venv\Scripts\python.exe tools\test_dsh_live.py > output\dsh-live\console.log 2>&1
echo exit=%ERRORLEVEL% >> output\dsh-live\console.log
echo done > output\dsh-live\done.txt
echo 完成，可以关闭此窗口。
