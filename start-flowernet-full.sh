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

# 选择 Python 解释器（优先 .venv）
if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN="$(pwd)/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

# 默认使用 Gemini 主 + OpenRouter 备（可通过环境变量覆盖）
GENERATOR_PROVIDER=${GENERATOR_PROVIDER:-gemini,openrouter}
OUTLINER_PROVIDER=${OUTLINER_PROVIDER:-gemini,openrouter}
GENERATOR_MODEL=${GENERATOR_MODEL:-models/gemini-2.5-flash-lite}
OUTLINER_MODEL=${OUTLINER_MODEL:-models/gemini-2.5-flash-lite}
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}

# 归一化 provider 值（防止大小写/空格导致兼容逻辑失效）
GENERATOR_PROVIDER=$(echo "$GENERATOR_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
OUTLINER_PROVIDER=$(echo "$OUTLINER_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')

export GENERATOR_PROVIDER OUTLINER_PROVIDER GENERATOR_MODEL OUTLINER_MODEL

# 兼容旧配置：如果 .env 仍是单一 ollama，自动升级为链式容灾
if [ "$GENERATOR_PROVIDER" = "ollama" ]; then
    GENERATOR_PROVIDER="gemini,openrouter"
    GENERATOR_MODEL="models/gemini-2.5-flash-lite"
fi
if [ "$OUTLINER_PROVIDER" = "ollama" ]; then
    OUTLINER_PROVIDER="gemini,openrouter"
    OUTLINER_MODEL="models/gemini-2.5-flash-lite"
fi

# 本地容错：若本地 Ollama 可用，默认将其作为第三级兜底
if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
    if [[ "$GENERATOR_PROVIDER" != *"ollama"* ]]; then
        GENERATOR_PROVIDER="$GENERATOR_PROVIDER,ollama"
    fi
    if [[ "$OUTLINER_PROVIDER" != *"ollama"* ]]; then
        OUTLINER_PROVIDER="$OUTLINER_PROVIDER,ollama"
    fi
    export GENERATOR_OLLAMA_MODEL=${GENERATOR_OLLAMA_MODEL:-qwen2.5:7b}
    export OUTLINER_OLLAMA_MODEL=${OUTLINER_OLLAMA_MODEL:-qwen2.5:7b}
    echo "ℹ️  已启用 Ollama 三级兜底（主 Gemini/OpenRouter，次 Ollama）"
fi
export GENERATOR_PROVIDER OUTLINER_PROVIDER
export GENERATOR_MODEL OUTLINER_MODEL

echo "✅ Provider 策略: GENERATOR_PROVIDER=$GENERATOR_PROVIDER, OUTLINER_PROVIDER=$OUTLINER_PROVIDER"
echo "✅ 模型策略: GENERATOR_MODEL=$GENERATOR_MODEL, OUTLINER_MODEL=$OUTLINER_MODEL"

if [[ "$GENERATOR_PROVIDER" == *"ollama"* ]] || [[ "$OUTLINER_PROVIDER" == *"ollama"* ]]; then
    if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "✅ Ollama 服务可用: $OLLAMA_URL"
    else
        echo ""
        echo "❌ 错误: provider chain 包含 ollama，但 Ollama 服务不可用 ($OLLAMA_URL)"
        echo ""
        echo "请先启动 Ollama，或从 provider chain 移除 ollama："
        echo "  ollama serve"
        echo "  ollama pull qwen2.5:7b"
        echo ""
        exit 1
    fi
else
    echo "ℹ️  当前 provider chain 不包含 Ollama"
fi

# 显示数据库配置
echo "✅ 数据库配置:"
echo "   USE_DATABASE=${USE_DATABASE:-true}"
echo "   DATABASE_PATH=${DATABASE_PATH:-flowernet_history.db}"

# 停止旧服务
echo ""
echo "🛑 停止旧服务..."
pkill -f "flowernet-.*main.py" 2>/dev/null
pkill -f "python.*main.py" 2>/dev/null
pkill -f "uvicorn.*main:app" 2>/dev/null
for port in 8000 8001 8002 8003 8010; do
    pids=$(lsof -ti tcp:$port 2>/dev/null || true)
    if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
    fi
done
sleep 2

# 启动 Verifier (端口 8000)
echo ""
echo "🚀 启动 Verifier (端口 8000)..."
cd flowernet-verifier
nohup "$PYTHON_BIN" main.py > /tmp/verifier.log 2>&1 &
VERIFIER_PID=$!
echo "   PID: $VERIFIER_PID"
cd ..
sleep 2

# 启动 Controller (端口 8001)
echo ""
echo "🚀 启动 Controller (端口 8001)..."
cd flowernet-controler
nohup "$PYTHON_BIN" main.py > /tmp/controller.log 2>&1 &
CONTROLLER_PID=$!
echo "   PID: $CONTROLLER_PID"
cd ..
sleep 2

# 启动 Outliner (端口 8003)
echo ""
echo "🚀 启动 Outliner (端口 8003)..."
cd flowernet-outliner
nohup "$PYTHON_BIN" main.py > /tmp/outliner.log 2>&1 &
OUTLINER_PID=$!
echo "   PID: $OUTLINER_PID"
cd ..
sleep 3

# 启动 Generator (端口 8002)
echo ""
echo "🚀 启动 Generator (端口 8002)..."
cd flowernet-generator
nohup "$PYTHON_BIN" main.py > /tmp/generator.log 2>&1 &
GENERATOR_PID=$!
echo "   PID: $GENERATOR_PID"
cd ..
sleep 3

# 启动 Web (端口 8010)
echo ""
echo "🚀 启动 Web (端口 8010)..."
cd flowernet-web
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 OUTLINER_URL=http://localhost:8003 GENERATOR_URL=http://localhost:8002 REQUEST_TIMEOUT=${REQUEST_TIMEOUT:-3600} nohup "$PYTHON_BIN" -m uvicorn main:app --host 0.0.0.0 --port 8010 > /tmp/web.log 2>&1 &
WEB_PID=$!
echo "   PID: $WEB_PID"
cd ..
sleep 3

# 检查服务状态
echo ""
echo "=============================================================="
echo "🔍 检查服务状态..."
echo "=============================================================="
echo ""

check_service() {
    local url=$1
    local name=$2
    if curl -s "$url" > /dev/null 2>&1; then
        echo "  ✅ $name - 在线"
        return 0
    else
        echo "  ❌ $name - 离线"
        return 1
    fi
}

VERIFIER_OK=0
CONTROLLER_OK=0
OUTLINER_OK=0
GENERATOR_OK=0
WEB_OK=0

check_service "http://localhost:8000/" "Verifier (8000)" && VERIFIER_OK=1
check_service "http://localhost:8001/" "Controller (8001)" && CONTROLLER_OK=1
check_service "http://localhost:8003/" "Outliner (8003)" && OUTLINER_OK=1
check_service "http://localhost:8002/" "Generator (8002)" && GENERATOR_OK=1
check_service "http://localhost:8010/health" "Web (8010)" && WEB_OK=1

echo ""
echo "=============================================================="

if [ $VERIFIER_OK -eq 1 ] && [ $CONTROLLER_OK -eq 1 ] && [ $GENERATOR_OK -eq 1 ] && [ $OUTLINER_OK -eq 1 ] && [ $WEB_OK -eq 1 ]; then
    echo "✅ 所有服务启动成功！"
    echo ""
    echo "📡 服务地址:"
    echo "  - Verifier:   http://localhost:8000"
    echo "  - Controller: http://localhost:8001"
    echo "  - Outliner:   http://localhost:8003"
    echo "  - Generator:  http://localhost:8002"
    echo "  - Web:        http://localhost:8010"
    echo ""
    echo "📖 关键 API:"
    echo "  - http://localhost:8010/api/generate"
    echo "  - http://localhost:8010/api/poffices/generate"
    echo "  - http://localhost:8010/api/download-docx"
    echo ""
    echo "📝 日志文件:"
    echo "  - /tmp/verifier.log"
    echo "  - /tmp/controller.log"
    echo "  - /tmp/outliner.log"
    echo "  - /tmp/generator.log"
    echo "  - /tmp/web.log"
    echo ""
    echo "🧪 快速检查:"
    echo "  curl -s http://localhost:8010/health"
    echo "  curl -s http://localhost:8002/health"
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
    echo "  tail -f /tmp/outliner.log"
    echo "  tail -f /tmp/generator.log"
    echo "  tail -f /tmp/web.log"
    echo ""
    exit 1
fi
