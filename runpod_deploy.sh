#!/usr/bin/env bash
# ============================================================
# runpod_deploy.sh — run INSIDE a RunPod pod (1x H100 80GB)
# Installs vLLM, downloads Llama 4 Scout, starts the server.
#
# IMPORTANT REALITY CHECKS (read before running):
#
# 1) 1.78-bit quants: the well-known "1.78-bit" Scout builds are
#    Unsloth *GGUF* dynamic quants. vLLM does not serve those
#    GGUF MoE quants reliably. On an H100 the practical options are:
#      a) FP8 (recommended, H100-native):  meta-llama/Llama-4-Scout-17B-16E-Instruct-FP8
#      b) BF16 full weights (~2x memory of FP8)
#    If you specifically want the Unsloth 1.78-bit GGUF, serve it
#    with llama.cpp's llama-server instead (also OpenAI-compatible,
#    same client code works). This script defaults to FP8 on vLLM.
#
# 2) --max-model-len 262144: a 262k context KV cache on ONE H100
#    will very likely OOM alongside Scout's weights. 32k-64k is the
#    realistic single-H100 envelope for this pipeline (article chunks
#    are ~8k tokens anyway). The spec'd value is kept below as a
#    variable so you can try it, but the default is 65536.
# ============================================================
set -euo pipefail

# ---- knobs -------------------------------------------------
MODEL_ID="${MODEL_ID:-meta-llama/Llama-4-Scout-17B-16E-Instruct-FP8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-65536}"      # spec asked for 262144; see note 2
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
PORT="${PORT:-8000}"
API_KEY="${VLLM_API_KEY:-change-me}"         # your client must send this key
HF_TOKEN="${HF_TOKEN:?Set HF_TOKEN (Llama 4 is a gated repo)}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-/workspace/models}"

echo "[1/4] System deps"
apt-get update -y && apt-get install -y --no-install-recommends git tmux htop

echo "[2/4] Python deps"
pip install -U pip
pip install -U "vllm>=0.8.4" "huggingface_hub[cli]"

echo "[3/4] Downloading model: ${MODEL_ID}"
huggingface-cli login --token "${HF_TOKEN}" --add-to-git-credential
huggingface-cli download "${MODEL_ID}" --local-dir "${DOWNLOAD_DIR}/scout" \
  --exclude "original/*"

echo "[4/4] Starting vLLM OpenAI-compatible server on :${PORT}"
# Runs in tmux so it survives your SSH session dropping.
tmux new-session -d -s vllm "
python -m vllm.entrypoints.openai.api_server \
  --model '${DOWNLOAD_DIR}/scout' \
  --served-model-name '${MODEL_ID}' \
  --tensor-parallel-size 1 \
  --max-model-len ${MAX_MODEL_LEN} \
  --max-num-seqs ${MAX_NUM_SEQS} \
  --disable-log-requests \
  --api-key '${API_KEY}' \
  --port ${PORT} 2>&1 | tee /workspace/vllm.log
"

echo
echo "vLLM starting in tmux session 'vllm'.  Tail logs:  tmux attach -t vllm"
echo "Endpoint (via RunPod proxy):  https://<pod-id>-${PORT}.proxy.runpod.net/v1"
echo "Set in your local .env:"
echo "  RUNPOD_BASE_URL=https://<pod-id>-${PORT}.proxy.runpod.net/v1"
echo "  RUNPOD_API_KEY=${API_KEY}"
echo "  RUNPOD_MODEL=${MODEL_ID}"
