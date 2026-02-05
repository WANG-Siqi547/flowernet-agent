# Render Generator 部署修复指南

## 问题分析

你的 Generator 服务在 Render 上显示 "Generator not initialized"。这是因为：

1. **启动方式问题**: 使用 `python main.py ...` 时，Render 上可能无法正确执行
2. **初始化时机**: `if __name__ == "__main__":` 块在使用 uvicorn 启动时不会执行

## 修复方案

已完成的修改：

### 1. 添加 FastAPI 启动事件 ✅
在 `main.py` 中添加了 `@app.on_event("startup")` 钩子，确保应用启动时自动初始化 Generator：

```python
@app.on_event("startup")
async def startup_event():
    """应用启动时初始化 Generator"""
    provider = os.getenv('GENERATOR_PROVIDER', 'gemini')
    model = os.getenv('GENERATOR_MODEL', None)
    init_generator(provider=provider, model=model)
```

### 2. 更新启动命令 ✅
将 `render.yaml` 中的启动命令从：
```bash
python main.py 8002 gemini
```

改为：
```bash
uvicorn main:app --host 0.0.0.0 --port 8002
```

### 3. 配置环境变量 ✅
在 `render.yaml` 中添加：
```yaml
envVars:
  - key: GOOGLE_API_KEY
    sync: false
  - key: GENERATOR_PROVIDER
    value: gemini
  - key: GENERATOR_MODEL
    value: models/gemini-2.5-flash
```

## 手动操作步骤（在 Render 网站）

### 1. 添加缺失的环境变量

1. 登录 [Render Dashboard](https://dashboard.render.com/)
2. 找到 `flowernet-generator` 服务
3. 点击 **Environment** 标签
4. 确保存在以下环境变量（如果没有则添加）：

| 环境变量 | 值 |
|---------|-----|
| `GOOGLE_API_KEY` | `AIzaSyBfB9tUHoEl0NjtuW8nNo_AXtpBGfa0REo` |
| `GENERATOR_PROVIDER` | `gemini` |
| `GENERATOR_MODEL` | `models/gemini-2.5-flash` |

### 2. 重新部署

修改后选择以下方式之一重新部署：

**选项 A: 自动部署（推荐）**
- Render 检测到 GitHub 推送会自动触发新部署
- 等待 3-5 分钟，部署完成后测试

**选项 B: 手动重新部署**
1. 在 Render Dashboard 中找到 `flowernet-generator`
2. 点击 **Manual Deploy** → **Deploy latest commit**
3. 等待部署完成

## 验证修复

部署完成后（等待 3-5 分钟），测试以下 URL：

```bash
# 1. 检查状态
curl https://flowernet-generator.onrender.com

# 2. 查看文档
open https://flowernet-generator.onrender.com/docs

# 3. 测试生成（关键测试）
curl -X POST https://flowernet-generator.onrender.com/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "介绍人工智能", "max_tokens": 1000}'

# 预期返回应该是 JSON 格式的 draft，而不是 "Generator not initialized"
```

## 常见问题排查

### 如果仍然显示 "Generator not initialized"

1. **检查环境变量是否正确设置**
   - 在 Render Dashboard 中验证 `GOOGLE_API_KEY` 是否存在
   - 查看日志确认启动事件是否执行

2. **查看 Render 日志**
   - 在 Render Dashboard 中点击 **Logs** 标签
   - 寻找包含 "Generator" 或错误信息的行
   - 查看是否有 `API key not found` 或其他初始化错误

3. **强制重新部署**
   - 删除服务后重新创建
   - 或在本地修改代码并推送，触发自动部署

### 如果显示 "API key not found"

1. 检查 `GOOGLE_API_KEY` 环境变量是否正确设置
2. 确保 API 密钥没有过期或被撤销
3. 验证 API 密钥格式：应该以 `AIza` 开头

### 如果显示其他错误

在 Render Logs 中查找具体错误信息，通常会包含：
- Python 错误堆栈
- 模块导入错误
- API 连接错误

## 需要的时间

部署通常需要：
- **代码推送到重新部署**: 3-5 分钟
- **第一次请求唤醒**: 30-60 秒（免费计划冷启动）
- **后续请求**: 1-3 秒

## 检查清单

部署前确认：

- [ ] `GOOGLE_API_KEY` 环境变量已设置
- [ ] `GENERATOR_PROVIDER` = `gemini`
- [ ] `GENERATOR_MODEL` = `models/gemini-2.5-flash`
- [ ] 代码已推送到 GitHub main 分支
- [ ] Render 已触发新部署（检查 Deployments 标签）

## 成功标志

当你看到以下响应时，说明修复成功：

```json
{
  "draft": "人工智能是计算机科学的一个分支...",
  "metadata": {
    "provider": "gemini",
    "tokens_used": 285,
    "finish_reason": "STOP"
  }
}
```

而不是：
```json
{"detail": "Generator not initialized"}
```

---

**需要帮助？** 如果修复后仍有问题，请分享 Render Logs 的完整输出。
