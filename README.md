# AICompanion

Windows desktop AI companion prototype built with Python, PySide6, SQLite, and a pluggable LLM service.

## Current milestone

- PySide6 chat window
- Layered app structure
- SQLite schema for chat messages, memories, reminders, and emotion state
- Local mock LLM service for offline development
- OpenAI-compatible LLM API client
- Streaming reply display through a background QThread
- Basic proactive message scheduler

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py
```

By default, the app uses `LLM_PROVIDER=mock`, so it can run without an API key.

To use a real model API, copy `.env.example` to `.env` and set:

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key
LLM_MODEL=gpt-4o-mini
```

Many providers support OpenAI-compatible `/chat/completions` APIs. In that case,
replace `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` with that provider's values.

## LLM Diagnostics

Before opening the desktop UI, you can test the configured provider with:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics
```

To send a custom message:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics "你好，请简单介绍一下你自己"
```

To test a non-streaming response:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics --no-stream
```

## Project Layout

```text
src/aicompanion/
  app.py                  # QApplication bootstrap
  config.py               # environment/config loading
  controllers/            # app workflow orchestration
  domain/                 # character, emotion, prompt models
  llm/                    # LLM provider abstraction
  services/               # memory, reminders, proactive messages
  storage/                # SQLite access and schema
  ui/                     # PySide6 widgets
```

For a module-by-module explanation, see [docs/module_guide.md](docs/module_guide.md).
