#!/bin/bash

# 启动 FlowerNet 所有服务（包括 Outliner + Database）

cd "$(dirname "$0")"

echo "🌸 启动 FlowerNet 完整服务套件（带 Database 集成）"
echo "=============================================================="

# 记录外部传入的 UniEval 覆盖参数（优先级高于 .env）
_OVR_UNIEVAL_KEEP_ALIVE_SET=${UNIEVAL_KEEP_ALIVE+x}
_OVR_UNIEVAL_KEEP_ALIVE_VAL=${UNIEVAL_KEEP_ALIVE-}
_OVR_UNIEVAL_WAIT_READY_SET=${UNIEVAL_WAIT_READY+x}
_OVR_UNIEVAL_WAIT_READY_VAL=${UNIEVAL_WAIT_READY-}
_OVR_UNIEVAL_AUTO_RESTART_SET=${UNIEVAL_AUTO_RESTART+x}
_OVR_UNIEVAL_AUTO_RESTART_VAL=${UNIEVAL_AUTO_RESTART-}
_OVR_UNIEVAL_READY_TIMEOUT_SET=${UNIEVAL_READY_TIMEOUT+x}
_OVR_UNIEVAL_READY_TIMEOUT_VAL=${UNIEVAL_READY_TIMEOUT-}

# 加载 .env 文件
if [ -f .env ]; then
    echo "✅ 加载 .env 配置文件"
    set -a
    . ./.env
    set +a
else
    echo "⚠️  未找到 .env 文件，使用默认配置"
fi

# 恢复外部覆盖参数
if [ -n "${_OVR_UNIEVAL_KEEP_ALIVE_SET:-}" ]; then
    UNIEVAL_KEEP_ALIVE="$_OVR_UNIEVAL_KEEP_ALIVE_VAL"
fi
if [ -n "${_OVR_UNIEVAL_WAIT_READY_SET:-}" ]; then
    UNIEVAL_WAIT_READY="$_OVR_UNIEVAL_WAIT_READY_VAL"
fi
if [ -n "${_OVR_UNIEVAL_AUTO_RESTART_SET:-}" ]; then
    UNIEVAL_AUTO_RESTART="$_OVR_UNIEVAL_AUTO_RESTART_VAL"
fi
if [ -n "${_OVR_UNIEVAL_READY_TIMEOUT_SET:-}" ]; then
    UNIEVAL_READY_TIMEOUT="$_OVR_UNIEVAL_READY_TIMEOUT_VAL"
fi

# 选择 Python 解释器（优先 .venv）
if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN="$(pwd)/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

# 默认使用 SenseNova（可通过环境变量覆盖）
GENERATOR_PROVIDER=${GENERATOR_PROVIDER:-sensenova}
OUTLINER_PROVIDER=${OUTLINER_PROVIDER:-sensenova}
GENERATOR_MODEL=${GENERATOR_MODEL:-SenseNova-V6-5-Turbo}
OUTLINER_MODEL=${OUTLINER_MODEL:-SenseNova-V6-5-Turbo}
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}

