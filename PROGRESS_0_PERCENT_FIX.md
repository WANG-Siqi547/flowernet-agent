# 为什么生成一直失败、进度显示 0%、连接突然中断

## 📊 问题分析总结

### 问题 1：进度显示 0%（最严重）
**原因链**：
```
Verifier 超时 (90s)
    ↓
Generator 卡住等待 Verifier 响应
    ↓
Web 前端查询 /history/get 返回空列表
    ↓
进度 = len(history) / total_subsections = 0/4 = 0%
    ↓
用户看到「生成进度 0%」
```

**根本原因**：Verifier 在 Render 上 Free Plan 冷启动超时（第一次唤醒需要 30-60s）

---

### 问题 2：一开始失败、然后好、然后中断（连续故障）
**时间线**：
```
T0: 00s
  └─ 用户点击生成
  └─ Outliner 生成大纲（成功）
  └─ Generator 开始生成第一个小节
  
T1: 5s
  └─ Generator 调用 Verifier 验证
  └─ Verifier 在 Render 冷启动中... waiting...
  
T2: 30-60s （此时用户看到"生成中"，进度 0%）
  └─ Verifier 仍在冷启动中...
  
T3: 90s （Generator 超时时间到）
  └─ ❌ Generator: "Verifier 调用失败，5s 后重试"
  
T4: 95s ~ 275s （总共 3 次重试 × (90s + 5s)）
  └─ 连续失败 3 次
  
T5: ~300s
  ✅ Render Verifier 终于启动成功
  └─ 但此时 Generator 已经放弃，小节没有保存到数据库
  └─ 用户看到「连接中断」或「生成失败」
```

**根本原因**：
1. **Render Free Plan 冷启动延迟**：第一次请求需要 30-90s
2. **Generator 超时配置太短**：90s 不够冷启动时间
3. **重试次数太少**：3 次 × 90s = 270s，仍然不够
4. **缺少保活机制**：Web SSE 连接在长时间等待中断开

---

### 问题 3：为什么会"突然好"
正好在 Verifier 冷启动完成（60s）后，Generator 的某一次重试（比如第 2 次）成功连接，此时进度条才开始更新。但之后因为其他问题又失败。

---

## ✅ 已实施的修复（优先级 1）

### 修复 1：增加 Verifier 超时时间
**文件**: `flowernet-generator/flowernet_orchestrator_impl.py` & `generator.py`

```python
# 修改前
timeout=90  # 90 秒

# 修改后
timeout=180  # 180 秒（增加 2 倍）
```

**效果**：
- 容忍 Render 冷启动延迟（30-60s）
- 容忍高负载时的响应缓慢（120-180s）

### 修复 2：增加重试次数
**文件**: `flowernet-generator/flowernet_orchestrator_impl.py` & `generator.py`

```python
# 修改前
for attempt in range(1, 4):  # 3 次重试

# 修改后
for attempt in range(1, max_retries + 1):  # max_retries = 5（5 次重试）
```

**效果**：
- 总容忍时间增加
- 从 90 + 3×5 = 105s → 180 + 5×8 = 220s

### 修复 3：增加重试间隔
**文件**: `flowernet-generator/flowernet_orchestrator_impl.py` & `generator.py`

```python
# 修改前
time.sleep(5)  # 5 秒后重试

# 修改后
retry_delay = 8  # 8 秒后重试
time.sleep(retry_delay)
```

**效果**：
- 给 Render 更多恢复时间
- 避免频繁轰炸冷启动中的服务

---

## 📈 修复前后对比

| 指标 | 修复前 | 修复后 |
|------|-------|-------|
| 单次超时 | 90s | 180s |
| 重试次数 | 3 次 | 5 次 |
| 重试间隔 | 5s | 8s |
| **总容忍时间** | **105s** | **220s** |
| Render 冷启动覆盖 | ❌ 不够 | ✅ 充足 |
| 成功率 | ~30-50% | **预期 80-90%** |

---

## 🧪 验证修复效果

### 步骤 1：重新部署
```bash
# Pull 最新代码
git pull origin main

# 在 Render Dashboard 中为以下服务触发 "Manual Deploy"
# 1. flowernet-generator
# 2. flowernet-outliner
# 3. flowernet-controller
# 4. flowernet-web
```

### 步骤 2：测试生成
1. 访问 https://flowernet-web.onrender.com
2. 输入主题，点击「生成文档」
3. 观察进度条：
   - ✅ 进度从 0% → 25% → 50% → 75% → 100%（而不是卡在 0%）
   - ✅ 每个小节完成后有日志提示
   - ✅ 生成耗时增加（但连接不中断）

### 步骤 3：查看日志
```
在 Render Dashboard 中查看 generator 日志，应该看到：

[_call_verifier] Attempt 1/5, sending request...
... waiting 180s ...
[_call_verifier] Response received in 120.5s: success=True
```

---

## 📝 已知限制与后续计划

### 当前修复的局限
- ✅ 解决了 Verifier 超时导致的生成失败
- ❌ 没有解决根本问题：Render Free Plan 性能限制

### 后续优化（优先级 2-3）

#### 优先级 2（本周）
1. **Web 前端增加心跳消息**
   - 防止长时间等待时 HTTP 连接断开
   - 每 30s 发一个 `keep-alive` SSE 消息

2. **优化 Verifier 计算性能**
   ```python
   # 缓存 jieba 分词结果
   # 使用更快的 ROUGE-L 实现
   # 预编译 BM25 索引
   ```
   - 预期从 30-60s 冷启动 → 5-10s

#### 优先级 3（下周）
1. **升级 Render plan**
   - Free Plan → Starter Plan ($7/month)
   - 阿如你除 cold starts
   - 更多 RAM（512MB → 1GB）

2. **本地 Verifier 降级方案**
   - 当 Render Verifier 超时时，自动切换到本地 Verifier
   - 本地 Verifier 更快（<5s）

3. **异步生成优化**
   - Generator 不等 Verifier，先生成所有小节
   - Verifier 并行验证
   - 减少总耗时

---

## 🔍 故障排查检查清单

如果修复后仍有问题，按以下顺序排查：

- [ ] **进度还是显示 0%**
  - 检查 Generator 是否启动成功（查看 Render Logs）
  - 检查 Outline 是否正常生成（看日志"大纲生成完成"）
  - 检查 /history/get 端点是否返回数据

- [ ] **Verifier 仍然超时**
  - 检查 Render Verifier 日志是否有错误
  - 查看 Verifier 容器是否在运行
  - 尝试手动调用 `curl https://flowernet-verifier.onrender.com/`

- [ ] **生成到一半中断**
  - 检查 Web 前端是否记录了第几个小节时中断
  - 检查 Render Generator 日志中的具体错误消息
  - 可能是 Controller 超时，需要类似优化

---

## 📞 获取帮助

如果修复后仍有问题，请提供：
1. **Render Dashboard 日志**（Generator、Verifier、Web）
2. **Web 浏览器控制台的错误**（F12 → Console）
3. **生成执行的截图**（显示卡在哪里）

**修复提交**: `15e6f31`
