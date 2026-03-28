
# FlowerNet Controller 触发率调整指南

## 目标
- Controller 触发率: **30%-50%**
- Controller 改纲成功率: **>= 80%**

## 关键参数

### 1. rel_threshold (相关性阈值)
- **含义**: Draft 必须与 Outline 相关性 >= 该值
- **范围**: 0.0 - 1.0
- **当前值**: 0.83
- **调整方向**:
  - 增加到 0.85-0.87 → 触发率 ↑ (相关性要求更严格)
  - 降低到 0.80-0.82 → 触发率 ↓ (相关性容限更大)

### 2. red_threshold (冗余度阈值)
- **含义**: Draft 的冗余度必须 <= 该值
- **范围**: 0.0 - 1.0
- **当前值**: 0.50
- **调整方向**:
  - 降低到 0.45-0.48 → 触发率 ↑ (冗余度容限更小)
  - 增加到 0.52-0.55 → 触发率 ↓ (冗余度容限更大)

## 调整步骤

### 步骤 1: 基准测试
```bash
# 启动 Ollama 测试环境
docker-compose -f docker-compose-test.yml up -d

# 拉取本地模型（首次）
docker exec flower-ollama ollama pull qwen2.5:7b

# 运行测试
python test_ollama_controller_trigger.py

# 查看结果
cat test_results.json
```

### 步骤 2: 解读结果
```
若触发率 < 30%:
  ↳ 目标: 增加触发率
  ↳ 方案 A 微调: rel_threshold 0.83→0.85, red_threshold 0.50→0.48
  ↳ 方案 B 激进: rel_threshold 0.83→0.87, red_threshold 0.50→0.45

若触发率 > 50%:
  ↳ 目标: 降低触发率
  ↳ 方案 A 微调: rel_threshold 0.83→0.82, red_threshold 0.50→0.52
  ↳ 方案 B 激进: rel_threshold 0.83→0.80, red_threshold 0.50→0.55
```

### 步骤 3: 修改代码
编辑 `flowernet-generator/flowernet_orchestrator_impl.py`:

```python
# 第 292-293 行
def _generate_and_verify_subsection(
    self,
    # ...
    rel_threshold: float = 0.83,    # ← 修改此值
    red_threshold: float = 0.50,    # ← 修改此值
) -> Dict[str, Any]:
    # ...
```

### 步骤 4: 重新测试
```bash
# 重新构建镜像
docker-compose -f docker-compose-test.yml up -d --build

# 再次运行测试
python test_ollama_controller_trigger.py

# 比较结果
diff test_results.json test_results_v2.json
```

## 预期触发率与参数关系

| rel_threshold | red_threshold | 预期触发率 | 说明 |
|---|---|---|---|
| 0.80 | 0.55 | 15-25% | 宽松要求 |
| 0.82 | 0.52 | 25-35% | 较宽松 |
| **0.83** | **0.50** | **30-40%** | **基准配置** |
| 0.85 | 0.48 | 35-45% | 较严格 |
| 0.87 | 0.45 | 45-55% | 严格要求 |

## 常见问题

### Q: 为什么改纲总是失败?
A: 
- Check Controller 的改纲算法是否能生成更好的 Outline
- 增加 Controller 重试次数 (MAX_CONTROLLER_RETRIES)
- 检查 LLM 生成能力 (考虑用更强大的模型)

### Q: 相关性和冗余度得分总是很接近?
A:
- 说明 Draft 质量中等，需要优化 Generator Prompt
- 考虑在 Prompt 中加入更多约束条件

### Q: 为什么本地 (Ollama) 和远端 (Azure) 效果不同?
A:
- Ollama (qwen2.5:7b) 能力有限，相比 GPT-4o-mini 质量较低
- 本地用 Ollama 测试是为了快速迭代，远端最终还是用 Azure

## 回溯到 Azure

当本地测试完成，触发率达到目标后:

```bash
# 恢复原始配置
mv docker-compose.yml.azure-backup docker-compose.yml

# 更新相同的阈值到 render.yaml
# 编辑: flowernet-generator/render.yaml
# 确保与代码中的参数一致

# 重新启动
docker-compose up -d --build
```

## 验证检查清单

- [ ] 本地 Ollama 测试完成
- [ ] 触发率在 30-50% 范围内
- [ ] 改纲成功率 >= 80%
- [ ] 相关性和冗余度得分分布合理
- [ ] 代码参数已提交到 Git
- [ ] render.yaml 已同步参数
- [ ] 远端 Azure 配置已验证
