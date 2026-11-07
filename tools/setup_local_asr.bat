@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
rem 本地语音识别：安装 sherpa-onnx，下载 SenseVoice 多语种模型（约 160 MB）到 %LOCALAPPDATA%\AICompanion\models\sense-voice
set PYTHONPATH=src
echo [1/2] 安装 sherpa-onnx 与 numpy ...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check "sherpa-onnx>=1.12,<3" numpy
if errorlevel 1 (echo 安装失败，请检查网络后重试 & exit /b 1)
set DEST=%LOCALAPPDATA%\AICompanion\models\sense-voice
.venv\Scripts\python.exe -c "import sys; from aicompanion.voice import local_asr; sys.exit(0 if local_asr.find_model() else 1)"
if not errorlevel 1 (echo [2/2] 模型已存在，跳过下载 & exit /b 0)
echo [2/2] 下载 SenseVoice 模型 ...
if not exist "%DEST%" mkdir "%DEST%"
curl.exe -L --fail --retry 3 -o "%DEST%\sense-voice.tar.bz2" https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
if errorlevel 1 (echo 下载失败，请检查网络（需要能访问 github.com）& exit /b 1)
tar -xjf "%DEST%\sense-voice.tar.bz2" -C "%DEST%"
if errorlevel 1 (echo 解压失败 & exit /b 1)
del "%DEST%\sense-voice.tar.bz2"
.venv\Scripts\python.exe -c "from aicompanion.voice import local_asr; print('模型位置：', local_asr.find_model()[0])"
exit /b 0
