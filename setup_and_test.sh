#!/bin/bash

# FlowerNet Database 配置和测试自动化脚本

echo "🌸 FlowerNet Database Integration 配置向导"
echo "=============================================================="
echo ""

# 检查项目目录
if [ ! -f "history_store.py" ]; then
    echo "❌ 错误: 请在项目根目录运行此脚本"
    exit 1
fi

# 1. 检查 GOOGLE_API_KEY
echo "📝 步骤 1/4: 检查 GOOGLE_API_KEY"
echo "------------------------------------------------------------"

if [ -z "$GOOGLE_API_KEY" ]; then
    echo ""
    echo "❌ GOOGLE_API_KEY 未设置"
    echo ""
    echo "请按以下步骤获取免费的 Google Gemini API Key:"
    echo ""
    echo "1. 访问: https://aistudio.google.com/app/apikey"
    echo "2. 登录 Google 账号"
    echo "3. 点击 'Create API Key'"
    echo "4. 复制生成的 API Key"
    echo ""
    echo "然后运行以下命令（将 YOUR_API_KEY 替换为你的实际密钥）:"
    echo ""
    echo "  export GOOGLE_API_KEY=\"YOUR_API_KEY\""
    echo ""
    echo "或者永久保存到 ~/.zshrc:"
    echo ""
    echo "  echo 'export GOOGLE_API_KEY=\"YOUR_API_KEY\"' >> ~/.zshrc"
    echo "  source ~/.zshrc"
    echo ""
    read -p "是否已经设置？(输入密钥或按 Enter 跳过): " api_key
    
    if [ ! -z "$api_key" ]; then
        export GOOGLE_API_KEY="$api_key"
        echo "✅ API Key 已临时设置（仅在本次会话有效）"
    else
        echo ""
        echo "⚠️  警告: 没有 API Key，服务将无法正常工作"
        echo ""
        read -p "是否仍要继续? [y/N]: " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    echo "✅ GOOGLE_API_KEY 已设置: ${GOOGLE_API_KEY:0:20}..."
fi

echo ""

# 2. 检查 .env 配置
echo "📝 步骤 2/4: 检查 Database 配置"
echo "------------------------------------------------------------"

if [ -f ".env" ]; then
    echo "✅ .env 文件已存在"
    echo ""
    echo "当前配置:"
    grep -E "USE_DATABASE|DATABASE_PATH" .env | sed 's/^/   /'
else
    echo "⚠️  .env 文件不存在（已自动创建）"
fi

echo ""

# 3. 停止旧服务
echo "📝 步骤 3/4: 停止旧服务"
echo "------------------------------------------------------------"

pkill -f "flowernet-.*main.py" 2>/dev/null
sleep 2
echo "✅ 已停止所有旧服务"
echo ""

# 4. 启动服务
echo "📝 步骤 4/4: 启动所有服务"
echo "------------------------------------------------------------"
echo ""

# 启动 Verifier
echo "🚀 启动 Verifier (8000)..."
cd flowernet-verifier
nohup python3 main.py > /tmp/verifier.log 2>&1 &
cd ..
sleep 2

# 启动 Controller
echo "🚀 启动 Controller (8001)..."
cd flowernet-controler
nohup python3 main.py > /tmp/controller.log 2>&1 &
cd ..
sleep 2

# 启动 Generator (带 Database)
echo "🚀 启动 Generator (8002) - 带 Database..."
cd flowernet-generator
export USE_DATABASE=true
export DATABASE_PATH=flowernet_history.db
nohup python3 main.py > /tmp/generator.log 2>&1 &
cd ..
sleep 3

# 启动 Outliner
echo "🚀 启动 Outliner (8003)..."
cd flowernet-outliner
export USE_DATABASE=true
export DATABASE_PATH=flowernet_history.db
nohup python3 main.py > /tmp/outliner.log 2>&1 &
cd ..
sleep 3

# 检查服务状态
echo ""
echo "=============================================================="
echo "🔍 检查服务状态"
echo "=============================================================="
echo ""

check_service() {
    local port=$1
    local name=$2
    if curl -s http://localhost:$port/ > /dev/null 2>&1; then
        echo "  ✅ $name (端口 $port)"
        return 0
    else
        echo "  ❌ $name (端口 $port) - 启动失败"
        return 1
    fi
}

ALL_OK=true
check_service 8000 "Verifier   " || ALL_OK=false
check_service 8001 "Controller " || ALL_OK=false
check_service 8002 "Generator  " || ALL_OK=false
check_service 8003 "Outliner   " || ALL_OK=false

echo ""

if [ "$ALL_OK" = true ]; then
    echo "=============================================================="
    echo "✅ 所有服务启动成功！"
    echo "=============================================================="
    echo ""
    echo "💾 Database 配置:"
    echo "   USE_DATABASE: true"
    echo "   DATABASE_PATH: flowernet_history.db"
    echo ""
    echo "📡 服务地址:"
    echo "   http://localhost:8000 - Verifier"
    echo "   http://localhost:8001 - Controller"
    echo "   http://localhost:8002 - Generator (带 Database)"
    echo "   http://localhost:8003 - Outliner"
    echo ""
    echo "📝 日志文件:"
    echo "   /tmp/verifier.log"
    echo "   /tmp/controller.log"
    echo "   /tmp/generator.log"
    echo "   /tmp/outliner.log"
    echo ""
    echo "=============================================================="
    echo ""
    read -p "🧪 是否立即运行快速健康检查? [Y/n]: " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        echo ""
        echo "=============================================================="
        echo "🧪 运行服务健康检查"
        echo "=============================================================="
        echo ""
        sleep 2
        curl -s http://localhost:8000/health
        curl -s http://localhost:8001/health
        curl -s http://localhost:8002/health
        curl -s http://localhost:8003/health
    else
        echo ""
        echo "手动运行健康检查:"
        echo "  curl -s http://localhost:8000/health"
        echo "  curl -s http://localhost:8001/health"
        echo "  curl -s http://localhost:8002/health"
        echo "  curl -s http://localhost:8003/health"
        echo ""
        echo "停止所有服务:"
        echo "  pkill -f 'flowernet-.*main.py'"
        echo ""
    fi
else
    echo "=============================================================="
    echo "❌ 部分服务启动失败"
    echo "=============================================================="
    echo ""
    echo "请查看日志文件排查问题:"
    echo "  tail -f /tmp/verifier.log"
    echo "  tail -f /tmp/controller.log"
    echo "  tail -f /tmp/generator.log"
    echo "  tail -f /tmp/outliner.log"
    echo ""
    exit 1
fi
