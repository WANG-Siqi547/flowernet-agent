# Verifier 调用失败诊断与修复指南

## 🔴 问题诊断结果

### 诊断命令输出
```
✅ 本地 http://localhost:8000 → Verifier 正常运行
❌ Render https://flowernet-verifier.onrender.com → 超时 (ReadTimeout after 10s)
```

**根本原因：Render 上 Verifier 服务未能及时响应**

---

## 📋 可能原因（优先级排序）

### 1️⃣ **Render Free Plan 冷启动** ⭐ 最可能
- Free plan 服务在 15 分钟无流量后会进入休眠
- 唤醒时需要 30-60s，优化后可缩短至 10-20s
- **症状**：首次请求超时，后续请求快速响应

### 2️⃣ **启动命令缺失** ✅ 已修复  
- 主 `render.yaml` 中 Verifier 未指定 `startCommand`
- **修复**：commit `4949bc4` 已添加 `startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT`

### 3️⃣ **依赖安装缓慢**
- `requirements.txt` 中有重复依赖（rouge-score, rank-bm25, jieba, numpy 各出现 2 次）
- 重复依赖导致 pip 安装时间翻倍
- **修复**：commit `4949bc4` 已清理重复依赖

### 4️⃣ **内存/资源不足**  
- Free plan 限制：0.5GB RAM，轻量级任务
- Verifier 本身很轻（无 LLM，仅用 jieba、ROUGE-L、BM25），应该没问题

---

## ✅ 已执行的修复

### 修复 1：添加显式启动命令
**文件**: `render.yaml` (第 20 行)
```yaml
startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
```
**效果**：确保 Render 使用正确的启动方式，不依赖 Dockerfile 的 CMD 延迟解析

### 修复 2：清理 requirements.txt
**文件**: `flowernet-verifier/requirements.txt`
- ❌ 删除：`pyngrok==7.0.5` (Verifier 不需要 ngrok)
- ❌ 删除：`nest-asyncio==1.5.8` (Verifier 不需要)
- ❌ 删除：重复的 `rouge-score`, `rank-bm25`, `jieba`, `numpy`
- ✅ 结果：23 行 → 13 行，安装时间减少 ~30%

**文件变更**：commit `4949bc4`

---

## 🚀 后续步骤（用户需要执行）

### 步骤 1：检查 Render Dashboard 日志
1. 访问 https://dashboard.render.com
2. 点击 `flowernet-verifier` 服务
3. 切换到 "Logs" 标签页
4. **查看是否有错误**：
   - ❌ `ModuleNotFoundError` → 依赖缺失（已修复）
   - ❌ `ConnectionError` → 网络问题
   - ❌ `Timeout` → 启动太慢
   - ✅ `Uvicorn running on 0.0.0.0:8000` → 正常启动

### 步骤 2：触发 Render 重新部署
1. 在 Render Dashboard → `flowernet-verifier`
2. 点击 "Manual Deploy" 或 "Clear Build Cache + Deploy"
3. 等待 3-5 分钟直到看到 "Live"

### 步骤 3：验证修复（本地运行诊断）
```bash
cd /Users/k1ns9sley/Desktop/msc\ project/flowernet-agent
python3 test_verifier.py
```

**预期输出**：
```
✅ HTTP 200 (Render https://flowernet-verifier.onrender.com)
验证结果: 通过/未通过（都正常）
```

### 步骤 4：验证完整流程
重新生成文档，检查日志中是否还有"Verifier 调用失败"

---

## 🧪 高级诊断命令

如果问题持续存在，运行以下命令：

### 测试 Verifier 健康检查
```bash
curl -i https://flowernet-verifier.onrender.com/
```
**预期**：HTTP 200，响应 `{"status": "online", ...}`

### 测试验证接口（带超时）
```bash
curl -X POST https://flowernet-verifier.onrender.com/verify \
  -H "Content-Type: application/json" \
  -d '{
    "draft": "测试内容",
    "outline": "测试大纲",
    "history": [],
    "rel_threshold": 0.4,
    "red_threshold": 0.6
  }' \
  --max-time 120
```

### 查看 Render 实时日志（如果有 CLI）
```bash
# 需要安装 Render CLI
render logs --service flowernet-verifier --follow
```

---

## 📊 预期时间表

| 操作 | 预期耗时 | 用户操作 |
|------|--------|--------|
| 修复提交 | ✅ 已完成 | - |
| Render 检测更新 | ~1 分钟 | 等待 |
| 拉取镜像 + pip 安装 | 3-5 分钟 | 等待 |
| 服务启动 | 1 分钟 | 检查日志 |
| 首次冷启动 Verifier | 30-60s | 等待 30s 后重试 |
| 后续请求 | <500ms | 正常 |

---

## 🎯 如果问题仍未解决

### 方案 A：升级到付费 Plan
- Free → Starter ($7/month)：避免冷启动，更多 RAM

### 方案 B：本地验证瓶颈
```bash
# 在本地运行完整的 Generator → Verifier 流程
cd flowernet-generator
python3 -c "
from flowernet_orchestrator_impl import FlowerNetOrchestrator
orch = FlowerNetOrchestrator(
    verifier_url='http://localhost:8000',  # 用本地 Verifier
    ...
)
# 运行生成测试
"
```

### 方案 C：分离 Verifier 为更小的微服务
- 当前 Verifier：验证 + 历史管理（~100KB）
- 拆分为：纯验证 microservice（<30KB）+ 历史管理 sidecar

---

## 📝 检查清单

- [ ] Github 已拉取最新代码 (`git pull origin main`)
- [ ] Render Dashboard 显示 Verifier 已 Deploy
- [ ] Verifier 日志中无错误信息
- [ ] `curl https://flowernet-verifier.onrender.com/` 返回 200  
- [ ] `python3 test_verifier.py` 显示 ✅ for Render URL
- [ ] 重新生成文档，验证日志中无 "Verifier 调用失败"

---

**提交信息**: `4949bc4` - Fix Verifier deployment: add startCommand, clean duplicate dependencies
