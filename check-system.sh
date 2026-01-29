#!/bin/bash

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FlowerNet 双端点 - 完整系统检查脚本
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 计数器
CHECKS_PASSED=0
CHECKS_FAILED=0

check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
        ((CHECKS_PASSED++))
    else
        echo -e "${RED}✗${NC} $1"
        ((CHECKS_FAILED++))
    fi
}

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}FlowerNet 双端点系统检查${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

echo -e "${YELLOW}📋 系统环境检查${NC}"
echo "─────────────────────────────────────────────"

# 检查 Docker
docker --version > /dev/null 2>&1
check_status "Docker 已安装"

# 检查 Docker Compose
docker-compose --version > /dev/null 2>&1
check_status "Docker Compose 已安装"

# 检查 Ngrok
ngrok --version > /dev/null 2>&1
check_status "Ngrok 已安装"

echo ""
echo -e "${YELLOW}🐳 Docker 服务检查${NC}"
echo "─────────────────────────────────────────────"

# 检查 Docker 守护进程
docker ps > /dev/null 2>&1
check_status "Docker 守护进程运行"

# 检查 Verifier 容器
docker ps | grep -q "flower-verifier"
check_status "Verifier 容器运行"

# 检查 Controller 容器
docker ps | grep -q "flower-controller"
check_status "Controller 容器运行"

# 检查 Verifier 健康状态
VERIFIER_STATUS=$(docker inspect flower-verifier 2>/dev/null | grep -q '"Status": "running"' && echo "healthy")
if [ "$VERIFIER_STATUS" = "healthy" ]; then
    echo -e "${GREEN}✓${NC} Verifier 容器健康"
    ((CHECKS_PASSED++))
else
    echo -e "${RED}✗${NC} Verifier 容器不健康"
    ((CHECKS_FAILED++))
fi

echo ""
echo -e "${YELLOW}🌐 网络端口检查${NC}"
echo "─────────────────────────────────────────────"

# 检查端口 8000
nc -z 127.0.0.1 8000 2>/dev/null
check_status "Verifier 端口 8000 开放"

# 检查端口 8001
nc -z 127.0.0.1 8001 2>/dev/null
check_status "Controller 端口 8001 开放"

# 检查端口 4040 (Ngrok Web UI)
nc -z 127.0.0.1 4040 2>/dev/null && {
    echo -e "${GREEN}✓${NC} Ngrok 监控端口 4040 (Ngrok 隧道已运行)"
    ((CHECKS_PASSED++))
} || {
    echo -e "${YELLOW}⚠${NC} Ngrok 监控端口 4040 (Ngrok 隧道未启动)"
}

echo ""
echo -e "${YELLOW}📁 脚本文件检查${NC}"
echo "─────────────────────────────────────────────"

# 检查必要的脚本文件
for script in ngrok-controller.sh ngrok-verifier.sh start-dual-endpoints.sh; do
    if [ -f "$script" ]; then
        echo -e "${GREEN}✓${NC} $script 存在"
        ((CHECKS_PASSED++))
    else
        echo -e "${RED}✗${NC} $script 不存在"
        ((CHECKS_FAILED++))
    fi
done

echo ""
echo -e "${YELLOW}📚 文档文件检查${NC}"
echo "─────────────────────────────────────────────"

# 检查文档文件
for doc in README_DUAL_ENDPOINTS.md QUICK_START_DUAL_ENDPOINTS.md ARCHITECTURE_DUAL_ENDPOINTS.md DUAL_ENDPOINTS_GUIDE.md; do
    if [ -f "$doc" ]; then
        echo -e "${GREEN}✓${NC} $doc 存在"
        ((CHECKS_PASSED++))
    else
        echo -e "${RED}✗${NC} $doc 不存在"
        ((CHECKS_FAILED++))
    fi
done

echo ""
echo -e "${YELLOW}🔗 API 端点检查${NC}"
echo "─────────────────────────────────────────────"

# 检查 Verifier API
curl -s http://localhost:8000/ > /dev/null 2>&1
check_status "Verifier API 响应 (http://localhost:8000)"

# 检查 Controller API
curl -s http://localhost:8001/ > /dev/null 2>&1
check_status "Controller API 响应 (http://localhost:8001)"

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}检查结果总结${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

echo -e "✅ 通过: ${GREEN}$CHECKS_PASSED${NC}"
echo -e "❌ 失败: ${RED}$CHECKS_FAILED${NC}"

TOTAL=$((CHECKS_PASSED + CHECKS_FAILED))
PERCENTAGE=$((CHECKS_PASSED * 100 / TOTAL))

echo ""
echo -e "进度: $CHECKS_PASSED/$TOTAL ($PERCENTAGE%)"
echo ""

if [ $CHECKS_FAILED -eq 0 ]; then
    echo -e "${GREEN}🎉 系统完全就绪！${NC}"
    echo ""
    echo "下一步:"
    echo "  1️⃣  在终端 1 启动 Controller 隧道:"
    echo "     ${BLUE}./ngrok-controller.sh${NC}"
    echo ""
    echo "  2️⃣  在终端 2 启动 Verifier 隧道:"
    echo "     ${BLUE}./ngrok-verifier.sh${NC}"
    echo ""
    echo "  3️⃣  记录公网 URL 并在应用中使用"
    echo ""
    echo "📖 详见: README_DUAL_ENDPOINTS.md"
    exit 0
else
    echo -e "${YELLOW}⚠️  需要修复以下问题:${NC}"
    echo ""
    
    if ! command -v docker &> /dev/null; then
        echo "1. 安装 Docker: https://www.docker.com/products/docker-desktop"
    fi
    
    if ! command -v ngrok &> /dev/null; then
        echo "2. 安装 Ngrok:"
        echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "   brew install ngrok"
    fi
    
    if ! docker ps &> /dev/null; then
        echo "3. 启动 Docker Desktop"
    fi
    
    if ! docker ps | grep -q "flower-verifier"; then
        echo "4. 启动 Docker 服务:"
        echo "   docker-compose up -d"
    fi
    
    echo ""
    echo "修复后再运行此脚本"
    exit 1
fi
