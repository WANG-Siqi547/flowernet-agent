#!/bin/bash
# Controller Ngrok 隧道
# 用法: ./ngrok-controller.sh

NGROK_TOKEN="38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR"

echo "🚀 启动 Controller 隧道 (端口 8001)..."
echo ""
echo "使用说明:"
echo "  - 查看公网 URL: http://localhost:4040"
echo "  - 按 Ctrl+C 停止隧道"
echo ""

# 尝试直接执行 ngrok (如果已安装)
if command -v ngrok &> /dev/null; then
    exec ngrok http 8001 --authtoken="$NGROK_TOKEN" --region=us
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
