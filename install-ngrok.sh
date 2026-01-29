#!/bin/bash

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Ngrok 安装脚本 (macOS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Ngrok 安装脚本${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# 检查是否已安装
if command -v ngrok &> /dev/null; then
    echo -e "${GREEN}✓ Ngrok 已安装: $(ngrok --version)${NC}"
    exit 0
fi

echo "未检测到 Ngrok，现在安装..."
echo ""

# 方式 1: 尝试 Homebrew
if command -v brew &> /dev/null; then
    echo "使用 Homebrew 安装..."
    brew install ngrok
    
    if command -v ngrok &> /dev/null; then
        echo -e "${GREEN}✓ Ngrok 安装成功: $(ngrok --version)${NC}"
        exit 0
    fi
fi

# 方式 2: 手动下载
echo "从官网下载 Ngrok..."

ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-arm64.zip"
elif [ "$ARCH" = "x86_64" ]; then
    URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-amd64.zip"
else
    echo -e "${RED}不支持的架构: $ARCH${NC}"
    exit 1
fi

# 下载
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

echo "下载中 (${ARCH})..."
curl -L -o ngrok.zip "$URL" 2>/dev/null || {
    echo -e "${RED}✗ 下载失败，请检查网络连接${NC}"
    rm -rf "$TEMP_DIR"
    exit 1
}

# 解压
unzip -q ngrok.zip

# 安装到系统路径
if [ -w /usr/local/bin ]; then
    sudo mv ngrok /usr/local/bin/
else
    # 如果无法写入 /usr/local/bin，使用 ~/.local/bin
    mkdir -p ~/.local/bin
    mv ngrok ~/.local/bin/
    
    # 确保 ~/.local/bin 在 PATH 中
    if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
        echo ""
        echo "⚠️  需要将 ~/.local/bin 添加到 PATH"
        echo ""
        echo "在你的 ~/.zshrc 中添加:"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        echo "然后运行:"
        echo "  source ~/.zshrc"
    fi
fi

# 清理
cd - > /dev/null
rm -rf "$TEMP_DIR"

# 验证安装
if command -v ngrok &> /dev/null; then
    echo -e "${GREEN}✓ Ngrok 安装成功: $(ngrok --version)${NC}"
    exit 0
else
    echo -e "${RED}✗ Ngrok 安装失败${NC}"
    exit 1
fi
