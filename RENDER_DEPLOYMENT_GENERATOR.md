# FlowerNet Generator - Render 部署指南

## 部署步骤

### 1. 准备工作

确保代码已推送到 GitHub：
```bash
git add .
git commit -m "Add generator render configuration"
git push origin main
```

### 2. 创建 Render 服务

1. 登录 [Render Dashboard](https://dashboard.render.com/)
2. 点击 **New +** → **Web Service**
3. 连接你的 GitHub 仓库：`WANG-Siqi547/flowernet-agent`

### 3. 配置服务

填写以下配置：

| 配置项 | 值 |
|--------|-----|
| **Name** | `flowernet-generator` |
| **Region** | `Singapore (Southeast Asia)` 或你偏好的区域 |
| **Branch** | `main` |
| **Root Directory** | `flowernet-generator` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py 8002 gemini` |
| **Plan** | `Free` |

### 4. 设置环境变量

在 **Environment** 标签页添加：

```
GOOGLE_API_KEY=AIzaSyBfB9tUHoEl0NjtuW8nNo_AXtpBGfa0REo
```

> ⚠️ **注意**: 在生产环境中，请使用 Render 的 Secret Files 或加密环境变量功能保护 API 密钥

### 5. 部署

1. 点击 **Create Web Service**
2. Render 会自动：
   - 克隆你的 GitHub 仓库
   - 安装依赖（requirements.txt）
   - 启动服务
3. 部署完成后，你会得到一个公网 URL，格式如：
   ```
   https://flowernet-generator.onrender.com
   ```

### 6. 验证部署

部署完成后，访问以下 URL 验证：

1. **健康检查**:
   ```
   https://flowernet-generator.onrender.com/health
   ```
   应该返回：`{"status": "healthy"}`

2. **API 文档**:
   ```
   https://flowernet-generator.onrender.com/docs
   ```
   查看自动生成的 Swagger 文档

3. **测试生成**:
   ```bash
   curl -X POST https://flowernet-generator.onrender.com/generate \
     -H "Content-Type: application/json" \
     -d '{
       "prompt": "介绍人工智能的基本概念",
       "max_tokens": 1000
     }'
   ```

## 完整系统配置

部署完成后，你将拥有三个独立的公网服务：

| 服务 | URL 示例 | 端口 |
|------|---------|------|
| **Verifier** | `https://flowernet-verifier.onrender.com` | 8000 |
| **Controller** | `https://flowernet-controller.onrender.com` | 8001 |
| **Generator** | `https://flowernet-generator.onrender.com` | 8002 |

## 使用完整系统

部署完成后，更新你的客户端代码以使用 Render URL：

```python
from flowernet_client import FlowerNetClient

# 使用 Render 公网 URL
client = FlowerNetClient(
    verifier_url="https://flowernet-verifier.onrender.com",
    controller_url="https://flowernet-controller.onrender.com",
    generator_url="https://flowernet-generator.onrender.com"
)

# 生成内容
result = client.generate_with_loop(
    outline="人工智能基础",
    initial_prompt="详细介绍人工智能的定义、特点和分类",
    max_iterations=3
)

print(f"生成成功！内容长度: {len(result['draft'])} 字符")
```

## 监控和日志

1. 在 Render Dashboard 中查看：
   - **Logs**: 实时查看服务日志
   - **Metrics**: 查看 CPU、内存使用情况
   - **Events**: 查看部署历史

2. 设置健康检查：
   - Render 会自动通过 `/health` 端点检查服务状态
   - 如果服务失败，Render 会自动重启

## 常见问题

### 1. 服务启动失败

检查日志中是否有以下错误：
- `ModuleNotFoundError`: 检查 requirements.txt 是否包含所有依赖
- `API key not found`: 确保在环境变量中设置了 `GOOGLE_API_KEY`

### 2. 冷启动延迟

免费版 Render 服务在 15 分钟无活动后会休眠：
- 第一次请求可能需要 30-60 秒唤醒
- 后续请求会很快响应
- 考虑使用 Render 的付费计划避免冷启动

### 3. API 配额限制

Google Gemini 免费层限制：
- 1500 请求/天
- 如果超出，考虑：
  - 升级到 Gemini API 付费计划
  - 添加请求缓存机制
  - 实现速率限制

## 自动部署

Render 会在你推送代码到 GitHub 时自动重新部署：

```bash
# 修改代码后
git add .
git commit -m "Update generator logic"
git push origin main

# Render 会自动检测变更并重新部署
```

## 成本

使用免费计划：
- ✅ 完全免费
- ✅ 750 小时/月运行时间
- ✅ 自动 HTTPS
- ⚠️ 15 分钟无活动后休眠

如需 24/7 运行，考虑升级到 $7/月的付费计划。

## 下一步

1. ✅ 部署 Generator 到 Render
2. ✅ 获取公网 URL
3. ✅ 测试完整系统
4. 🔄 可选：设置自定义域名
5. 🔄 可选：配置 CI/CD 自动测试

完成部署后，你就拥有了一个完全云端的 FlowerNet 内容生成系统！
