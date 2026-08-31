#!/usr/bin/env bash
# Start the local, protected V2 widget preview without enabling public V2 traffic.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python_bin="${PYTHON_BIN:-$project_dir/.venv/bin/python}"
port="${PORT:-8010}"

if [[ ! -x "$python_bin" ]]; then
  echo "Python environment is missing. Create it first: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Configure OPENROUTER_API_KEY (or local Ollama), then run this command again."
  exit 1
fi

# These exports override .env only for this local process.  The preview page is
# loopback-only and injects its token server-side; neither QA controls nor the
# token are exposed through the ordinary widget or public /chat requests.
export DIALOGUE_V2_ROUTING_ENABLED=true
export DIALOGUE_V2_LOCAL_PREVIEW_ENABLED=true
export DIALOGUE_V2_QA_CONTROLS_ENABLED=true
export DIALOGUE_V2_QA_CONTROL_TOKEN="${DIALOGUE_V2_QA_CONTROL_TOKEN:-local-v2-preview}"
export DIALOGUE_V2_LIVE_DELIVERY_ENABLED=false
export DIALOGUE_V2_INTERNAL_CANARY_ENABLED=false
export DIALOGUE_V2_INTERNAL_CANARY_PERCENT=0
export DIALOGUE_V2_FORCE_LEGACY=false
export COMMERCE_EXTERNAL_EXECUTION_ENABLED=false

echo "V2 Preview: http://127.0.0.1:${port}/widget-v2-preview"
echo "Public /chat stays Legacy; public V2 canary stays 0%."

uvicorn_args=(app.main:app --host 127.0.0.1 --port "$port")
if [[ "${V2_PREVIEW_RELOAD:-0}" == "1" ]]; then
  uvicorn_args+=(--reload)
fi
exec "$python_bin" -m uvicorn "${uvicorn_args[@]}"
