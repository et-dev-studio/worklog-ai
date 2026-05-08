#!/usr/bin/env bash
# start-inference.sh — launch llama-server with the BitNet GGUF model so worklog-ai
# can talk to it over the OpenAI Chat Completions API. Run this in its own terminal,
# tmux pane, or as a systemd-user unit (see docs/inference-systemd.md).
#
# Tunables (env):
#   BITNET_DIR    Path to BitNet repo                 (default: ~/projects/BitNet)
#   BITNET_MODEL  Path to GGUF model                  (default: <BITNET_DIR>/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf)
#   BITNET_LLAMA  Path to llama-server binary         (default: <BITNET_DIR>/build/bin/llama-server)
#   INFERENCE_HOST  Listen host                       (default: 127.0.0.1)
#   INFERENCE_PORT  Listen port                       (default: 8080)
#   INFERENCE_CTX   Context window                    (default: 2048)
#   INFERENCE_THREADS CPU threads                     (default: 4)
#   INFERENCE_API_KEY  Bearer token to require        (default: unset = open on localhost)
set -euo pipefail

BITNET_DIR="${BITNET_DIR:-$HOME/projects/BitNet}"
BITNET_MODEL="${BITNET_MODEL:-$BITNET_DIR/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf}"
BITNET_LLAMA="${BITNET_LLAMA:-$BITNET_DIR/build/bin/llama-server}"
INFERENCE_HOST="${INFERENCE_HOST:-127.0.0.1}"
INFERENCE_PORT="${INFERENCE_PORT:-8080}"
INFERENCE_CTX="${INFERENCE_CTX:-2048}"
INFERENCE_THREADS="${INFERENCE_THREADS:-4}"

if [[ ! -x "$BITNET_LLAMA" ]]; then
    echo "start-inference: llama-server binary not found at $BITNET_LLAMA" >&2
    echo "Build BitNet first: cd $BITNET_DIR && python setup_env.py && cmake --build build" >&2
    exit 127
fi
if [[ ! -f "$BITNET_MODEL" ]]; then
    echo "start-inference: model not found at $BITNET_MODEL" >&2
    exit 127
fi

cd "$BITNET_DIR"

ARGS=(
    -m "$BITNET_MODEL"
    --host "$INFERENCE_HOST"
    --port "$INFERENCE_PORT"
    -c "$INFERENCE_CTX"
    -t "$INFERENCE_THREADS"
    -ngl 0
    -b 1
)
if [[ -n "${INFERENCE_API_KEY:-}" ]]; then
    ARGS+=(--api-key "$INFERENCE_API_KEY")
fi

echo "start-inference: launching $BITNET_LLAMA on http://$INFERENCE_HOST:$INFERENCE_PORT"
echo "                 model=$BITNET_MODEL"
exec "$BITNET_LLAMA" "${ARGS[@]}"
