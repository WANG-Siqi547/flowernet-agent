#!/bin/bash

#############################################################################
# FlowerNet 生产环境一键部署脚本
# 
# 使用方法: ./deploy.sh [ngrok_token]
# 示例: ./deploy.sh 2Yd9YYxxxxxxxxxxxxxxxxxxxxx_xxxxxx
#############################################################################

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印函数
print_header() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║ $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
}

print_info() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC}  $1"
}

print_error() {
    echo -e "${RED}✗${NC}  $1"
}

# 检查前置条件
check_prerequisites() {
    print_header "检查前置条件"
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    print_info "Docker 已安装: $(docker --version)"
    
    # 检查 Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose 未安装，请先安装"
        exit 1
    fi
    print_info "Docker Compose 已安装: $(docker-compose --version)"
    
    # 检查项目目录
    if [ ! -f "docker-compose.yml" ]; then
        print_error "docker-compose.yml 不存在，请确保在项目根目录运行此脚本"
        exit 1
    fi
    print_info "项目结构正确"
}

# 获取 Ngrok Token
get_ngrok_token() {
    print_header "Ngrok 认证配置"
    
    if [ -z "$1" ]; then
        echo -e "${YELLOW}未提供 Ngrok Token${NC}"
        echo ""
        echo "如何获取 Ngrok Token:"
        echo "1. 访问 https://dashboard.ngrok.com/signup 注册账户"
        echo "2. 登录后访问 https://dashboard.ngrok.com/auth"
        echo "3. 复制你的 Authtoken"
        echo ""
        read -p "请输入你的 Ngrok Authtoken: " NGROK_TOKEN
    else
        NGROK_TOKEN=$1
    fi
    
    if [ -z "$NGROK_TOKEN" ]; then
        print_error "Ngrok Token 为空"
        exit 1
    fi
    
    print_info "已获取 Ngrok Token (前 10 字符): ${NGROK_TOKEN:0:10}..."
}

# 更新 docker-compose.yml
update_docker_compose() {
    print_header "更新 docker-compose.yml"
    
    # 备份原文件
    cp docker-compose.yml docker-compose.yml.backup
    print_info "已备份原文件: docker-compose.yml.backup"
    
    # 替换 Token
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/你的_NGROK_TOKEN/${NGROK_TOKEN}/g" docker-compose.yml
    else
        # Linux
        sed -i "s/你的_NGROK_TOKEN/${NGROK_TOKEN}/g" docker-compose.yml
    fi
    
    print_info "已更新 NGROK_AUTHTOKEN"
    
    # 验证更新
    if grep -q "$NGROK_TOKEN" docker-compose.yml; then
        print_info "Token 已成功写入 docker-compose.yml"
    else
        print_error "Token 更新失败"
        exit 1
    fi
}

# 创建 .env 文件
create_env_file() {
    print_header "创建环境配置文件"
    
    cat > .env << EOF
# FlowerNet 生产环境配置
NGROK_AUTHTOKEN=${NGROK_TOKEN}

# 服务配置
VERIFIER_PORT=8000
CONTROLLER_PORT=8001
NGROK_PORT=4040

# 应用配置
LOG_LEVEL=INFO
DEBUG=false
EOF
    
    print_info "已创建 .env 文件"
}

# 构建镜像
build_images() {
    print_header "构建 Docker 镜像"
    
    echo "这可能需要 5-10 分钟..."
    docker-compose build --no-cache
    
    print_info "镜像构建完成"
}

# 启动服务
start_services() {
    print_header "启动服务"
    
    docker-compose up -d
    
    print_info "服务已启动"
    
    # 等待服务完全启动
    echo "等待服务初始化..."
    sleep 10
}

# 验证服务状态
verify_services() {
    print_header "验证服务状态"
    
    echo ""
    docker-compose ps
    echo ""
    
    # 检查 verifier
    if docker-compose exec -T verifier-app curl -s http://localhost:8000/ > /dev/null 2>&1; then
        print_info "Verifier 服务正常"
    else
        print_warning "Verifier 服务可能未完全启动（首次启动会下载模型，需要等待 1-2 分钟）"
    fi
    
    # 检查 controller
    if docker-compose exec -T controller-app curl -s http://localhost:8001/ > /dev/null 2>&1; then
        print_info "Controller 服务正常"
    else
        print_warning "Controller 服务可能未完全启动"
    fi
    
    # 检查 ngrok
    if docker-compose ps flower-tunnel | grep -q "Up"; then
        print_info "Ngrok 隧道已启动"
    else
        print_error "Ngrok 隧道启动失败，请检查 Token"
    fi
}

# 获取 Ngrok URL
get_ngrok_url() {
    print_header "获取公网 URL"
    
    echo "等待 Ngrok 建立隧道..."
    sleep 3
    
    # 尝试获取 Ngrok URL
    NGROK_URL=$(docker logs flower-tunnel 2>/dev/null | grep -oP 'https://\K[^ ]+' | head -1 || echo "")
    
    if [ -z "$NGROK_URL" ]; then
        print_warning "暂时无法获取 Ngrok URL，这是正常的"
        echo ""
        echo "手动获取方法:"
        echo "  docker logs flower-tunnel | grep 'forwarding'"
        echo ""
        echo "或使用 Ngrok API:"
        echo "  curl http://localhost:4040/api/tunnels | python3 -m json.tool"
    else
        print_info "公网 URL: https://$NGROK_URL"
        echo ""
        echo "你现在可以通过以下地址访问 FlowerNet:"
        echo "  https://$NGROK_URL/process"
    fi
}

# 显示监控信息
show_monitoring() {
    print_header "监控和日志"
    
    echo ""
    echo "实时日志查看:"
    echo "  docker-compose logs -f controller-app"
    echo "  docker-compose logs -f verifier-app"
    echo "  docker-compose logs -f flower-tunnel"
    echo ""
    echo "系统监控:"
    echo "  docker stats"
    echo ""
    echo "停止服务:"
    echo "  docker-compose down"
    echo ""
    echo "重启服务:"
    echo "  docker-compose restart"
}

# 测试 API
test_api() {
    print_header "测试 API"
    
    echo ""
    echo "测试本地 API..."
    
    if curl -s -X GET http://localhost:8001/ > /dev/null 2>&1; then
        print_info "Controller API 响应正常"
        
        # 显示测试命令
        echo ""
        echo "完整流程测试命令:"
        echo ""
        echo 'curl -X POST http://localhost:8001/process \'
        echo '  -H "Content-Type: application/json" \'
        echo "  -d '{\"outline\": \"Discuss the impact of AI on healthcare\"}'"
        echo ""
    else
        print_warning "Controller API 暂不可用（如果是首次启动，请等待 1-2 分钟）"
    fi
}

# 主函数
main() {
    print_header "FlowerNet 生产环境部署"
    
    check_prerequisites
    get_ngrok_token "$1"
    update_docker_compose
    create_env_file
    
    echo ""
    read -p "是否现在构建并启动服务? (y/n): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        build_images
        start_services
        verify_services
        get_ngrok_url
        show_monitoring
        test_api
        
        print_header "部署完成！🎉"
        echo ""
        echo "后续步骤:"
        echo "1. 监控日志确保服务正常运行"
        echo "2. 使用公网 URL 访问 API"
        echo "3. 在生产环境中配置自动监控和告警"
        echo ""
    else
        print_info "部署已取消"
    fi
}

# 运行主函数
main "$1"
