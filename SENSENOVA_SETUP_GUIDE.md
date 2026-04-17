# SenseNova API Key 配置指南

## 问题现象
Generator 在 Render 远程部署上连续生成失败，错误日志显示：
- `SENSENOVA_API_KEY not set`
- `Generator 失败、准备重试`（循环）

## 根本原因
render.yaml 中配置了生成服务使用 SenseNova，但标记为 `sync: false`（不从 GitHub 同步），要求在 Render Dashboard 上手动设置环境变量。**这个环境变量目前未被设置**。

## 解决步骤

### 1. 获取 SenseNova API Key
- 登录 SenseNova 官网：https://www.sensenova.cn/
- 进入 API Keys 管理页面
- 创建或复制现有的 API key

### 2. 在 Render Dashboard 上设置环境变量

对于 **Generator 服务**：
1. 访问 https://dashboard.render.com
2. 点击 **flowernet-generator** 服务
3. 进入 **Environment** 标签页
4. 添加以下环境变量：
   - `SENSENOVA_API_KEY` = 你的 SenseNova API Key
   - `GENERATOR_SENSENOVA_API_KEY` = 同样的 API Key

对于 **Outliner 服务**：
1. 点击 **flowernet-outliner** 服务
2. 进入 **Environment** 标签页
3. 添加以下环境变量：
   - `SENSENOVA_API_KEY` = 你的 SenseNova API Key
   - `OUTLINER_SENSENOVA_API_KEY` = 同样的 API Key

对于 **Verifier 服务**（如果也用 SenseNova）：
1. 点击 **flowernet-verifier** 服务
2. 进入 **Environment** 标签页
3. 添加环境变量（如有需要）

### 3. 重新部署服务
设置完成后，为每个服务触发重新部署：
1. 在服务页面点击 **Manual Deploy**
2. 等待部署完成（通常需要 2-5 分钟）

### 4. 验证生成
部署完成后，在 Web UI 中：
1. 输入文档标题和需求
2. 点击生成
3. 查看 Generator 是否成功调用 SenseNova API

## 验证 API Key 是否生效

在 Render 的 Generator 服务 Logs 中查看：
- ✅ **成功指标**: 看到类似 `prompt_tokens: 123, output_tokens: 456` 的日志
- ❌ **失败指标**: 持续显示 `SENSENOVA_API_KEY not set` 或 `401 Unauthorized`

## 便捷数据库检查（可选）

如果想查看本地历史记录中是否有 SenseNova 调用的错误，运行：

```bash
sqlite3 flowernet_history.db "SELECT message FROM progress_events WHERE message LIKE '%SENSENOVA%' OR message LIKE '%SenseNova%' ORDER BY timestamp DESC LIMIT 10"
```

## 常见问题

**Q: API Key 在哪里生成？**  
A: 在 SenseNova 官网 → API Keys 管理 → 创建新 Key

**Q: Render 上为什么要手动设置而不是从 GitHub 读？**  
A: render.yaml 中 `sync: false` 表示这个环境变量是敏感信息，不应该存储在 GitHub 上。必须在 Render Dashboard 上手动设置。

**Q: 设置后还是失败怎么办？**  
A: 
1. 检查 API Key 是否复制正确（无多余空格）
2. 检查 SenseNova 账户是否有可用的额度
3. 查看 Render 服务 Logs 中的具体错误信息

## 完整的环境变量检查清单

对于 **flowernet-generator**：
- [ ] `PORT` = 8002
- [ ] `GENERATOR_PROVIDER` = sensenova
- [ ] `GENERATOR_PROVIDER_CHAIN` = sensenova
- [ ] `GENERATOR_SENSENOVA_MODEL` = SenseNova-V6-5-Turbo
- [ ] `GENERATOR_SENSENOVA_API_URL` = https://api.sensenova.cn/v1/llm/chat-completions
- [ ] `GENERATOR_SENSENOVA_API_KEY` = (你的 API Key)
- [ ] `SENSENOVA_API_KEY` = (你的 API Key)

对于 **flowernet-outliner**：
- [ ] `PORT` = 8003
- [ ] `OUTLINER_PROVIDER` = sensenova
- [ ] `OUTLINER_PROVIDER_CHAIN` = sensenova
- [ ] `OUTLINER_SENSENOVA_MODEL` = SenseNova-V6-5-Turbo
- [ ] `OUTLINER_SENSENOVA_API_URL` = https://api.sensenova.cn/v1/llm/chat-completions
- [ ] `OUTLINER_SENSENOVA_API_KEY` = (你的 API Key)
- [ ] `SENSENOVA_API_KEY` = (你的 API Key)
