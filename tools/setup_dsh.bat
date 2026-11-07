@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
if not exist output\dsh-check mkdir output\dsh-check
del /q output\dsh-check\done.txt 2>nul
echo [1/2] 安装 DeepSeek Harness（deepseek-harness-sdk，自带运行时，不需要 Node）...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements-agent-lock.txt > output\dsh-check\setup.log 2>&1
echo exit=%ERRORLEVEL% >> output\dsh-check\setup.log
echo [2/2] 在临时文件夹里试跑一个小任务（会产生少量词元用量）...
.venv\Scripts\python.exe tools\check_dsh.py > output\dsh-check\check.log 2>&1
echo exit=%ERRORLEVEL% >> output\dsh-check\check.log
echo done > output\dsh-check\done.txt
echo 完成，可以关闭此窗口。
pause
