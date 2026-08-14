#!/bin/bash
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
if [ ! -f .venv/bin/uvicorn ]; then
  python3 -m venv .venv
  .venv/bin/pip install --quiet fastapi "uvicorn[standard]" PyMuPDF python-pptx openai python-multipart python-dotenv
fi
if [ -z "$LLM_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ] && [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "WARNING: no API key set (OPENROUTER_API_KEY recommended). Summaries/captions/question generation will fail until you set one."
fi
exec .venv/bin/uvicorn app.main:app --reload --port 8000
