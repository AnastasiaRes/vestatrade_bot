#!/usr/bin/env bash
# Run the normal widget with local Ollama and V2-first ownership.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export LLM_PROVIDER=ollama
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3-vl:8b-instruct}"
export OLLAMA_MODEL_STRONG="${OLLAMA_MODEL_STRONG:-$OLLAMA_MODEL}"
export OLLAMA_EMBEDDING_MODEL="${OLLAMA_EMBEDDING_MODEL:-bge-m3}"

if [[ "${V2_OLLAMA_SKIP_PREPARE:-0}" != "1" ]]; then
  "$project_dir/scripts/prepare_ollama_v2.sh"
fi

exec "$project_dir/scripts/start_v2_primary.sh"
