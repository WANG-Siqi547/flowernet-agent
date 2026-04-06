# 🚀 FlowerNet 远端测试指南

## 📋 测试步骤

### 步骤 1：Render 部署
1. 在 Render Dashboard 上进入你的服务（flowernet-web, flowernet-generator, flowernet-outliner）
2. 每个服务点击 **"Manual Deploy"** 或 **"Redeploy Latest Commit"**
3. 等待部署完成（通常 2-5 分钟）

### 步骤 2：验证部署完成
```bash
# 检查所有服务是否在线
curl -m 20 https://flowernet-web.onrender.com/health
curl -m 20 https://flowernet-generator.onrender.com/health  
curl -m 20 https://flowernet-outliner.onrender.com/
```

如果都返回 200 或 JSON 响应，说明服务已就绪。

### 步骤 3：运行完整远端测试
```bash
# 从你的本地机器运行远端测试
python3 test_remote_full.py
```

## 📊 测试内容

| 检查项 | 说明 | 预期结果 |
|------|------|--------|
| **[1] Health Checks** | 检查所有服务在线 | ✅ web, generator, outliner 全部 200 |
| **[2] Module Smoke** | 测试 generator /generate 和 outliner /generate-structure | ✅ 两个模块都能生成内容 |
| **[3] E2E 生成** | 完整的 1x1 文档生成（1章1节）| ✅ 生成成功，内容 ≥800 字 |

## ⏱️ 预期耗时

- Health checks: 1 分钟
- Module smoke: 5-10 分钟
- E2E 生成: 20-40 分钟（取决于 Azure/DashScope API 延迟）
- **总计**: 30-50 分钟

## 🔧 故障排查

### 服务响应超时
```bash
# Render 免费层可能会冷启动（首次请求时启动）
# 重新尝试一次，等待服务启动
curl -m 45 https://flowernet-web.onrender.com/health
```

### 生成失败
检查 Render 环境变量是否正确设置：
- `AZURE_OPENAI_API_KEY` ✅（必需）
- `DASHSCOPE_API_KEY` ✅（必需）
- `GENERATOR_PROVIDER_CHAIN: azure,gemini,dashscope,openrouter` ✅（已修复）
- `OUTLINER_PROVIDER_CHAIN: azure,gemini,dashscope,openrouter` ✅（已修复）

### API Key 过期或不足额
如果反复失败，检查：
- Azure OpenAI 额度是否用尽
- DashScope API 是否有访问限制
- OpenRouter 是否有余额

## 📝 测试日志

测试完成后，脚本会输出 JSON 结果：
```json
{
  "overall_pass": true,
  "health_ok": true,
  "module_smoke_ok": true,
  "e2e_ok": true,
  ...
}
```

## ✅ 完成标志

所有检查通过时：
```
overall_pass: true ✅
```
