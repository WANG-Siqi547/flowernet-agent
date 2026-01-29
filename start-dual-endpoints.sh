#!/bin/bash

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FlowerNet 双端点一键启动脚本
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -e

NGROK_TOKEN="38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ️  $1${NC}"
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print_header "FlowerNet 双端点启动"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 步骤 1: 检查 Docker
print_info "检查 Docker..."
if command -v docker &> /dev/null; then
    print_success "Docker 已安装"
else
    print_error "Docker 未安装"
    exit 1
fi

# 步骤 2: 检查/安装 Ngrok
print_info "检查 Ngrok..."
if ! command -v ngrok &> /dev/null; then
    print_error "Ngrok 未安装，现在安装..."
    
    # 检查是否有 Homebrew
    if command -v brew &> /dev/null; then
        print_info "使用 Homebrew 安装 Ngrok..."
        brew install ngrok
        print_success "Ngrok 安装完成"
    else
        print_error "Homebrew 未安装"
        echo ""
        echo "请先安装 Homebrew:"
        echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo ""
        exit 1
    fi
else
    NGROK_VERSION=$(ngrok --version 2>&1 | head -1)
    print_success "Ngrok 已安装: $NGROK_VERSION"
fi

# 步骤 3: 配置 Ngrok Token
print_info "配置 Ngrok Token..."
ngrok config add-authtoken "$NGROK_TOKEN" 2>/dev/null || true
print_success "Ngrok Token 已配置"

# 步骤 4: 检查 Docker 服务
print_info "检查 Docker 服务..."
if docker-compose ps 2>/dev/null | grep -q "flower-verifier"; then
    print_success "Docker 服务正在运行"
else
    print_error "Docker 服务未运行，现在启动..."
    docker-compose up -d
    sleep 10
    print_success "Docker 服务已启动"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print_header "立即启动 Ngrok 隧道"
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

echo "需要在两个不同的终端中运行以下命令："
echo ""
echo "┌─ 终端 1: Controller 隧道 (端口 8001) ─┐"
echo "│                                        │"
echo "│  ${BLUE}./ngrok-controller.sh${NC}            │"
echo "│                                        │"
echo "└────────────────────────────────────────┘"
echo ""
echo "┌─ 终端 2: Verifier 隧道 (端口 8000) ──┐"
echo "│                                        │"
echo "│  ${BLUE}./ngrok-verifier.sh${NC}              │"
echo "│                                        │"
echo "└────────────────────────────────────────┘"
echo ""

read -p "按 Enter 键继续或选择下面的选项..."
echo ""
echo "选项:"
echo "  1) 自动启动两个隧道 (需要 tmux)"
echo "  2) 手动启动 (查看说明)"
echo ""
read -p "选择 (1-2): " choice

case $choice in
    1)
        if command -v tmux &> /dev/null; then
            print_info "启动 tmux 会话..."
            
            # 创建新窗口
            tmux new-session -d -s flowernet
            
            # 窗口 1: Controller
            tmux send-keys -t flowernet "cd \"$(pwd)\" && ./ngrok-controller.sh" Enter
            
            # 窗口 2: Verifier
            tmux new-window -t flowernet
            tmux send-keys -t flowernet "cd \"$(pwd)\" && ./ngrok-verifier.sh" Enter
            
            print_success "Tmux 会话已创建！"
            echo ""
            echo "附加到会话:"
            echo "  ${BLUE}tmux attach -t flowernet${NC}"
            echo ""
            echo "查看不同窗口:"
            echo "  Ctrl+b n  (下一个窗口)"
            echo "  Ctrl+b p  (上一个窗口)"
            echo ""
            
            # 自动附加
            sleep 2
            tmux attach -t flowernet
        else
            print_error "需要安装 tmux"
            exit 1
        fi
        ;;
    2)
        print_info "手动启动说明:"
        echo ""
        echo "打开两个终端窗口，分别执行:"
        echo ""
        echo "  终端 1: ${BLUE}./ngrok-controller.sh${NC}"
        echo "  终端 2: ${BLUE}./ngrok-verifier.sh${NC}"
        echo ""
        ;;
    *)
        print_error "无效选择"
        exit 1
        ;;
esac

print_header "完成！"
echo "你现在有两个独立的公网端点:"
echo ""
echo "  🔵 Controller: https://xxx.ngrok-free.dev  → localhost:8001"
echo "  🔴 Verifier:   https://yyy.ngrok-free.dev  → localhost:8000"
echo ""
echo "访问 Ngrok Web UI 查看详细信息:"
echo "  http://localhost:4040"
echo ""
