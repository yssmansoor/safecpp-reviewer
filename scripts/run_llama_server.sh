#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAMA_BIN="${PROJECT_ROOT}/deps/llama.cpp/build/bin/llama-server"
MODEL="${PROJECT_ROOT}/models/Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf"

if [[ ! -f "${LLAMA_BIN}" ]]; then
    echo "ERROR: llama-server binary not found at ${LLAMA_BIN}"
    exit 1
fi

if [[ ! -f "${MODEL}" ]]; then
    echo "ERROR: Model not found at ${MODEL}"
    exit 1
fi

exec "${LLAMA_BIN}" \
    -m "${MODEL}" \
    -ngl 99 \
    -c 8192 \
    --host 127.0.0.1 \
    --port 8080
