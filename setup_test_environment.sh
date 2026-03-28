#!/bin/bash

# 临时切换到 Ollama 用于测试（避免 Azure 网络问题）
cd "$(dirname "$0")"

echo "🔄 临时配置本地服务使用 Ollama..."

# 备份原始配置
cp docker-compose.yml docker-compose.yml.azure-backup

# 创建临时配置：切换到ollama
cat > docker-compose-test.yml << 'EOF'
version: '3.8'

services:
  ollama:
    image: ollama/ollama:latest
    container_name: flower-ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    networks:
      - flowernet

  verifier-app:
    build: ./flowernet-verifier
    container_name: flower-verifier
    restart: unless-stopped
    environment:
      - PORT=8000
      - USE_DATABASE=true
      - DATABASE_PATH=/data/flowernet_history.db
    volumes:
      - shared_data:/data
      - verifier_cache:/root/.cache
    networks:
      - flowernet

  controller-app:
    build: ./flowernet-controler
    container_name: flower-controller
    restart: unless-stopped
    depends_on:
      - verifier-app
    environment:
      - PORT=8001
      - VERIFIER_URL=http://verifier-app:8000
      - USE_DATABASE=true
      - DATABASE_PATH=/data/flowernet_history.db
      - CONTROLLER_USE_LLM_OUTLINE=true
      - CONTROLLER_REQUIRE_LLM_SOURCE=false
      - CONTROLLER_LLM_TIMEOUT=120
      - CONTROLLER_LLM_RETRIES=3
      - CONTROLLER_MIN_SCORE_GAIN=0.001
      - CONTROLLER_MIN_REL_ANCHOR_GAIN=0.005
      - CONTROLLER_MIN_NOVELTY_GAIN=0.005
      - CONTROLLER_MIN_STRUCTURE_GAIN=0.05
    volumes:
      - shared_data:/data
    networks:
      - flowernet

  outliner-app:
    build: ./flowernet-outliner
    container_name: flower-outliner
    restart: unless-stopped
    depends_on:
      - ollama
    environment:
      - PORT=8003
      - OUTLINER_PROVIDER=ollama
      - OUTLINER_MODEL=qwen2.5:7b
      - OLLAMA_URL=http://ollama:11434
      - USE_DATABASE=true
      - DATABASE_PATH=/data/flowernet_history.db
      - OLLAMA_RETRIES=6
      - OLLAMA_BACKOFF=3
      - OLLAMA_MAX_BACKOFF=60
      - PROVIDER_RETRIES=2
      - PROVIDER_BACKOFF=1.2
      - PROVIDER_MAX_BACKOFF=20.0
      - PROVIDER_JITTER=0.3
      - PROVIDER_MIN_INTERVAL=0.8
      - OUTLINER_SERIALIZE_TASKS=true
      - OUTLINER_TASK_WAIT_TIMEOUT=5
      - OUTLINER_FLOW_RETRIES=6
      - OUTLINER_FLOW_BACKOFF=5.0
    volumes:
      - shared_data:/data
    networks:
      - flowernet

  generator-app:
    build: ./flowernet-generator
    container_name: flower-generator
    restart: unless-stopped
    depends_on:
      - ollama
      - verifier-app
      - controller-app
      - outliner-app
    environment:
      - PORT=8002
      - GENERATOR_PROVIDER=ollama
      - GENERATOR_MODEL=qwen2.5:7b
      - OLLAMA_URL=http://ollama:11434
      - VERIFIER_URL=http://verifier-app:8000
      - CONTROLLER_URL=http://controller-app:8001
      - OUTLINER_URL=http://outliner-app:8003
      - USE_DATABASE=true
      - DATABASE_PATH=/data/flowernet_history.db
      - OLLAMA_RETRIES=6
      - OLLAMA_BACKOFF=3
      - OLLAMA_MAX_BACKOFF=60
      - MAX_CONTROLLER_RETRIES=2
      - STRICT_CONTROLLER_EFFECTIVE=false
    volumes:
      - shared_data:/data
    networks:
      - flowernet

  web-app:
    build: ./flowernet-web
    container_name: flower-web
    restart: unless-stopped
    depends_on:
      - outliner-app
      - generator-app
    ports:
      - "8010:8010"
    environment:
      - OUTLINER_URL=http://outliner-app:8003
      - GENERATOR_URL=http://generator-app:8002
      - REQUEST_TIMEOUT=3600
      - API_AUTH_ENABLED=false
      - DOWNSTREAM_RETRIES=10
      - DOWNSTREAM_BACKOFF=4.0
      - DOWNSTREAM_MAX_BACKOFF=180.0
      - DOWNSTREAM_JITTER=0.4
    volumes:
      - shared_data:/data
    networks:
      - flowernet

volumes:
  ollama_data:
  shared_data:
  verifier_cache:

networks:
  flowernet:
    driver: bridge
EOF

echo "✅ 临时配置已创建: docker-compose-test.yml"
echo ""
echo "📝 下一步："
echo "1. 拉取 ollama 模型: docker exec flower-ollama ollama pull qwen2.5:7b"
echo "2. 启动测试环境: docker-compose -f docker-compose-test.yml up -d"
echo "3. 运行测试: python test_controller_trigger_rate.py"
echo "4. 恢复原配置: mv docker-compose.yml.azure-backup docker-compose.yml"
