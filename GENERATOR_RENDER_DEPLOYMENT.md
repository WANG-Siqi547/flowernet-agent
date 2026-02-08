# FlowerNet Generator - Render 部署指南

完整部署 FlowerNet Generator 到 Render 云平台的步骤和配置说明。

## 快速开始

### 1. 准备工作

确保代码已推送到 GitHub：
```bash
git add .
git commit -m "Deploy generator to Render"
git push origin main
```

### 2. 创建 Render 服务

1. 登录 [Render Dashboard](https://dashboard.render.com/)
2. 点击 **New +** → **Web Service**
3. 连接 GitHub 仓库：`WANG-Siqi547/flowernet-agent`

### 3. 配置服务

| 配置项 | 值 |
|--------|-----|
| **Name** | `flowernet-generator` |
| **Region** | `Singapore (Southeast Asia)` |
| **Branch** | `main` |
| **Root Directory** | `flowernet-generator` ⚠️ 必填 |
| **Runtime** | `Docker` 或 `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | `Free` |

### 4. 设置环境变量

在 **Environment** 标签页添加（通过 Render 网站界面设置）：

| 环境变量 | 值 | 说明 |
|---------|-----|------|
| `GOOGLE_API_KEY` | `你的密钥` | 从 [Google AI Studio](https://aistudio.google.com/app/apikey) 获取 |
| `GENERATOR_PROVIDER` | `gemini` | LLM 提供商 |
| `GENERATOR_MODEL` | `models/gemini-2.5-flash` | 使用的模型 |

> ⚠️ **重要**: 
> - API 密钥请在 Render 网站手动设置，不要提交到代码仓库
> - 如果旧密钥泄露，需要重新生成新密钥

### 5. 部署

1. 点击 **Create Web Service**
2. 等待 3-5 分钟完成部署
3. 部署成功后获得公网 URL：`https://flowernet-generator.onrender.com`

## 验证部署

### 基础测试

```bash
# 1. 健康检查
curl https://flowernet-generator.onrender.com/health
# 预期: {"status":"healthy","generator_initialized":true}

# 2. 调试信息
curl https://flowernet-generator.onrender.com/debug
# 预期: {"status":"Generator initialized",...}

# 3. API 文档
open https://flowernet-generator.onrender.com/docs
```

### 功能测试

```bash
# 测试内容生成
curl -X POST https://flowernet-generator.onrender.com/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "介绍人工智能的基本概念", "max_tokens": 1000}'

# 预期返回包含生成的文本
```

## 完整系统架构

部署完成后的三个服务：

| 服务 | URL | 功能 |
|------|-----|------|
| **Verifier** | `https://flowernet-verifier.onrender.com` | 内容验证 |
| **Controller** | `https://flowernet-controller.onrender.com` | 提示词优化 |
| **Generator** | `https://flowernet-generator.onrender.com` | 内容生成 |

### 客户端使用示例

```python
from flowernet_client import FlowerNetClient

# 使用公网 URL
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
```

## 常见问题

### 1. Generator 未初始化

**症状**: `/debug` 显示 `Generator NOT initialized`

**解决方案**:
1. 检查环境变量是否正确设置
2. 查看 Render Logs 确认错误信息
3. 确保 `requirements.txt` 包含 `google-genai>=1.62.0`
4. 手动触发重新部署

### 2. API 密钥被拒绝

**症状**: `403 PERMISSION_DENIED` 或 `API key was reported as leaked`

**解决方案**:
1. 访问 [Google AI Studio](https://aistudio.google.com/app/apikey)
2. 删除旧密钥，生成新密钥
3. 在 Render Environment 中更新环境变量
4. 触发重新部署

### 3. 冷启动延迟

**症状**: 首次请求响应很慢（30-60秒）

**原因**: Render 免费计划在 15 分钟无活动后会休眠服务

**解决方案**:
- 接受免费计划的冷启动（正常现象）
- 或升级到付费计划 ($7/月) 保持 24/7 运行

### 4. 依赖安装失败

**症状**: 部署时 Build 失败

**解决方案**:
1. 检查 `requirements.txt` 格式正确
2. 确保包含所有必要依赖：
   ```
   google-genai>=1.62.0
   fastapi>=0.115.0
   uvicorn>=0.32.0
   ```
3. 查看 Build Logs 确认具体错误

## 监控和维护

### 查看日志

Render Dashboard → `flowernet-generator` → **Logs**
- 实时查看服务输出
- 排查错误和异常
- 确认启动事件是否成功

### 查看指标

Render Dashboard → `flowernet-generator` → **Metrics**
- CPU 和内存使用情况
- 请求数量和响应时间
- 服务健康状态

### 自动部署

推送代码到 GitHub 会自动触发重新部署：
```bash
git add .
git commit -m "Update generator"
git push origin main
# Render 自动检测并部署（约 3-5 分钟）
```

## 成本和限制

### 免费计划
- ✅ 完全免费
- ✅ 750 小时/月运行时间
- ✅ 自动 HTTPS
- ⚠️ 15 分钟无活动后休眠
- ⚠️ 共享 CPU 和内存

### Google Gemini 免费层
- ✅ 完全免费
- ⚠️ 1500 请求/天
- ⚠️ 每分钟速率限制

### 升级选项
- Render Starter Plan: $7/月（无休眠）
- Gemini API 付费: 按使用量计费

## 安全建议

1. **不要在代码中硬编码 API 密钥**
2. **使用 Render 的环境变量功能**
3. **定期轮换 API 密钥**
4. **监控异常请求和使用量**
5. **考虑添加访问控制（API Token）**

## 故障排查检查清单

部署失败时依次检查：

- [ ] GitHub 仓库连接正确
- [ ] Root Directory 设置为 `flowernet-generator`
- [ ] 环境变量已正确设置
- [ ] API 密钥有效且未泄露
- [ ] `requirements.txt` 包含所有依赖
- [ ] 启动命令正确
- [ ] Logs 中无明显错误

## 获取帮助

如果遇到问题：
1. 查看 Render Logs 获取详细错误信息
2. 访问 `/debug` 端点查看服务状态
3. 检查本文档的常见问题部分
4. 参考 Render 官方文档

---

部署完成后，你将拥有一个完全云端的 FlowerNet 内容生成系统！
