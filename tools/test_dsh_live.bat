@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
if not exist output\dsh-live mkdir output\dsh-live
del /q output\dsh-live\done.txt 2>nul
echo 正在实测 DeepSeek Harness（五个场景，约几分钟，会产生少量词元用量）...
.venv\Scripts\python.exe tools\test_dsh_live.py > output\dsh-live\console.log 2>&1
echo exit=%ERRORLEVEL% >> output\dsh-live\console.log
echo done > output\dsh-live\done.txt
echo 完成，可以关闭此窗口。
