#!/bin/bash

# FlowerNet 双端点快速设置脚本

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FlowerNet 双端点快速设置${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""

# 检查 Docker
echo "检查 Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Docker 已安装: $(docker --version)"

# 检查 Docker Compose
echo "检查 Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}❌ Docker Compose 未安装${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Docker Compose 已安装"

# 检查 Ngrok
echo "检查 Ngrok..."
if ! command -v ngrok &> /dev/null; then
    echo -e "${RED}❌ Ngrok 未安装${NC}"
    echo ""
    echo "安装方法 (macOS):"
    echo "  brew install ngrok"
    echo ""
    exit 1
fi
echo -e "${GREEN}✓${NC} Ngrok 已安装: $(ngrok --version)"

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}启动 Docker 服务${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""

# 停止旧容器
echo "清理旧容器..."
docker-compose down --remove-orphans 2>/dev/null || true

# 启动新容器
echo "启动 Verifier 和 Controller..."
docker-compose up -d

# 等待服务启动
echo "等待服务启动..."
sleep 10

# 检查服务状态
echo ""
echo "检查服务状态..."
docker-compose ps

echo ""
echo -e "${GREEN}✓ Docker 服务已启动${NC}"

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}后续步骤：启动 Ngrok 隧道${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}"
echo ""

echo "现在需要在两个终端中分别启动 Ngrok 隧道："
echo ""
echo "终端 1 - Controller 隧道:"
echo "  ngrok authtoken 38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR"
echo "  ngrok http 8001 --region=us"
echo ""
echo "终端 2 - Verifier 隧道:"
echo "  ngrok authtoken 38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR"
echo "  ngrok http 8000 --region=us"
echo ""

echo -e "${BLUE}或者使用自动脚本：${NC}"
echo "  ./start-ngrok-tunnels.sh"
echo ""

echo -e "${BLUE}本地测试：${NC}"
echo "  curl http://localhost:8000/  # Verifier"
echo "  curl http://localhost:8001/  # Controller"
echo ""

echo -e "${GREEN}✓ 设置完成！${NC}"
echo ""
