@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
set AIC_CHECK_OUT=output\v2-check
if not exist %AIC_CHECK_OUT% mkdir %AIC_CHECK_OUT%
if not exist output\dsh-live mkdir output\dsh-live
del /q %AIC_CHECK_OUT%\done.txt output\dsh-live\done.txt 2>nul
echo {"done":false,"passed":false,"reason":"offline-checks-pending"} > output\dsh-live\report.json
echo 等待离线检查完成。 > output\dsh-live\console.log
echo [1/3] unittest ...
.venv\Scripts\python.exe -m unittest discover -s tests > %AIC_CHECK_OUT%\unittest.log 2>&1
set AIC_CHECK_EXIT=%ERRORLEVEL%
echo exit=%AIC_CHECK_EXIT% >> %AIC_CHECK_OUT%\unittest.log
if not "%AIC_CHECK_EXIT%"=="0" goto finish
echo [2/3] desktop pet smoke（这一步请不要碰角色）...
.venv\Scripts\python.exe tools\smoke_pet.py > %AIC_CHECK_OUT%\smoke.log 2>&1
set AIC_CHECK_EXIT=%ERRORLEVEL%
echo exit=%AIC_CHECK_EXIT% >> %AIC_CHECK_OUT%\smoke.log
if not "%AIC_CHECK_EXIT%"=="0" goto finish
echo [3/3] 干活实测（真实模型，仅进程环境凭据；未提供时退出 2）...
.venv\Scripts\python.exe tools\test_dsh_live.py > output\dsh-live\console.log 2>&1
set AIC_CHECK_EXIT=%ERRORLEVEL%
echo exit=%AIC_CHECK_EXIT% >> output\dsh-live\console.log
:finish
echo done > %AIC_CHECK_OUT%\done.txt
echo done > output\dsh-live\done.txt
echo 检查结束，exit=%AIC_CHECK_EXIT%（0=通过，2=缺少真实测试前提而未执行）。
exit /b %AIC_CHECK_EXIT%
