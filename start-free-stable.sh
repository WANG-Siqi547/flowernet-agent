#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/5] 启动全栈容器..."
docker compose up -d --build

echo "[2/5] 等待 Ollama 启动..."
for i in {1..60}; do
  if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama 就绪"
    break
  fi
  sleep 2
done

echo "[3/5] 拉取模型 qwen2.5:7b（首次会比较久）..."
docker exec -i flower-ollama ollama pull qwen2.5:7b

echo "[4/5] 检查服务健康..."
curl -s http://localhost:8000/ >/dev/null && echo "verifier ok"
curl -s http://localhost:8001/ >/dev/null && echo "controller ok"
curl -s http://localhost:8002/ >/dev/null && echo "generator ok"
curl -s http://localhost:8003/ >/dev/null && echo "outliner ok"
curl -s http://localhost:8010/health >/dev/null && echo "web ok"

echo "[5/5] 端到端快速验证..."
curl -s -X POST http://localhost:8010/api/poffices/generate \
  -H "Content-Type: application/json" \
  -d '{"query":"test"}' | head -c 300 || true

echo ""
echo "✅ 免费稳定模式已启动（无 ngrok / 无 Gemini）"
echo "Web 入口: http://localhost:8010"
