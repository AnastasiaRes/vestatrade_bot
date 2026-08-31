#!/usr/bin/env bash
# Run the ordinary widget with V2-first ownership and Legacy fallback.
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

# These values make ordinary /chat requests V2-first for this process only.
# V2 still has to pass semantic, contract, source and outcome gates; failure
# returns the turn to Legacy. The kill switch remains available in .env.
export DIALOGUE_V2_ROUTING_ENABLED=true
export DIALOGUE_V2_LIVE_DELIVERY_ENABLED=true
export DIALOGUE_V2_PUBLIC_PRIMARY_ENABLED=true
export DIALOGUE_V2_INTERNAL_CANARY_ENABLED=false
export DIALOGUE_V2_INTERNAL_CANARY_PERCENT=0
export DIALOGUE_V2_LOCAL_PREVIEW_ENABLED=false
export DIALOGUE_V2_QA_CONTROLS_ENABLED=false
export DIALOGUE_V2_FORCE_LEGACY=false
export COMMERCE_EXTERNAL_EXECUTION_ENABLED=false

echo "V2-first widget: http://127.0.0.1:${port}/widget-demo"
echo "Rollback: restart with DIALOGUE_V2_FORCE_LEGACY=true."

uvicorn_args=(app.main:app --host 127.0.0.1 --port "$port")
if [[ "${V2_PRIMARY_RELOAD:-0}" == "1" ]]; then
  uvicorn_args+=(--reload)
fi
exec "$python_bin" -m uvicorn "${uvicorn_args[@]}"
