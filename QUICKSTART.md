# 🚀 FlowerNet 快速开始指南

## 📋 5 分钟快速部署

### 方式 1: 自动化部署（推荐）

```bash
# 1. 进入项目目录
cd /Users/k1ns9sley/Desktop/msc\ project/flowernet-agent

# 2. 运行部署脚本
./deploy.sh YOUR_NGROK_TOKEN

# 将 YOUR_NGROK_TOKEN 替换为你从 https://dashboard.ngrok.com/auth 获取的 Token
# 示例: ./deploy.sh 2Yd9YYxxxxxxxxxxxxxxxxxxxxx_xxxxxx
```

### 方式 2: 手动配置

```bash
# 1. 编辑 docker-compose.yml
nano docker-compose.yml

# 将这行:
#   - NGROK_AUTHTOKEN=你的_NGROK_TOKEN
# 改为:
#   - NGROK_AUTHTOKEN=2Yd9YYxxxxxxxxxxxxxxxxxxxxx_xxxxxx

# 2. 启动服务
docker-compose up -d

# 3. 等待初始化（约 1-2 分钟）
docker logs flower-verifier -f

# 当看到 "Uvicorn running" 时，按 Ctrl+C 退出
```

---

## ✅ 验证部署

### 检查服务状态

```bash
# 方式 1: 使用健康检查脚本
./health-check.sh

# 方式 2: 手动检查
docker-compose ps

# 预期输出:
# flower-verifier     Up (healthy)
# flower-controller   Up (healthy)
# flower-tunnel       Up
```

### 获取公网 URL

```bash
# 从日志获取 Ngrok URL
docker logs flower-tunnel | grep forwarding

# 或使用脚本自动获取
docker logs flower-tunnel 2>/dev/null | grep -oP 'https://\K[^ ]+' | head -1
```

### 测试 API

```bash
# 本地测试
curl -X POST http://localhost:8001/process \
  -H "Content-Type: application/json" \
  -d '{"outline": "Discuss the impact of AI on healthcare"}'

# 公网测试（替换 YOUR_NGROK_URL）
curl -X POST https://YOUR_NGROK_URL/process \
  -H "Content-Type: application/json" \
  -d '{"outline": "Discuss the impact of AI on healthcare"}'
```

---

## 🔧 常见操作

### 查看日志

```bash
# 实时日志
docker-compose logs -f controller-app

# 查看错误
docker-compose logs controller-app 2>&1 | grep -i error

# 查看特定容器
docker logs flower-verifier --tail 50
docker logs flower-controller --tail 50
docker logs flower-tunnel --tail 50
```

### 重启服务

```bash
# 重启所有服务
docker-compose restart

# 重启特定服务
docker-compose restart controller-app

# 重启 Ngrok（重新建立隧道）
docker-compose restart ngrok
```

### 停止和启动

```bash
# 停止所有服务（保留数据）
docker-compose stop

# 启动已停止的服务
docker-compose start

# 完全删除容器和网络（清空环境）
docker-compose down

# 删除所有数据（包括缓存）
docker-compose down -v
```

### 更新代码

```bash
# 拉取最新代码
git pull origin main

# 重新构建镜像
docker-compose build --no-cache

# 启动新版本
docker-compose up -d
```

---

## 📊 监控和调试

### 实时监控

```bash
# 方式 1: 使用 Docker stats
docker stats

# 方式 2: 使用脚本
./health-check.sh --metrics --logs

# 方式 3: 定期检查
watch -n 5 docker-compose ps
```

### 性能分析

```bash
# 查看资源使用
docker stats --no-stream

# 分析容器进程
docker top flower-controller

# 查看网络连接
docker exec flower-controller netstat -an | grep LISTEN
```

### 调试模式

```bash
# 进入容器进行调试
docker exec -it flower-controller bash

# 在容器内测试连接
curl http://verifier-app:8000/

# 查看环境变量
env | grep VERIFIER
```

---

## 🌐 Ngrok 配置

### 获取 Token

1. 访问 https://dashboard.ngrok.com/signup（注册账户）
2. 登录 https://dashboard.ngrok.com/
3. 点击 "Your Authtoken"（左侧菜单）
4. 点击 "Copy" 复制 Token
5. 使用 `deploy.sh YOUR_TOKEN` 或编辑 `docker-compose.yml`

### Ngrok 仪表板

部署后可以访问 Ngrok 仪表板查看流量和隧道信息：

```bash
http://localhost:4040
```

### 自定义 Ngrok 配置

如需高级配置（如自定义域名、IP 限制等），编辑 `docker-compose.yml`:

```yaml
ngrok:
  command:
    - "http"
    - "controller-app:8001"
    - "--region=us"  # 选择区域
    - "--log=stdout"
```

---

## 🔐 安全建议

### 生产环境检查清单

- [ ] 更新 `.env` 文件中的所有敏感信息
- [ ] 确保 `.env` 在 `.gitignore` 中
- [ ] 使用强密码保护 API（实现认证）
- [ ] 配置 CORS 限制允许的来源
- [ ] 启用 HTTPS（Ngrok 默认已启用）
- [ ] 定期查看日志检查异常
- [ ] 配置备份策略
- [ ] 监控内存和 CPU 使用

### 基本认证设置

编辑 `flowernet-controler/main.py`：

```python
from fastapi import Header, HTTPException
from typing import Optional

@app.post("/process")
async def process_task(req: GenerateRequest, x_token: Optional[str] = Header(None)):
    if x_token != os.getenv("API_TOKEN", "default-token"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # ... 继续处理
```

---

## 🆘 故障排查

### Ngrok 无法连接

```bash
# 1. 检查 Token 是否正确
docker logs flower-tunnel | grep -i "error\|invalid"

# 2. 重新生成 Token
# 访问 https://dashboard.ngrok.com/auth 重新生成 Token

# 3. 更新配置并重启
docker-compose down
# 更新 docker-compose.yml 中的 NGROK_AUTHTOKEN
docker-compose up -d
```

### Controller 无法连接 Verifier

```bash
# 1. 检查网络
docker network inspect flowernet-agent_flowernet

# 2. 测试连接
docker-compose exec controller-app curl http://verifier-app:8000/

# 3. 查看日志
docker logs flower-controller | grep -i "connection\|refused"
```

### 内存不足

```bash
# 1. 检查内存使用
docker stats

# 2. 清理无用镜像和容器
docker system prune -a

# 3. 限制容器内存（在 docker-compose.yml 中）
services:
  verifier-app:
    deploy:
      resources:
        limits:
          memory: 2G
```

---

## 📚 更多资源

- **完整部署指南**: 查看 `DEPLOYMENT.md`
- **系统测试**: 运行 `python3 test_system.py`
- **健康检查**: 运行 `./health-check.sh --detailed --logs`
- **架构说明**: 查看 `ALGORITHM_EXPLANATION.md`

---

## 🎯 下一步

### 集成 LLM

编辑 `flowernet-controler/main.py` 的 `mock_llm_generator` 函数：

```python
import openai  # 或其他 LLM 库

def real_llm_generator(prompt):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=2000
    )
    return response.choices[0].message.content
```

### 添加数据库

添加持久化存储用于保存生成历史：

```yaml
# docker-compose.yml
  postgres:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=secure_password
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### 启用监控

集成 Prometheus + Grafana：

```yaml
# docker-compose.yml
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
```

---

## 📞 支持

遇到问题？

1. 查看日志: `docker-compose logs`
2. 运行检查: `./health-check.sh --logs`
3. 查看文档: `DEPLOYMENT.md`
4. 查看源代码注释了解实现细节

---

**祝部署顺利！** 🎉
