#!/usr/bin/env bash
# Install/check the pinned local models and build the one existing passport
# vector cache before a V2-first local launch. No secrets are read or printed.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python_bin="${PYTHON_BIN:-$project_dir/.venv/bin/python}"
ollama_bin="${OLLAMA_BIN:-ollama}"
chat_model="${OLLAMA_MODEL:-qwen3-vl:8b-instruct}"
embedding_model="${OLLAMA_EMBEDDING_MODEL:-bge-m3}"
ollama_url="${OLLAMA_BASE_URL:-http://localhost:11434}"

if [[ ! -x "$python_bin" ]]; then
  echo "Python environment is missing. Create it first: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if ! command -v "$ollama_bin" >/dev/null 2>&1; then
  echo "Ollama CLI is not installed or is not on PATH. Install Ollama, start it, then run this script again."
  exit 1
fi

# `show` verifies both the local daemon and exact model availability. Missing
# models are downloaded only for this named local Ollama installation.
for model in "$chat_model" "$embedding_model"; do
  if ! "$ollama_bin" show "$model" >/dev/null 2>&1; then
    echo "Downloading required Ollama model: $model"
    "$ollama_bin" pull "$model"
  else
    echo "Ollama model is ready: $model"
  fi
done

export LLM_PROVIDER=ollama
export OLLAMA_BASE_URL="$ollama_url"
export OLLAMA_MODEL="$chat_model"
export OLLAMA_MODEL_STRONG="${OLLAMA_MODEL_STRONG:-$chat_model}"
export OLLAMA_EMBEDDING_MODEL="$embedding_model"

"$python_bin" scripts/check_llm.py
"$python_bin" scripts/prepare_passport_index.py

echo "Ollama V2 preparation completed. Start the ordinary V2-first widget with: ./scripts/start_v2_ollama.sh"
