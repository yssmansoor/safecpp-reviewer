#!/usr/bin/env bash
# Launch llama-server with a chosen Qwen2.5-Coder model.
#
# Usage:
#   ./scripts/run_llama_server.sh           # defaults to 7b
#   ./scripts/run_llama_server.sh 7b
#   ./scripts/run_llama_server.sh 14b
#   ./scripts/run_llama_server.sh /path/to/custom.gguf
#
# Override defaults via env vars:
#   PORT=8081 ./scripts/run_llama_server.sh 14b
#   GPU_LAYERS=40 ./scripts/run_llama_server.sh 14b
#   CTX=4096 ./scripts/run_llama_server.sh 7b

set -euo pipefail

MODEL_ARG="${1:-7b}"

case "$MODEL_ARG" in
    7b)
        MODEL_PATH="models/Qwen2.5-Coder-7B-Instruct-Q5_K_M.gguf"
        ;;
    14b)
        MODEL_PATH="models/qwen2.5-coder-14b-instruct-q5_k_m.gguf"
        ;;
    *.gguf)
        MODEL_PATH="$MODEL_ARG"
        ;;
    *)
        echo "Unknown model: $MODEL_ARG" >&2
        echo "Usage: $0 [7b|14b|/path/to/model.gguf]" >&2
        exit 2
        ;;
esac

if [ ! -f "$MODEL_PATH" ]; then
    echo "Model file not found: $MODEL_PATH" >&2
    echo "Download with:" >&2
    echo "  wget -P models/ https://huggingface.co/Qwen/Qwen2.5-Coder-${MODEL_ARG^^}-Instruct-GGUF/resolve/main/$(basename "$MODEL_PATH")" >&2
    exit 1
fi

PORT="${PORT:-8080}"
GPU_LAYERS="${GPU_LAYERS:-999}"
CTX="${CTX:-8192}"
PARALLEL="${PARALLEL:-1}"

LLAMA_BIN="deps/llama.cpp/build/bin/llama-server"
if [ ! -x "$LLAMA_BIN" ]; then
    echo "llama-server not found at $LLAMA_BIN" >&2
    exit 1
fi

echo "→ Loading $MODEL_PATH"
echo "  port=$PORT  gpu_layers=$GPU_LAYERS  ctx=$CTX  parallel=$PARALLEL"

exec "$LLAMA_BIN" \
    -m "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --n-gpu-layers "$GPU_LAYERS" \
    --ctx-size "$CTX" \
    --parallel "$PARALLEL"
