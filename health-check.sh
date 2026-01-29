#!/bin/bash

#############################################################################
# FlowerNet 生产环境检查脚本
# 
# 功能: 检查系统健康状态、性能指标、日志分析
# 使用方法: ./health-check.sh [--detailed] [--logs] [--metrics]
#############################################################################

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

# 全局变量
DETAILED=false
SHOW_LOGS=false
SHOW_METRICS=false

# 打印函数
print_header() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║ $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC}  $1"
}

print_info() {
    echo -e "${CYAN}ℹ${NC}  $1"
}

# 检查容器状态
check_containers() {
    print_header "容器状态检查"
    
    local containers=("flower-verifier" "flower-controller" "flower-tunnel")
    local all_healthy=true
    
    for container in "${containers[@]}"; do
        if docker ps --filter "name=$container" --filter "status=running" | grep -q "$container"; then
            # 获取容器信息
            local status=$(docker inspect "$container" --format='{{.State.Status}}')
            local uptime=$(docker inspect "$container" --format='{{.State.StartedAt}}')
            print_success "$container is running (since: $uptime)"
            
            # 健康检查
            if docker inspect "$container" --format='{{.State.Health.Status}}' 2>/dev/null | grep -q "healthy\|none"; then
                print_info "  Health: $(docker inspect "$container" --format='{{.State.Health.Status}}')"
            fi
        else
            print_error "$container is NOT running"
            all_healthy=false
        fi
    done
    
    return $([ "$all_healthy" = true ] && echo 0 || echo 1)
}

# 检查网络连接
check_connectivity() {
    print_header "网络连接检查"
    
    # 检查 Verifier API
    if docker-compose exec -T controller-app curl -s http://verifier-app:8000/ > /dev/null 2>&1; then
        print_success "Controller → Verifier 连接正常"
    else
        print_error "Controller → Verifier 连接失败"
    fi
    
    # 检查外部网络（从 Ngrok 容器检查）
    if docker-compose exec -T ngrok curl -s https://api.ngrok.com/api/tunnels > /dev/null 2>&1; then
        print_success "外部网络连接正常"
    else
        print_warning "外部网络连接可能存在问题"
    fi
}

# 检查资源使用
check_resources() {
    print_header "资源使用情况"
    
    echo ""
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
    echo ""
    
    # 检查磁盘空间
    print_info "Docker 数据卷情况:"
    docker volume ls -q | while read volume; do
        if [[ "$volume" == *"verifier"* ]]; then
            local size=$(du -sh $(docker volume inspect "$volume" --format='{{.Mountpoint}}') 2>/dev/null | cut -f1)
            print_info "  $volume: $size"
        fi
    done
}

# 获取 Ngrok 信息
check_ngrok() {
    print_header "Ngrok 隧道状态"
    
    # 从日志获取转发 URL
    local ngrok_url=$(docker logs flower-tunnel 2>/dev/null | grep -oP 'https://\K[^ ]+' | head -1)
    
    if [ -z "$ngrok_url" ]; then
        print_warning "Ngrok URL 尚未建立或 Token 无效"
        echo ""
        echo "诊断步骤:"
        echo "  1. 检查 Token 是否正确: docker logs flower-tunnel | grep -i 'error'"
        echo "  2. 重启 Ngrok: docker-compose restart ngrok"
        echo "  3. 查看完整日志: docker logs flower-tunnel"
    else
        print_success "Ngrok 隧道地址: https://$ngrok_url"
        print_info "  API 端点: https://$ngrok_url/process"
        
        # 测试隧道连接
        if curl -s https://$ngrok_url/ > /dev/null 2>&1; then
            print_success "隧道连接正常"
        else
            print_warning "隧道连接失败，请检查网络"
        fi
    fi
}

