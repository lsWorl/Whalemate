@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
if not exist output\voice-check mkdir output\voice-check
del /q output\voice-check\done.txt 2>nul
echo [1/2] unittest ...
.venv\Scripts\python.exe -m unittest discover -s tests > output\voice-check\unittest.log 2>&1
echo exit=%ERRORLEVEL% >> output\voice-check\unittest.log
echo [2/2] 打断实测（回声门控，约 1 分钟）：戴好蓝牙耳机，先安静 25 秒，之后听到"现在说"就开口。
pause
.venv\Scripts\python.exe tools\measure_voice.py --backends qt 2> output\voice-check\measure.err
echo done > output\voice-check\done.txt
echo 完成，可以关闭此窗口。
pause
