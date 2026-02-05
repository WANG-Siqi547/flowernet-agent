#!/bin/bash

# FlowerNet 系统停止脚本
# 关闭所有运行的 FlowerNet 服务

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    停止 FlowerNet 系统${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 停止函数
stop_service() {
    local name=$1
    local pid_file="$SCRIPT_DIR/logs/${name}.pid"
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        echo -e "${BLUE}停止 $name (PID: $pid)...${NC}"
        kill $pid 2>/dev/null || true
        rm -f "$pid_file"
        echo -e "${GREEN}✅ $name 已停止${NC}"
    else
        echo -e "${YELLOW}⚠️  $name PID 文件未找到${NC}"
    fi
}

# 停止所有服务
stop_service "Generator"
stop_service "Verifier"
stop_service "Controller"

# 杀死可能残留的进程
echo -e "\n${BLUE}清理残留进程...${NC}"
pkill -f "python.*main.py" || true
sleep 1

# 验证是否全部停止
echo -e "\n${BLUE}验证服务状态...${NC}"
if ! pgrep -f "python.*main.py" > /dev/null; then
    echo -e "${GREEN}✅ 所有服务已停止${NC}"
else
    echo -e "${YELLOW}⚠️  仍有进程运行${NC}"
fi

echo -e "\n${BLUE}════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ 停止完成${NC}"
echo -e "${BLUE}════════════════════════════════════════${NC}"
