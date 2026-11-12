@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
set PYTHONIOENCODING=utf-8
if not exist output\dsh-check mkdir output\dsh-check
echo [1/2] 安装锁定的 DeepSeek Harness 与官方 MCP 依赖...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements-agent-lock.txt > output\dsh-check\setup.log 2>&1
set AIC_SETUP_EXIT=%ERRORLEVEL%
echo exit=%AIC_SETUP_EXIT% >> output\dsh-check\setup.log
if not "%AIC_SETUP_EXIT%"=="0" goto finish
echo [2/2] 检查依赖一致性...
.venv\Scripts\python.exe -m pip check > output\dsh-check\check.log 2>&1
set AIC_SETUP_EXIT=%ERRORLEVEL%
echo exit=%AIC_SETUP_EXIT% >> output\dsh-check\check.log
echo 安装不调用收费接口。真实验收请另运行 tools\run_agent_checks.bat（只读进程环境，不加载 .env）。
:finish
echo done > output\dsh-check\done.txt
echo 结束，exit=%AIC_SETUP_EXIT%。
exit /b %AIC_SETUP_EXIT%
