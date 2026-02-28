#!/bin/bash

# 启动 FlowerNet 所有服务（包括 Outliner + Database）

cd "$(dirname "$0")"

echo "🌸 启动 FlowerNet 完整服务套件（带 Database 集成）"
echo "=============================================================="

# 加载 .env 文件
if [ -f .env ]; then
    echo "✅ 加载 .env 配置文件"
    export $(grep -v '^#' .env | xargs)
else
    echo "⚠️  未找到 .env 文件，使用默认配置"
fi

# 检查 Ollama（默认 provider）
GENERATOR_PROVIDER=${GENERATOR_PROVIDER:-ollama}
OUTLINER_PROVIDER=${OUTLINER_PROVIDER:-ollama}
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}

if [ "$GENERATOR_PROVIDER" = "ollama" ] || [ "$OUTLINER_PROVIDER" = "ollama" ]; then
    if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "✅ Ollama 服务可用: $OLLAMA_URL"
    else
        echo ""
        echo "❌ 错误: Ollama 服务不可用 ($OLLAMA_URL)"
        echo ""
        echo "请先启动 Ollama："
        echo "  ollama serve"
        echo "  ollama pull qwen2.5:7b"
        echo ""
        exit 1
    fi
else
    echo "ℹ️  当前未使用 Ollama provider"
fi

# 显示数据库配置
echo "✅ 数据库配置:"
echo "   USE_DATABASE=${USE_DATABASE:-true}"
echo "   DATABASE_PATH=${DATABASE_PATH:-flowernet_history.db}"

# 停止旧服务
echo ""
echo "🛑 停止旧服务..."
pkill -f "flowernet-.*main.py" 2>/dev/null
sleep 2

# 启动 Verifier (端口 8000)
echo ""
echo "🚀 启动 Verifier (端口 8000)..."
cd flowernet-verifier
nohup python3 main.py > /tmp/verifier.log 2>&1 &
VERIFIER_PID=$!
echo "   PID: $VERIFIER_PID"
cd ..
sleep 2

# 启动 Controller (端口 8001)
echo ""
echo "🚀 启动 Controller (端口 8001)..."
cd flowernet-controler
nohup python3 main.py > /tmp/controller.log 2>&1 &
CONTROLLER_PID=$!
echo "   PID: $CONTROLLER_PID"
cd ..
sleep 2

# 启动 Generator (端口 8002) - 带 Database
echo ""
echo "🚀 启动 Generator (端口 8002) - 带 Database 集成..."
cd flowernet-generator
nohup python3 main.py > /tmp/generator.log 2>&1 &
GENERATOR_PID=$!
echo "   PID: $GENERATOR_PID"
cd ..
sleep 3

# 启动 Outliner (端口 8003)
echo ""
echo "🚀 启动 Outliner (端口 8003)..."
cd flowernet-outliner
nohup python3 main.py > /tmp/outliner.log 2>&1 &
OUTLINER_PID=$!
echo "   PID: $OUTLINER_PID"
cd ..
sleep 3

# 检查服务状态
echo ""
echo "=============================================================="
echo "🔍 检查服务状态..."
echo "=============================================================="
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
OUTLINER_OK=0

check_service 8000 "Verifier   " && VERIFIER_OK=1
check_service 8001 "Controller " && CONTROLLER_OK=1
check_service 8002 "Generator  " && GENERATOR_OK=1
check_service 8003 "Outliner   " && OUTLINER_OK=1

echo ""
echo "=============================================================="

if [ $VERIFIER_OK -eq 1 ] && [ $CONTROLLER_OK -eq 1 ] && [ $GENERATOR_OK -eq 1 ] && [ $OUTLINER_OK -eq 1 ]; then
    echo "✅ 所有服务启动成功！"
    echo ""
    echo "📡 服务地址:"
    echo "  - Verifier:   http://localhost:8000"
    echo "  - Controller: http://localhost:8001"
    echo "  - Generator:  http://localhost:8002 (带 Database)"
    echo "  - Outliner:   http://localhost:8003"
    echo ""
    echo "📖 API 文档:"
    echo "  - http://localhost:8002/docs (Generator)"
    echo "  - http://localhost:8003/docs (Outliner)"
    echo ""
    echo "💾 数据库文件:"
    echo "  - ${DATABASE_PATH:-flowernet_history.db}"
    echo ""
    echo "📝 日志文件:"
    echo "  - /tmp/verifier.log"
    echo "  - /tmp/controller.log"
    echo "  - /tmp/generator.log"
    echo "  - /tmp/outliner.log"
    echo ""
    echo "🧪 运行完整测试:"
    echo "  python3 test_database_integration.py"
    echo ""
    echo "🛑 停止所有服务:"
    echo "  ./stop-flowernet.sh"
    echo ""
    exit 0
else
    echo "❌ 部分服务启动失败，请检查日志文件"
    echo ""
    echo "查看日志:"
    echo "  tail -f /tmp/verifier.log"
    echo "  tail -f /tmp/controller.log"
    echo "  tail -f /tmp/generator.log"
    echo "  tail -f /tmp/outliner.log"
    echo ""
    exit 1
fi
