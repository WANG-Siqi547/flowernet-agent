# 🚀 FlowerNet 部署指南 - Render.com

## 📋 部署方案总览

**平台**: Render.com  
**费用**: 完全免费  
**服务数量**: 无限制（你需要的7个或更多都可以）  
**每个服务**: 独立公网 URL + HTTPS

---

## 🎯 你将获得的 URLs

部署完成后，每个服务都有独立的公网地址：

| 服务 | 公网 URL | 功能 |
|------|---------|------|
| Verifier | `https://flowernet-verifier.onrender.com` | 文本验证 API |
| Controller | `https://flowernet-controller.onrender.com` | 流程控制 API |
| 未来服务3 | `https://flowernet-xxx.onrender.com` | ... |
| ... | ... | 可扩展至7个或更多 |

---

## 📦 一键部署步骤

### 1. 推送代码到 GitHub

```bash
cd "/Users/k1ns9sley/Desktop/msc project/flowernet-agent"

# 初始化 Git（如果还没有）
git init
git add .
git commit -m "Initial commit - FlowerNet deployment ready"

# 推送到 GitHub（替换成你的仓库地址）
git remote add origin https://github.com/你的用户名/flowernet-agent.git
git push -u origin main
```

### 2. 在 Render.com 部署

#### 2.1 部署 Verifier 服务

