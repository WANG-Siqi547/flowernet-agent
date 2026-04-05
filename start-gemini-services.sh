#!/bin/bash

# 启动 FlowerNet 服务（使用 Google Gemini 免费 API）

cd "$(dirname "$0")"

echo "🌸 启动 FlowerNet 服务 (使用 Google Gemini)"
echo "=========================================="

# 检查 GOOGLE_API_KEY
if [ -z "$GOOGLE_API_KEY" ]; then
    echo ""
    echo "⚠️  警告: GOOGLE_API_KEY 环境变量未设置！"
    echo ""
    echo "请先设置 API Key:"
    echo "  export GOOGLE_API_KEY=\"你的API密钥\""
    echo ""
    echo "获取免费 API Key: https://aistudio.google.com/app/apikey"
    echo "详细说明: GEMINI_SETUP_GUIDE.md"
    echo ""
    read -p "是否继续启动? (可能会失败) [y/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ GOOGLE_API_KEY 已设置"
fi

# 停止旧服务
echo ""
echo "🛑 停止旧服务..."
pkill -f "main.py" 2>/dev/null
sleep 2

# 启动 Verifier (端口 8000)
echo ""
echo "🚀 启动 Verifier (端口 8000)..."
nohup python3 flowernet-verifier/main.py 8000 > /tmp/verifier.log 2>&1 &
VERIFIER_PID=$!
echo "   PID: $VERIFIER_PID"
sleep 1

# 启动 Controller (端口 8001)
echo ""
echo "🚀 启动 Controller (端口 8001)..."
nohup python3 flowernet-controler/main.py 8001 > /tmp/controller.log 2>&1 &
CONTROLLER_PID=$!
echo "   PID: $CONTROLLER_PID"
sleep 1

# 启动 Generator with Gemini (端口 8002)
echo ""
echo "🚀 启动 Generator with Gemini (端口 8002)..."
nohup python3 flowernet-generator/main.py 8002 gemini > /tmp/generator.log 2>&1 &
GENERATOR_PID=$!
echo "   PID: $GENERATOR_PID"
sleep 2

# 检查服务状态
echo ""
echo "🔍 检查服务状态..."
echo ""

check_service() {
    local port=$1
    local name=$2
    if curl -s http://localhost:$port/ > /dev/null 2>&1; then
        echo "  ✅ $name (端口 $port) - 在线"
        return 0
    else
        echo "  ❌ $name (端口 $port) - 离线"
        return 1
    fi
}

VERIFIER_OK=0
CONTROLLER_OK=0
GENERATOR_OK=0

check_service 8000 "Verifier   " && VERIFIER_OK=1
check_service 8001 "Controller " && CONTROLLER_OK=1
check_service 8002 "Generator  " && GENERATOR_OK=1

echo ""
echo "=========================================="

if [ $VERIFIER_OK -eq 1 ] && [ $CONTROLLER_OK -eq 1 ] && [ $GENERATOR_OK -eq 1 ]; then
    echo "✅ 所有服务启动成功！"
    echo ""
    echo "📡 服务地址:"
    echo "  - Verifier:   http://localhost:8000"
    echo "  - Controller: http://localhost:8001"
    echo "  - Generator:  http://localhost:8002 (使用 Gemini)"
    echo ""
    echo "📖 API 文档:"
    echo "  - http://localhost:8002/docs (Generator)"
    echo ""
    echo "🧪 快速检查:"
    echo "  curl -s http://localhost:8002/health"
    echo "  curl -s http://localhost:8000/health"
    echo "  curl -s http://localhost:8001/health"
    echo ""
    echo "📋 查看日志:"
    echo "  tail -f /tmp/generator.log"
    echo "  tail -f /tmp/verifier.log"
    echo "  tail -f /tmp/controller.log"
else
    echo "⚠️  部分服务启动失败，请检查日志:"
    echo "  tail -f /tmp/generator.log"
    echo "  tail -f /tmp/verifier.log"
    echo "  tail -f /tmp/controller.log"
fi

echo "=========================================="
echo ""