# 归一化 provider 值（防止大小写/空格导致兼容逻辑失效）
GENERATOR_PROVIDER=$(echo "$GENERATOR_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
OUTLINER_PROVIDER=$(echo "$OUTLINER_PROVIDER" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')

export GENERATOR_PROVIDER OUTLINER_PROVIDER GENERATOR_MODEL OUTLINER_MODEL

export GENERATOR_PROVIDER OUTLINER_PROVIDER
export GENERATOR_MODEL OUTLINER_MODEL

echo "✅ Provider 策略: GENERATOR_PROVIDER=$GENERATOR_PROVIDER, OUTLINER_PROVIDER=$OUTLINER_PROVIDER"
echo "✅ 模型策略: GENERATOR_MODEL=$GENERATOR_MODEL, OUTLINER_MODEL=$OUTLINER_MODEL"

if [[ "$GENERATOR_PROVIDER" == *"ollama"* ]] || [[ "$OUTLINER_PROVIDER" == *"ollama"* ]]; then
    if curl -fsS "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
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

# UniEval 运行策略（常驻 + 预热门禁 + 持久缓存）
UNIEVAL_KEEP_ALIVE=${UNIEVAL_KEEP_ALIVE:-true}
UNIEVAL_WAIT_READY=${UNIEVAL_WAIT_READY:-true}
UNIEVAL_READY_TIMEOUT=${UNIEVAL_READY_TIMEOUT:-360}
UNIEVAL_CACHE_DIR=${UNIEVAL_CACHE_DIR:-.cache/huggingface}
UNIEVAL_AUTO_RESTART=${UNIEVAL_AUTO_RESTART:-true}
UNIEVAL_WATCHDOG_INTERVAL=${UNIEVAL_WATCHDOG_INTERVAL:-20}
UNIEVAL_RESTART_COOLDOWN=${UNIEVAL_RESTART_COOLDOWN:-90}
UNIEVAL_WATCHDOG_PID_FILE=${UNIEVAL_WATCHDOG_PID_FILE:-/tmp/unieval-watchdog.pid}

if [[ "$UNIEVAL_CACHE_DIR" != /* ]]; then
    UNIEVAL_CACHE_DIR="$(pwd)/$UNIEVAL_CACHE_DIR"
fi
mkdir -p "$UNIEVAL_CACHE_DIR"
export UNIEVAL_CACHE_DIR

UNIEVAL_REUSE=0
if [ "$UNIEVAL_KEEP_ALIVE" = "true" ] && curl -fsS http://localhost:8004/health/live >/dev/null 2>&1; then
    UNIEVAL_REUSE=1
fi

# 停止旧服务
echo ""
echo "🛑 停止旧服务..."
PORTS_TO_STOP="8000 8001 8002 8003 8010"
if [ $UNIEVAL_REUSE -eq 0 ]; then
    PORTS_TO_STOP="$PORTS_TO_STOP 8004"
    if [ -f "$UNIEVAL_WATCHDOG_PID_FILE" ]; then
        watchdog_pid=$(cat "$UNIEVAL_WATCHDOG_PID_FILE" 2>/dev/null || true)
        if [ -n "$watchdog_pid" ] && kill -0 "$watchdog_pid" 2>/dev/null; then
            kill "$watchdog_pid" 2>/dev/null || true
        fi
        rm -f "$UNIEVAL_WATCHDOG_PID_FILE"
    fi
else
    echo "ℹ️  检测到 UniEval 已在运行，按配置保留常驻进程（不重启 8004）"
fi

for port in $PORTS_TO_STOP; do
    pids=$(lsof -ti tcp:$port 2>/dev/null || true)
    if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
    fi
done
sleep 2

# 启动 UniEval (端口 8004) - 可选
UNIEVAL_OK=0
UNIEVAL_STARTED=0
if [ -n "${UNIEVAL_ENDPOINT:-}" ] && [[ "${UNIEVAL_ENDPOINT}" == *"localhost:8004"* ]]; then
    echo ""
    if [ $UNIEVAL_REUSE -eq 1 ]; then
        UNIEVAL_STARTED=1
        UNIEVAL_OK=1
        echo "♻️  复用已有 UniEval 进程 (端口 8004)，保持常驻"
    else
        echo "🚀 尝试启动 UniEval (端口 8004)..."
        if "$PYTHON_BIN" -c "import transformers, torch, sentencepiece" >/dev/null 2>&1; then
            cd flowernet-unieval
            nohup "$PYTHON_BIN" main.py > /tmp/unieval.log 2>&1 &
            UNIEVAL_PID=$!
            UNIEVAL_STARTED=1
            echo "   PID: $UNIEVAL_PID"
            cd ..
            # 等待 API 进程就绪
            UNIEVAL_WAIT_OK=0
            for i in $(seq 1 30); do
                if curl -fsS http://localhost:8004/health/live >/dev/null 2>&1; then
                    UNIEVAL_WAIT_OK=1
                    break
                fi
                sleep 1
            done
            if [ $UNIEVAL_WAIT_OK -eq 1 ]; then
                UNIEVAL_OK=1
                echo "   ✅ UniEval API 已启动（预热进行中）"
            else
                echo "   ❌ UniEval API 未在预期时间内启动"
                exit 1
            fi
        else
            echo "   ⚠️ 本地缺少 UniEval 依赖（transformers/torch/sentencepiece），自动关闭 UNIEVAL_ENDPOINT"
            echo "   ℹ️ 若需要真实 UniEval 推理，请使用 docker-compose 启动 unieval-app"
            export UNIEVAL_ENDPOINT=""
        fi
    fi

    if [ $UNIEVAL_STARTED -eq 1 ] && [ "$UNIEVAL_WAIT_READY" = "true" ]; then
        echo "   ⏳ 等待 UniEval 预热完成（ready）..."
        READY_OK=0
        for i in $(seq 1 "$UNIEVAL_READY_TIMEOUT"); do
            if curl -fsS http://localhost:8004/health/ready >/dev/null 2>&1; then
                READY_OK=1
                break
            fi
            sleep 1
        done
        if [ $READY_OK -eq 1 ]; then
            echo "   ✅ UniEval 已 ready，开始放流量"
        else
            echo "   ❌ UniEval 在 ${UNIEVAL_READY_TIMEOUT}s 内未 ready，停止启动以避免流量进入未就绪实例"
            exit 1
        fi
    fi

    if [ $UNIEVAL_STARTED -eq 1 ] && [ "$UNIEVAL_AUTO_RESTART" = "true" ]; then
        if [ -f "$UNIEVAL_WATCHDOG_PID_FILE" ]; then
            watchdog_pid=$(cat "$UNIEVAL_WATCHDOG_PID_FILE" 2>/dev/null || true)
        else
            watchdog_pid=""
        fi

        if [ -n "$watchdog_pid" ] && kill -0 "$watchdog_pid" 2>/dev/null; then
            echo "   ♻️ UniEval Watchdog 已运行 (PID: $watchdog_pid)"
        else
            echo "   🛡️ 启动 UniEval Watchdog（自动拉起 + 冷却保护）..."
            FLOWERNET_ROOT="$(pwd)"
            nohup bash -c "
                interval=$UNIEVAL_WATCHDOG_INTERVAL
                cooldown=$UNIEVAL_RESTART_COOLDOWN
                last_restart=0
                while true; do
                    sleep \"\$interval\"
                    if curl -fsS http://localhost:8004/health/ready >/dev/null 2>&1; then
                        continue
                    fi
                    now=\$(date +%s)
                    if [ \$((now-last_restart)) -lt \$cooldown ]; then
                        continue
                    fi
                    if ! \"$PYTHON_BIN\" -c 'import transformers, torch, sentencepiece' >/dev/null 2>&1; then
                        continue
                    fi
                    last_restart=\$now
                    pids=\$(lsof -ti tcp:8004 2>/dev/null || true)
                    if [ -n \"\$pids\" ]; then
                        kill -9 \$pids 2>/dev/null || true
                    fi
                    cd \"$FLOWERNET_ROOT/flowernet-unieval\"
                    UNIEVAL_CACHE_DIR=\"$UNIEVAL_CACHE_DIR\" \"$PYTHON_BIN\" main.py >> /tmp/unieval.log 2>&1 &
                done
            " > /tmp/unieval-watchdog.log 2>&1 &
            watchdog_pid=$!
            echo "$watchdog_pid" > "$UNIEVAL_WATCHDOG_PID_FILE"
            echo "   ✅ UniEval Watchdog 已启动 (PID: $watchdog_pid)"
        fi
    fi
fi

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
    if curl -fsS "$url" > /dev/null 2>&1; then
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
if [ $UNIEVAL_STARTED -eq 1 ]; then
    check_service "http://localhost:8004/health/ready" "UniEval (8004)" && UNIEVAL_OK=1
fi

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
    if [ $UNIEVAL_STARTED -eq 1 ]; then
        if [ $UNIEVAL_OK -eq 1 ]; then
            echo "  - UniEval:    http://localhost:8004"
        else
            echo "  - UniEval:    未就绪（已自动降级）"
        fi
    fi
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
    if [ $UNIEVAL_STARTED -eq 1 ]; then
        echo "  - /tmp/unieval.log"
    fi
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
    if [ $UNIEVAL_STARTED -eq 1 ]; then
        echo "  tail -f /tmp/unieval.log"
    fi
    echo ""
    exit 1
fi
