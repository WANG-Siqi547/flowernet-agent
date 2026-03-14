#!/bin/bash

# 为 Ollama 启动 Ngrok 隧道
# 用法: ./start-ollama-ngrok.sh

resolve_ngrok() {
    for candidate in /usr/local/bin/ngrok /opt/homebrew/bin/ngrok "$(command -v ngrok 2>/dev/null)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" version >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

NGROK_BIN="$(resolve_ngrok)"
NGROK_TOKEN="${NGROK_AUTHTOKEN:-${NGROK_TOKEN:-}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OLLAMA_SOURCE_HOST="${OLLAMA_SOURCE_HOST:-127.0.0.1}"
OLLAMA_SOURCE_PORT="${OLLAMA_SOURCE_PORT:-11434}"
OLLAMA_BRIDGE_HOST="${OLLAMA_BRIDGE_HOST:-::1}"
OLLAMA_BRIDGE_PORT="${OLLAMA_BRIDGE_PORT:-11435}"
NGROK_TARGET="${OLLAMA_NGROK_ADDR:-localhost:${OLLAMA_BRIDGE_PORT}}"

echo "🚀 启动 Ollama Ngrok 隧道 (端口 11434)..."
echo ""

# 检查 ngrok 是否安装
if [ -z "$NGROK_BIN" ]; then
    echo "❌ ngrok 未安装"
    echo ""
    echo "请安装 ngrok:"
    echo "  brew install ngrok"
    echo ""
    exit 1
fi

echo "✅ 使用 ngrok: $NGROK_BIN"

# 检查 Ollama 是否在运行
echo "检查 Ollama 服务..."
if ! curl -s "http://${OLLAMA_SOURCE_HOST}:${OLLAMA_SOURCE_PORT}/api/tags" > /dev/null 2>&1; then
    echo "⚠️  警告: Ollama 可能未在运行 (端口 11434)"
    echo "请先启动 Ollama: ollama serve"
    read -p "继续? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

if ! lsof -iTCP:"${OLLAMA_BRIDGE_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "🔁 启动 Ollama 本地桥接: [${OLLAMA_BRIDGE_HOST}]:${OLLAMA_BRIDGE_PORT} -> ${OLLAMA_SOURCE_HOST}:${OLLAMA_SOURCE_PORT}"
    "$PYTHON_BIN" "$SCRIPT_DIR/ollama_bridge.py" \
        --listen-host "$OLLAMA_BRIDGE_HOST" \
        --listen-port "$OLLAMA_BRIDGE_PORT" \
        --target-host "$OLLAMA_SOURCE_HOST" \
        --target-port "$OLLAMA_SOURCE_PORT" \
        >/tmp/flowernet_ollama_bridge.log 2>&1 &
    sleep 1
fi

if [ -n "$NGROK_TOKEN" ]; then
    "$NGROK_BIN" config add-authtoken "$NGROK_TOKEN" > /dev/null 2>&1 || true
fi

# 启动隧道
echo ""
echo "📡 启动隧道..."
echo "   目标地址: ${NGROK_TARGET}"
echo "----- Ngrok 隧道信息 -----"
echo ""
echo "按 Ctrl+C 停止隧道"
echo ""

"$NGROK_BIN" http "$NGROK_TARGET" \
    --region=us \
    --log=stdout \
    2>&1 | tee /tmp/ollama_ngrok.log