# 测试 API
test_api() {
    print_header "API 功能测试"
    
    echo ""
    
    # 测试本地 verifier API
    print_info "测试 Verifier API..."
    if curl -s -X GET http://localhost:8000/ > /dev/null 2>&1; then
        print_success "Verifier API 响应正常"
    else
        print_error "Verifier API 无响应"
    fi
    
    # 测试本地 controller API
    print_info "测试 Controller API..."
    if curl -s -X GET http://localhost:8001/ > /dev/null 2>&1; then
        print_success "Controller API 响应正常"
    else
        print_error "Controller API 无响应"
    fi
    
    # 测试端点
    print_info "测试完整流程..."
    local response=$(curl -s -X POST http://localhost:8001/process \
        -H "Content-Type: application/json" \
        -d '{"outline": "Test"}' 2>/dev/null)
    
    if [ ! -z "$response" ]; then
        print_success "完整流程测试成功"
    else
        print_error "完整流程测试失败"
    fi
}

# 显示日志
show_logs() {
    if [ "$SHOW_LOGS" = true ]; then
        print_header "最近日志"
        
        echo ""
        echo -e "${MAGENTA}=== Verifier 日志 ===${NC}"
        docker logs --tail 20 flower-verifier 2>/dev/null || echo "无日志"
        
        echo ""
        echo -e "${MAGENTA}=== Controller 日志 ===${NC}"
        docker logs --tail 20 flower-controller 2>/dev/null || echo "无日志"
        
        echo ""
        echo -e "${MAGENTA}=== Ngrok 日志 ===${NC}"
        docker logs --tail 20 flower-tunnel 2>/dev/null || echo "无日志"
    fi
}

# 显示指标
show_metrics() {
    if [ "$SHOW_METRICS" = true ]; then
        print_header "系统指标"
        
        echo ""
        print_info "实时统计:"
        docker stats --no-stream
        
        echo ""
        print_info "存储使用:"
        du -sh . 2>/dev/null || echo "无法计算"
    fi
}

# 生成报告
generate_report() {
    print_header "健康检查报告"
    
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local report_file="health-check-report-$(date +%Y%m%d-%H%M%S).txt"
    
    {
        echo "FlowerNet 健康检查报告"
        echo "生成时间: $timestamp"
        echo ""
        echo "=== 容器状态 ==="
        docker-compose ps
        echo ""
        echo "=== 资源使用 ==="
        docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
        echo ""
        echo "=== 网络状态 ==="
        docker network inspect flowernet-agent_flowernet 2>/dev/null || echo "网络信息不可用"
    } > "$report_file"
    
    print_success "报告已生成: $report_file"
}

# 解析参数
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --detailed) DETAILED=true; shift ;;
            --logs) SHOW_LOGS=true; shift ;;
            --metrics) SHOW_METRICS=true; shift ;;
            --help) show_help; exit 0 ;;
            *) echo "未知参数: $1"; show_help; exit 1 ;;
        esac
    done
}

# 显示帮助
show_help() {
    cat << EOF
使用方法: ./health-check.sh [选项]

选项:
  --detailed      显示详细信息
  --logs          显示容器日志
  --metrics       显示系统指标
  --help          显示此帮助信息

示例:
  ./health-check.sh                    # 基础检查
  ./health-check.sh --detailed --logs  # 详细检查和日志
  ./health-check.sh --metrics          # 显示性能指标
EOF
}

# 主函数
main() {
    parse_args "$@"
    
    clear
    print_header "FlowerNet 生产环境健康检查"
    echo "检查时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    
    # 执行检查
    check_containers && containers_ok=true || containers_ok=false
    echo ""
    
    check_connectivity
    echo ""
    
    check_resources
    echo ""
    
    check_ngrok
    echo ""
    
    test_api
    echo ""
    
    # 显示可选信息
    if [ "$SHOW_LOGS" = true ]; then
        show_logs
        echo ""
    fi
    
    if [ "$SHOW_METRICS" = true ]; then
        show_metrics
        echo ""
    fi
    
    # 生成报告
    generate_report
    
    # 总体状态
    print_header "检查完成"
    if [ "$containers_ok" = true ]; then
        print_success "系统状态正常"
        exit 0
    else
        print_error "检测到问题，请查看上方输出"
        exit 1
    fi
}

# 运行主函数
main "$@"
