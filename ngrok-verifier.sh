#!/bin/bash
# Verifier Ngrok 隧道
# 用法: ./ngrok-verifier.sh

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

echo "🚀 启动 Verifier 隧道 (端口 8000)..."
echo ""
echo "使用说明:"
echo "  - 查看公网 URL: http://localhost:4040"
echo "  - 按 Ctrl+C 停止隧道"
echo ""

# 尝试直接执行 ngrok (如果已安装)
if [ -n "$NGROK_BIN" ]; then
    if [ -n "$NGROK_TOKEN" ]; then
        "$NGROK_BIN" config add-authtoken "$NGROK_TOKEN" > /dev/null 2>&1 || true
    fi
    exec "$NGROK_BIN" http 8000 --region=us
else
    echo "❌ ngrok 未找到"
    echo ""
    echo "安装方法 1 - 从 Homebrew (推荐):"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo "  brew install ngrok"
    echo ""
    echo "安装方法 2 - 手动下载:"
    echo "  https://ngrok.com/download"
    echo ""
    exit 1
fi
