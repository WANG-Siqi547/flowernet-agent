# FlowerNet 生产环境部署指南

## 📋 前置条件

### 1. 获取 Ngrok 认证令牌

**步骤 1**: 注册 Ngrok 账户
```bash
# 访问官网
https://dashboard.ngrok.com/signup

# 或直接登录（如果已有账户）
https://dashboard.ngrok.com/login
```

**步骤 2**: 获取 Authtoken
1. 登录后访问 Dashboard
2. 左侧菜单选择 "Your Authtoken"
3. 点击 "Copy" 复制你的 token
4. Token 格式示例: `2Yd9YYxxxxxxxxxxxxxxxxxxxxx_xxxxxx`

---

## 🚀 部署步骤

### 方案 1: 使用环境变量（推荐用于 CI/CD）

```bash
# 方式 A: 直接在命令行设置环境变量
cd /Users/k1ns9sley/Desktop/msc\ project/flowernet-agent

export NGROK_AUTHTOKEN="你的_Ngrok_Token"
docker-compose up -d

# 方式 B: 创建 .env 文件
echo "NGROK_AUTHTOKEN=你的_Ngrok_Token" > .env
docker-compose up -d
```

### 方案 2: 直接编辑 docker-compose.yml（简单快速）

```yaml
environment:
  - NGROK_AUTHTOKEN=你的_Ngrok_Token
```

---

## 📦 完整部署命令

```bash
# Step 1: 进入项目目录
cd /Users/k1ns9sley/Desktop/msc\ project/flowernet-agent

# Step 2: 拉取最新代码（如果使用 Git）
git pull origin main

# Step 3: 重新构建镜像（可选，仅在代码更新时）
docker-compose build --no-cache

# Step 4: 启动所有服务
docker-compose up -d

# Step 5: 验证服务状态
docker-compose ps
docker logs flower-verifier --tail 20
docker logs flower-controller --tail 20
docker logs flower-tunnel --tail 20
```

---

## 🔍 验证部署

### 1. 检查容器状态
```bash
docker-compose ps

# 预期输出:
# NAME                 STATUS              PORTS
# flower-verifier      Up                  0.0.0.0:8000->8000/tcp
# flower-controller    Up                  0.0.0.0:8001->8001/tcp
# flower-tunnel        Up                  [Ngrok URL]
```

### 2. 获取 Ngrok 公网 URL
```bash
# 查看 ngrok 日志找到公网地址
docker logs flower-tunnel | grep -i "forwarding"

# 或使用 ngrok API
curl http://localhost:4040/api/tunnels
```

### 3. 测试本地 API
```bash
# Verifier 服务
curl http://localhost:8000/

# Controller 服务
curl http://localhost:8001/

# 完整验证测试
curl -X POST http://localhost:8001/process \
  -H "Content-Type: application/json" \
  -d '{"outline": "Discuss the impact of AI on healthcare"}'
```

### 4. 通过 Ngrok 外网访问
```bash
# 获取 Ngrok URL
NGROK_URL=$(docker logs flower-tunnel | grep -oP 'https://\K[^ ]+(?=.ngrok-free.app)' | head -1)

# 测试外网访问
curl -X POST https://${NGROK_URL}.ngrok-free.app/process \
  -H "Content-Type: application/json" \
  -d '{"outline": "Discuss the impact of AI on healthcare"}'
```

---

## 🔧 常见问题排查

### 问题 1: Ngrok Token 无效
```bash
# 错误信息:
# "ERR_NGROK_210 - invalid authorization token"

# 解决方案:
1. 重新检查 Token 是否复制正确
2. 确保 Token 未过期
3. 重新生成新的 Token
4. 更新 docker-compose.yml 并重启:
   docker-compose down
   docker-compose up -d
```

### 问题 2: 容器无法通信
```bash
# 错误信息:
# "connection refused" 或 "Name or service not known"

# 解决方案:
1. 检查网络连接:
   docker network ls
   docker network inspect flowernet-agent_default

2. 检查容器日志:
   docker logs flower-verifier
   docker logs flower-controller

3. 重启服务:
   docker-compose restart
```

### 问题 3: 内存不足
```bash
# 检查资源使用
docker stats

# 如果需要限制资源，在 docker-compose.yml 中添加:
services:
  verifier-app:
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
```

---

## 📊 生产环境配置检查清单

- [ ] 已注册 Ngrok 账户
- [ ] 已获取 Authtoken
- [ ] 已在 docker-compose.yml 中配置 NGROK_AUTHTOKEN
- [ ] 已运行 `docker-compose build`
- [ ] 已运行 `docker-compose up -d`
- [ ] 已验证三个容器都在运行
- [ ] 已获取 Ngrok 公网 URL
- [ ] 已测试本地 API 访问
- [ ] 已测试外网 API 访问
- [ ] 已配置日志监控

---

## 🛡️ 生产环境安全建议

### 1. 环境变量管理
```bash
# 不要在 git 中提交敏感信息
echo ".env" >> .gitignore

# 使用 GitHub Secrets 或其他密钥管理工具
# 示例: GitHub Actions
export NGROK_AUTHTOKEN=${{ secrets.NGROK_TOKEN }}
```

### 2. 日志管理
```bash
# 配置日志轮转
docker-compose logs --tail 0 -f  # 实时日志
docker logs --tail 100 flower-controller  # 查看最后100行
```

### 3. 监控和告警
```bash
# 定期检查服务状态
watch -n 5 docker-compose ps

# 或使用监控工具 (例如 Prometheus + Grafana)
docker run -d \
  --name prometheus \
  -v /path/to/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus:latest
```

### 4. 备份和恢复
```bash
# 备份模型和配置
docker exec flower-verifier tar -czf - /app > backup_verifier.tar.gz

# 恢复
docker exec -i flower-verifier tar -xzf - < backup_verifier.tar.gz
```

---

## 📈 性能优化

### 1. GPU 加速（如果可用）
```yaml
services:
  verifier-app:
    runtime: nvidia
    environment:
      - CUDA_VISIBLE_DEVICES=0
```

### 2. 缓存策略
```python
# 在 main.py 中添加缓存
from functools import lru_cache

@lru_cache(maxsize=128)
def cached_verification(draft_hash, outline_hash):
    # 缓存验证结果
    pass
```

### 3. 并发处理
```yaml
services:
  controller-app:
    deploy:
      replicas: 3  # 运行 3 个实例
```

---

## 🚨 监控脚本

创建 `monitor.sh` 用于持续监控:

```bash
#!/bin/bash

while true; do
    clear
    echo "=== FlowerNet 系统监控 $(date) ==="
    echo ""
    docker-compose ps
    echo ""
    echo "=== 资源使用 ==="
    docker stats --no-stream --format "table {{.Container}}\t{{.MemUsage}}\t{{.CPUPerc}}"
    echo ""
    echo "=== Ngrok 状态 ==="
    curl -s http://localhost:4040/api/tunnels | python3 -m json.tool
    
    sleep 30
done
```

使用:
```bash
chmod +x monitor.sh
./monitor.sh
```

---

## 📞 获取帮助

- Ngrok 文档: https://ngrok.com/docs
- Docker 文档: https://docs.docker.com/compose/
- FlowerNet Issues: [Your GitHub Repo]

