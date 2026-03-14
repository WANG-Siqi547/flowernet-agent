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
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  警告: Ollama 可能未在运行 (端口 11434)"
    echo "请先启动 Ollama: ollama serve"
    read -p "继续? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

if [ -n "$NGROK_TOKEN" ]; then
    "$NGROK_BIN" config add-authtoken "$NGROK_TOKEN" > /dev/null 2>&1 || true
fi

# 启动隧道
echo ""
echo "📡 启动隧道..."
echo "----- Ngrok 隧道信息 -----"
echo ""
echo "按 Ctrl+C 停止隧道"
echo ""

"$NGROK_BIN" http 11434 \
    --region=us \
    --log=stdout \
    2>&1 | tee /tmp/ollama_ngrok.log
