#!/bin/bash

# FlowerNet 双端点 Ngrok 启动脚本
# 
# 功能：在主机上启动两个独立的 Ngrok 隧道
# - Controller 隧道（端口 8001）
# - Verifier 隧道（端口 8000）
#
# 使用方法：./start-ngrok-tunnels.sh

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

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FlowerNet 双端点 Ngrok 隧道启动${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""

# 检查 ngrok 是否安装
if [ -z "$NGROK_BIN" ]; then
    echo "❌ ngrok 未安装"
    echo ""
    echo "安装方法 (macOS):"
    echo "  brew install ngrok"
    echo ""
    echo "或访问: https://ngrok.com/download"
    exit 1
fi

echo -e "${GREEN}✓${NC} ngrok 已安装: $($NGROK_BIN version)"
echo ""

# 启动 Controller 隧道
echo "启动 Controller 隧道 (本地端口 8001)..."
if [ -n "$NGROK_TOKEN" ]; then
    "$NGROK_BIN" config add-authtoken "$NGROK_TOKEN" > /dev/null 2>&1 || true
fi

"$NGROK_BIN" http 8001 \
    --region=us \
    --log=stdout \
    --log-format=json \
    2>&1 | grep -oP '"url":"https://[^"]+' | cut -d'"' -f4 &

CONTROLLER_PID=$!

echo "启动 Verifier 隧道 (本地端口 8000)..."
sleep 2

"$NGROK_BIN" http 8000 \
    --region=us \
    --log=stdout \
    --log-format=json \
    2>&1 | grep -oP '"url":"https://[^"]+' | cut -d'"' -f4 &

VERIFIER_PID=$!

sleep 3

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}公网端点信息${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""

# 获取 Ngrok URLs
echo "正在获取隧道地址..."
sleep 2

# 通过 Ngrok API 获取 URLs
NGROK_URLS=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null || curl -s http://localhost:4041/api/tunnels 2>/dev/null)

echo ""
echo -e "${GREEN}Controller 端点:${NC}"
echo "  http://localhost:8001 (本地)"
echo "  API: /process"
echo ""
echo -e "${GREEN}Verifier 端点:${NC}"
echo "  http://localhost:8000 (本地)"
echo "  API: /verify"
echo ""

echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""
echo "提示: 按 Ctrl+C 停止隧道"
echo ""

# 等待所有后台进程
wait
