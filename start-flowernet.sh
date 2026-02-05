#!/bin/bash

# FlowerNet 完整系统启动脚本
# 同时启动 Generator、Verifier 和 Controller 三个服务

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    🌸 FlowerNet 系统启动脚本 🌸       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 未找到${NC}"
    exit 1
fi

# 检查必要的环境变量
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${YELLOW}⚠️  警告：ANTHROPIC_API_KEY 环境变量未设置${NC}"
    echo -e "${YELLOW}   请设置: export ANTHROPIC_API_KEY='your-key'${NC}"
    read -p "继续吗? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 创建日志目录
mkdir -p logs

# 清理旧的日志
rm -f logs/*.log

echo -e "\n${GREEN}📦 检查依赖...${NC}"

# 检查并安装依赖
for service in "flowernet-generator" "flowernet-verifier" "flowernet-controler"; do
    if [ -f "$service/requirements.txt" ]; then
        echo -e "${BLUE}→ 检查 $service 依赖${NC}"
        pip install -q -r "$service/requirements.txt" 2>/dev/null || true
    fi
done

echo -e "${GREEN}✅ 依赖检查完成${NC}"

# 启动函数
start_service() {
    local name=$1
    local port=$2
    local service_path=$3
    
    echo -e "\n${BLUE}🚀 启动 $name (端口 $port)...${NC}"
    
    cd "$SCRIPT_DIR/$service_path"
    python3 main.py "$port" > "../logs/${name}.log" 2>&1 &
    local pid=$!
    
    echo $pid > "../logs/${name}.pid"
    echo -e "${GREEN}✅ $name 已启动 (PID: $pid)${NC}"
    
    # 等待服务启动
    sleep 3
    
    # 检查服务是否在线
    if curl -s "http://localhost:$port/" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ $name 已就绪${NC}"
    else
        echo -e "${YELLOW}⚠️  $name 正在启动，请稍候...${NC}"
    fi
}

# 启动所有服务
echo -e "\n${BLUE}════════════════════════════════════════${NC}"
echo -e "${BLUE}    启动核心服务${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}"

start_service "Verifier" 8000 "flowernet-verifier"
start_service "Controller" 8001 "flowernet-controler"
start_service "Generator" 8002 "flowernet-generator"

# 等待所有服务完全启动
echo -e "\n${YELLOW}⏳ 等待所有服务启动完成...${NC}"
sleep 5

echo -e "\n${BLUE}════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ FlowerNet 系统启动完成！${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}"

echo -e "\n📋 服务地址:"
echo -e "  ${BLUE}Generator:${NC}  http://localhost:8002 (API docs: /docs)"
echo -e "  ${BLUE}Verifier:${NC}   http://localhost:8000 (API docs: /docs)"
echo -e "  ${BLUE}Controller:${NC} http://localhost:8001 (API docs: /docs)"

echo -e "\n📝 日志文件:"
echo -e "  ${BLUE}Generator:${NC}  logs/Generator.log"
echo -e "  ${BLUE}Verifier:${NC}   logs/Verifier.log"
echo -e "  ${BLUE}Controller:${NC} logs/Controller.log"

echo -e "\n🧪 运行测试:"
echo -e "  ${BLUE}python3 test_flowernet_e2e.py${NC}"

echo -e "\n🛑 停止所有服务:"
echo -e "  ${BLUE}bash stop-flowernet.sh${NC}"

echo -e "\n"
# 保持脚本运行，显示日志
echo -e "${YELLOW}按 Ctrl+C 停止（不会停止后台服务）${NC}"
echo -e "${YELLOW}使用 'bash stop-flowernet.sh' 停止所有服务${NC}"
echo -e "\n"

# 显示实时日志
tail -f logs/*.log
