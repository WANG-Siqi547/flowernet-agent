#!/usr/bin/env bash
set -euo pipefail

# Recommended credible LongWriter run path.
# Use this on a remote NVIDIA GPU machine, not on the local M4 Mac.
# Suggested hardware: A10G 24GB minimum for small 2x2 tests; L40S/A100 preferred.

MODEL="${MODEL:-THUDM/LongWriter-llama3.1-8b}"
PORT="${PORT:-8088}"

python3 -m pip install -U "vllm>=0.6.0" "transformers>=4.43.0" "huggingface_hub>=0.24.0"

python3 -m vllm.entrypoints.openai.api_server \
  --model "${MODEL}" \
  --served-model-name longwriter \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --port "${PORT}"