1. 访问 [Render Dashboard](https://dashboard.render.com/)
2. 点击 **New +** → **Web Service**
3. 连接你的 GitHub 仓库
4. 配置如下：

```yaml
Name: flowernet-verifier
Region: Singapore (或选择离你最近的)
Branch: main
Root Directory: flowernet-verifier
Environment: Docker
Plan: Free
```

5. 点击 **Create Web Service**

#### 2.2 部署 Controller 服务

重复上述步骤，配置如下：

```yaml
Name: flowernet-controller
Region: Singapore
Branch: main
Root Directory: flowernet-controler
Environment: Docker
Plan: Free
```

### 3. 等待部署完成

- 首次部署约 5-10 分钟
- 完成后会自动分配公网 URL
- 自动启用 HTTPS

---

## 🔧 环境变量配置

部署后需要更新环境变量中的实际 URL：

### Verifier 服务环境变量
在 Render Dashboard → flowernet-verifier → Environment 添加：
```
VERIFIER_PUBLIC_URL=https://flowernet-verifier.onrender.com
PORT=8000
```

### Controller 服务环境变量
在 Render Dashboard → flowernet-controller → Environment 添加：
```
CONTROLLER_PUBLIC_URL=https://flowernet-controller.onrender.com
VERIFIER_URL=https://flowernet-verifier.onrender.com
PORT=8001
```

---

## ✅ 测试部署

部署完成后测试：

```bash
# 测试 Verifier 状态
curl https://flowernet-verifier.onrender.com/

# 测试验证功能
curl -X POST https://flowernet-verifier.onrender.com/verify \
  -H "Content-Type: application/json" \
  -d '{
    "draft": "人工智能正在改变世界",
    "outline": "AI技术应用",
    "history": []
  }'

# 测试 Controller
curl https://flowernet-controller.onrender.com/
```

---

## 🎉 扩展到更多服务（第3、4、5...个）

### 方法1：手动复制

```bash
# 复制现有服务
cp -r flowernet-verifier flowernet-service3

# 修改里面的代码和配置
cd flowernet-service3
# 编辑 render.yaml，修改服务名称

# 在 Render Dashboard 重复部署步骤
```

### 方法2：使用 Blueprint（一次部署所有服务）

在项目根目录创建统一的 `render.yaml`：

```yaml
services:
  - type: web
    name: flowernet-verifier
    env: docker
    region: singapore
    plan: free
    dockerfilePath: ./flowernet-verifier/Dockerfile
    dockerContext: ./flowernet-verifier
    envVars:
      - key: PORT
        value: 8000
      - key: VERIFIER_PUBLIC_URL
        value: https://flowernet-verifier.onrender.com
    
  - type: web
    name: flowernet-controller
    env: docker
    region: singapore
    plan: free
    dockerfilePath: ./flowernet-controler/Dockerfile
    dockerContext: ./flowernet-controler
    envVars:
      - key: PORT
        value: 8001
      - key: CONTROLLER_PUBLIC_URL
        value: https://flowernet-controller.onrender.com
      - key: VERIFIER_URL
        value: https://flowernet-verifier.onrender.com
    
  # 添加第3个服务示例
  - type: web
    name: flowernet-generator
    env: docker
    region: singapore
    plan: free
    dockerfilePath: ./flowernet-generator/Dockerfile
    dockerContext: ./flowernet-generator
    envVars:
      - key: PORT
        value: 8002
      - key: GENERATOR_PUBLIC_URL
        value: https://flowernet-generator.onrender.com
```

然后在 Render Dashboard:
1. 点击 **New** → **Blueprint**
2. 连接 GitHub 仓库
3. Render 会自动检测 `render.yaml` 并一次性部署所有服务！

---

## ⚠️ 免费版限制说明

| 限制项 | 详情 | 影响 | 解决方案 |
|--------|------|------|----------|
| **休眠机制** | 15分钟无请求会休眠 | 首次请求慢 | UptimeRobot 定时 ping |
| **唤醒时间** | 休眠后首次请求需 30-50 秒 | 用户体验 | 升级到付费版（$7/月） |
| **带宽** | 100GB/月 | 通常够用 | 监控使用量 |
| **构建时长** | 无限制 | 无影响 | - |
| **实例数** | 无限制 | 无影响 | - |

### 解决休眠问题（推荐）

使用免费的 **UptimeRobot** 每 5 分钟 ping 你的服务：

1. 访问 https://uptimerobot.com
2. 注册免费账户
3. 添加监控：
   - `https://flowernet-verifier.onrender.com/`
   - `https://flowernet-controller.onrender.com/`
4. 设置间隔：5 分钟

这样服务永远不会休眠！

---

## 📊 Render Dashboard 功能

部署后你可以在控制台看到：

- ✅ **实时日志**: 查看所有请求和错误
- ✅ **性能监控**: CPU、内存、响应时间
- ✅ **自动 HTTPS**: 免费 SSL 证书
- ✅ **健康检查**: 自动重启崩溃的服务
- ✅ **版本回滚**: 一键回到历史版本
- ✅ **自定义域名**: 可绑定自己的域名（免费）

---

## 🔄 自动部署流程

配置完成后，开发流程变得超简单：

```bash
# 1. 本地修改代码
vim flowernet-verifier/verifier.py

# 2. 提交并推送
git add .
git commit -m "优化验证算法"
git push

# 3. Render 自动检测到更新
# 4. 自动构建新版本
# 5. 自动部署到生产环境
# 6. 完成！（约 3-5 分钟）
```

---

## 🌍 多服务架构示例

假设你要部署 7 个服务：

```
flowernet-agent/
├── flowernet-verifier/          → https://flowernet-verifier.onrender.com
├── flowernet-controler/         → https://flowernet-controller.onrender.com
├── flowernet-generator/         → https://flowernet-generator.onrender.com
├── flowernet-summarizer/        → https://flowernet-summarizer.onrender.com
├── flowernet-translator/        → https://flowernet-translator.onrender.com
├── flowernet-analyzer/          → https://flowernet-analyzer.onrender.com
└── flowernet-api-gateway/       → https://flowernet-api.onrender.com
```

每个都是独立的 FastAPI 服务，独立的 URL，完全免费！

---

## 💡 最佳实践

### 1. 统一配置管理

创建 `.env.production` 模板：

```bash
# 所有服务的公网 URL
VERIFIER_URL=https://flowernet-verifier.onrender.com
CONTROLLER_URL=https://flowernet-controller.onrender.com
GENERATOR_URL=https://flowernet-generator.onrender.com
# ... 更多服务
```

### 2. 健康检查端点

确保每个服务都有 `/health` 端点：

```python
@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}
```

### 3. 请求日志

在 FastAPI 中添加中间件记录所有请求：

```python
from fastapi import Request
import time

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    print(f"{request.method} {request.url.path} - {duration:.2f}s")
    return response
```

---

## 🆚 Render vs 其他平台对比

| 平台 | 免费服务数 | 独立URL | 休眠 | 推荐度 |
|------|-----------|---------|------|--------|
| **Render** | ✅ 无限 | ✅ 是 | ⚠️ 15分钟 | ⭐⭐⭐⭐⭐ |
| Railway | 3个 | ✅ 是 | ❌ 否 | ⭐⭐⭐⭐ |
| Fly.io | 3个 | ✅ 是 | ❌ 否 | ⭐⭐⭐ |
| Heroku | 需付费 | ✅ 是 | - | ⭐⭐ |
| Vercel | 无限* | ✅ 是 | ❌ 否 | ⭐⭐⭐* |

*Vercel 需要改造成无服务器函数

**结论**: 对于你的需求（7+个服务，全部免费），Render 是最佳选择！

---

## 📞 下一步行动

### 立即开始：

1. ✅ **配置文件已创建**
   - `flowernet-verifier/render.yaml`
   - `flowernet-controler/render.yaml`

2. ⏭️ **推送代码到 GitHub**
   ```bash
   git init
   git add .
   git commit -m "Ready for Render deployment"
   git push
   ```

3. ⏭️ **在 Render 创建服务**
   - 访问 https://dashboard.render.com
   - 连接 GitHub
   - 创建 2 个 Web Service

4. ⏭️ **获得公网 URL**
   - 等待 5-10 分钟
   - 收到部署完成通知
   - 开始使用！

---

## 🎁 额外福利

### 自定义域名（免费）

如果你有自己的域名（如 `flowernet.com`），可以免费绑定：

1. Render Dashboard → 服务页面 → Settings → Custom Domain
2. 添加：
   - `verifier.flowernet.com` → Verifier 服务
   - `controller.flowernet.com` → Controller 服务
3. 更新 DNS 记录（Render 会提供详细说明）
4. 等待 SSL 证书自动配置
5. 完成！

### 监控告警

Render 支持集成：
- Slack 通知
- Webhook 回调
- Email 告警

配置后，服务崩溃会自动通知你！

---

## 🆘 常见问题

**Q: 部署失败怎么办？**  
A: 查看 Render Dashboard 的 Logs，通常是依赖安装问题。

**Q: 如何查看日志？**  
A: Dashboard → 服务页面 → Logs（实时更新）

**Q: 能自动扩容吗？**  
A: 免费版不支持，升级到 $7/月可以自动扩容。

**Q: 数据会丢失吗？**  
A: 容器重启会丢失数据，建议用外部数据库（MongoDB Atlas 免费版）。

**Q: 支持 WebSocket 吗？**  
A: 支持！FastAPI 的 WebSocket 完全兼容。

---

需要我帮你：
- [ ] 创建统一的 Blueprint 配置？
- [ ] 优化 Dockerfile 减少构建时间？
- [ ] 配置 GitHub Actions 自动测试？
- [ ] 设置监控和告警？

告诉我你需要什么！
