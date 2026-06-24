# FlowerNet Agent - 完整技术文档

FlowerNet 是一个面向长文档生产的多服务闭环系统。它不是一次性生成全文，而是将任务拆分为"结构规划 -> 小节生成 -> 质量验证 -> 失败修复 -> 文档组装"的工程流水线，并在每个阶段提供显式状态、可追踪数据和可调参数。

本文档是系统的完整技术参考，涵盖原理、运行流程、核心逻辑、配置策略、质量优化、指标系统、部署方法、故障排查和测试方案，便于在开发、实验、部署和论文写作中直接使用。

---

## 目录

1. [系统目标与设计哲学](#1-系统目标与设计哲学)
2. [仓库结构与服务角色](#2-仓库结构与服务角色)
3. [服务接口总览](#3-服务接口总览)
4. [端到端运行流程](#4-端到端运行流程)
5. [质量控制原理](#5-质量控制原理)
6. [引用质量优化（3阶段方案）](#6-引用质量优化3阶段方案)
7. [引证漂移问题与解决方案](#7-引证漂移问题与解决方案)
8. [指标展示系统](#8-指标展示系统)
9. [稳定性机制](#9-稳定性机制)
10. [数据与状态存储模型](#10-数据与状态存储模型)
11. [文档格式化逻辑](#11-文档格式化逻辑)
12. [配置体系](#12-配置体系)
13. [Docker运行说明](#13-docker运行说明)
14. [测试与验证体系](#14-测试与验证体系)
15. [常见问题与排查路径](#15-常见问题与排查路径)
16. [推荐调参路线](#16-推荐调参路线)
17. [论文写作指南](#17-论文写作指南)
18. [快速开始](#18-快速开始)
19. [文档维护说明](#19-文档维护说明)

---

## 1. 系统目标与设计哲学

FlowerNet 的目标不是"尽快生成一段看起来像样的文本"，而是"稳定地产出结构正确、主题对齐、低重复、可验证、可追踪、可恢复的长文档"。

核心设计哲学：

1. **先结构后正文**。先把章节和小节任务定义清楚，再逐小节写作。
2. **小节级质量门控**。每个小节都要经过验证，不通过就修，不是整篇一把梭。
3. **控制器做定向修复**。不是简单重试，而是根据失败维度改写小节大纲。
4. **工程稳定性优先**。内建超时、重试、退避、串行锁、恢复和部分返回。
5. **全程可观测**。每次生成、验证、修复都可落库，支持回放和离线评估。
6. **引用质量多层防护**。从RAG检索、LLM生成、质量验证三层锁定引用的领域相关性。

这套设计非常适合"有质量约束、有可追溯要求、有线上稳定性要求、有引用质量要求"的文档生产场景。

---

## 2. 仓库结构与服务角色

### 2.1 主要服务目录

- **flowernet-web**
  - 网关与编排入口，负责调用下游服务、超时预算、结果汇总、Markdown/DOCX 输出
  
- **flowernet-outliner**
  - 结构规划服务，负责文档标题、章节/小节结构、content prompt 生成、历史数据服务
  
- **flowernet-generator**
  - 内容生成编排服务，逐小节执行生成-验证-修复闭环
  - 包含引用质量优化的3阶段方案集成
  
- **flowernet-verifier**
  - 质量验证服务，输出相关性、冗余度、多维质量、不确定性和失败维度
  - 包含引证漂移检测和黑名单过滤
  
- **flowernet-controler**
  - 控制器服务，在验证失败时选择修复策略并改纲
  - 支持 bandit 算法进行策略选择与离线评估
  
- **flowernet-unieval**
  - 可选语义评估服务（NLI 模型），为 verifier 提供维度评分

### 2.2 辅助与运维脚本

- docker-compose.yml：本地容器化编排
- start-flowernet-full.sh、start_all_services.sh、start_services.py：服务启动
- stop-flowernet.sh、restart_services.py：停止/重启
- health-check.sh、check-system.sh：健康检查与系统诊断
- full_regression_check.py、run_2x2_full_stats.py、run_stress_2x2_3x2.py：回归与压测
- bandit_ope.py：控制器 bandit 事件离线评估

---

## 3. 服务接口总览

### 3.1 flowernet-web (端口 8010)

**端点**:
- GET /：主页
- GET /health：健康检查
- GET /api/generate-stream：流式生成接口
- GET /api/recover-document：从历史恢复文档
- POST /api/generate：完整生成接口（推荐）
- POST /api/download-docx：DOCX 下载
- POST /api/poffices/generate：POffice 任务式生成
- POST /api/poffices/task-status：POffice 任务状态
- GET /api/metrics/*：指标展示系统（8个端点）

**职责**:
1. 接收用户请求并生成 document_id
2. 调用 outliner 生成结构与 content prompts
3. 调用 generator 执行闭环生成
4. 汇总统计并组装最终 Markdown
5. 可选：输出 DOCX 下载流
6. 提供流式进度和任务式接口
7. 暴露指标 API 和仪表板

### 3.2 flowernet-outliner (端口 8003)

**端点**（20+个）:
- POST /generate-outline
- POST /generate-structure
- POST /outline/generate-and-save
- POST /outline/save / get
- POST /history/* (add/get/get-text/clear/statistics/progress)
- POST /progress/add
- POST /subsection-tracking/* (create/update/get)
- POST /passed-history/* (add/get/get-text/clear)

**职责**:
1. 生成文档结构（章节与小节）
2. 为每个小节生成写作提示
3. 保存并提供历史内容、通过内容和进度事件
4. 作为恢复流程的数据来源

### 3.3 flowernet-generator (端口 8002)

**端点**:
- GET /health
- GET /debug
- POST /generate：基础生成
- POST /generate_with_context：带上下文生成
- POST /generate_section：章节生成
- POST /generate_document：文档级生成

**职责**:
1. 调用 LLM provider 生成小节草稿
2. **融合3阶段引用质量优化**（见第6节）
3. 调用 verifier 进行质量判定
4. 失败时调用 controller 改纲
5. 维护文档级统计和小节状态

### 3.4 flowernet-verifier (端口 8000)

**端点**:
- GET /
- POST /verify

**职责**:
1. 评估相关性与冗余度
2. **实施引证漂移检测和黑名单过滤**（见第7节）
3. 产出多维质量分和失败维度
4. 输出质量权重、不确定性、阈值信息
5. 在配置要求下强制多维质量门控

### 3.5 flowernet-controler (端口 8001)

**端点**:
- GET /
- POST /refine_prompt
- POST /analyze_failures
- POST /improve-outline

**职责**:
1. 在验证失败时给出改纲方案
2. 基于上下文 bandit 选择策略臂
3. 记录事件用于离线评估

### 3.6 flowernet-unieval (端口 8004)

**端点**:
- GET /health/live
- GET /health/ready
- POST /score

**职责**:
1. 加载 NLI 模型并提供评分服务
2. 供 verifier 获取语义维度评估

---

## 4. 端到端运行流程（详细）

以 POST /api/generate 为例，完整流程如下：

### 阶段 A：请求进入网关

1. web 接收 topic、chapter_count、subsection_count、阈值和超时参数
2. web 生成 document_id
3. web 计算或更新超时预算（包含动态超时逻辑）

### 阶段 B：结构生成

1. web 调用 outliner 的 /outline/generate-and-save
2. outliner 通过 provider chain 生成结构
3. outliner 返回：document_title、structure、content_prompts
4. web 进行结构校正，确保章节数/小节数与请求一致

### 阶段 C：小节闭环生成

对每个 subsection 顺序执行：

1. **生成阶段**：generator 依据 outline + prompt + 历史构造输入
   - 调用 RAG 检索相关文献（**Stage 1：已锚定到领域**）
   - 通过 provider chain 生成 draft（**Stage 2：生成提示已强化**）
2. **验证阶段**：调用 verifier /verify
   - 检查相关性、冗余度
   - **执行黑名单检测**（Stage 3：拒绝跨领域论文）
3. **决策**：
   - 若通过：写入 passed history，进入下一个小节
   - 若失败：调用 controller /improve-outline 获取修复后的 outline
   - 进入下一轮生成尝试

**循环终止条件**:
- 小节通过
- 达到 MAX_SUBSECTION_ATTEMPTS
- 连续生成失败达到 MAX_GENERATOR_FAILURES_PER_SUBSECTION

### 阶段 D：组装与返回

1. web 从 history 与结构组装 Markdown
2. **执行引证质量检查**（可选的后处理层）
3. 根据通过率、失败情况决定返回 success 或 partial
4. 统一返回统计信息（通过/失败/强制、迭代数、质量均值、bandit 信息等）

---

## 5. 质量控制原理（Verifier + Controller）

### 5.1 三层门控

核心通过逻辑至少包含：

1. **relevancy_index** 达标（threshold：默认 0.55-0.85 配置）
2. **redundancy_index** 不超限（threshold：默认 0.70-0.75 配置）
3. **source_check** 通过（若启用引证漂移检测）

在 REQUIRE_MULTIDIM_QUALITY 为 true 时，还需要满足多维质量相关规则。

### 5.2 多维质量

常见维度：

- topic_alignment：内容主题与大纲的对齐程度
- coverage_completeness：内容对大纲要点的覆盖度
- logical_coherence：逻辑结构和连贯性
- evidence_grounding：是否有充分的引用证据
- novelty：内容新颖程度
- structure_clarity：文档组织和表达清晰度

verifier 会输出：
- quality_dimensions：各维度的评分
- quality_dimensions_failed：失败的维度列表
- quality_weights：各维度的权重
- uncertainty / confidence：信息

这些结果会被 controller 用于定向修复。

### 5.3 Controller 策略与 Contextual Bandit 强化学习机制

controller 不是简单地固定一种改纲方式，而是通过**上下文多臂老虎机（Contextual Multi-Armed Bandit）**算法动态选择最优策略。

#### 5.3.1 可选策略臂（Arms）

系统支持以下修复策略：

| 策略 | 触发条件 | 效果 | 开销 |
|------|---------|------|------|
| **llm** | 通用 | 完整重新改纲，最灵活 | 高（需LLM调用） |
| **rule** | 结构问题 | 规则驱动改纲，快速 | 低 |
| **rule_structured** | 逻辑断层 | 结构化规则改纲 | 中 |
| **defect_topic** | topic_alignment 失败 | 强化主题对齐 | 中 |
| **defect_evidence** | evidence_grounding 失败 | 增加引用证据要求 | 中 |
| **defect_structure** | structure_clarity 失败 | 重组文档逻辑结构 | 中 |

#### 5.3.2 Contextual Bandit 算法原理

**核心思想**：在有限的尝试次数内，学习哪些策略在不同的失败上下文中表现最好，同时平衡"探索（exploration）"与"利用（exploitation）"。

**上下文特征** (`context`)：
```python
context = {
    "document_topic": str,           # 文档主题
    "subsection_outline": str,       # 当前小节大纲  
    "failure_reason": str,           # 验证失败原因（e.g., "topic_alignment"）
    "quality_dimensions_failed": [str],  # 失败的多维指标列表
    "iteration_count": int,          # 已重试次数
    "provider_chain_status": str,    # 当前provider状态
    "rag_quality_score": float,      # RAG检索质量评分
}
```

**策略选择流程**：

```
1. 获取当前失败上下文 context
   ↓
2. 调用 Bandit 算法：
   a) 基于历史数据估计各策略在当前context下的期望收益 (QValue)
   b) 根据置信上界 (UCB) 计算每个策略的"乐观估计"
   c) 从中选择最高乐观估计的策略（带探索概率ε）
   ↓
3. 执行选择的策略，得到改纲方案
   ↓
4. 改纲后重新调用 Generator，获得新的生成结果
   ↓
5. 评估生成结果：
   - 相关性、冗余度是否改善？ → reward_relevancy, reward_redundancy
   - 失败维度是否被解决？  → reward_dimension_fix
   ↓
6. 记录 (context, action, reward) 事件到 bandit_events.jsonl
   ↓
7. 离线更新策略：计算该策略的收益增量 (gain)
   - 若 gain > MIN_GAIN 阈值 → 该策略被判定为"有效"
   - 否则 → 降权或快速失败
```

**多臂老虎机的关键参数**：

```bash
# Bandit 配置参数
CONTROLLER_BANDIT_ENABLED=true              # 启用 Bandit 学习
CONTROLLER_BANDIT_EVENTS_PATH=bandit_events.jsonl  # 事件日志
CONTROLLER_BANDIT_STATE_PATH=bandit_state.json     # 学到的策略权重

# 收益与约束阈值
CONTROLLER_MIN_RELEVANCY_GAIN=0.05         # 最小相关性增益
CONTROLLER_MIN_REDUNDANCY_REDUCTION=0.10   # 最小冗余度下降
CONTROLLER_MIN_DIMENSION_FIX_RATE=0.30     # 最小维度修复率
CONTROLLER_EFFECTIVENESS_THRESHOLD=0.40    # 策略有效性阈值

# 探索-利用平衡
CONTROLLER_EPSILON=0.1                     # 探索概率（10%随机尝试）
CONTROLLER_UCB_CONFIDENCE=1.96              # 置信区间系数（对应95%）

# 策略约束与降级
CONTROLLER_CONSTRAINT_MAX_LLM_CALLS=3      # 单个小节最多3次LLM改纲
CONTROLLER_CONSTRAINT_FALLBACK_STRATEGY=rule_structured  # 降级策略
```

#### 5.3.3 收益函数与强化学习信号

每次策略执行后，系统计算多维收益：

```python
reward = {
    "relevancy_improvement": float,    # 相关性提升幅度
    "redundancy_reduction": float,     # 冗余度下降幅度
    "dimension_fix_count": int,        # 修复的失败维度数
    "dimension_fix_rate": float,       # 修复率 (修复数 / 原失败数)
    "convergence_speed": float,        # 收敛速度指标 (1 / iteration)
    "provider_efficiency": float,      # Provider 调用效率
    "total_reward": float,             # 综合收益加权和
}
```

**综合收益计算**（加权求和）：

$$\text{total\_reward} = 0.3 \times \text{relevancy\_improvement} + 0.2 \times \text{redundancy\_reduction} + 0.35 \times \text{dimension\_fix\_rate} + 0.15 \times \text{convergence\_speed}$$

#### 5.3.4 离线策略评估（OPE）

系统支持基于历史事件的离线评估，通过脚本 `bandit_ope.py` 实现：

**OPE 方法**：

| 方法 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **IPS (Inverse Probability Scoring)** | $\hat{V} = \frac{1}{n} \sum \frac{\mathbb{1}[\pi(a\|s)=a_i]}{p_i} R_i$ | 无偏 | 方差大 |
| **SNIPS (Self-Normalized IPS)** | 对IPS进行自归一化 | 降低方差 | 有轻微偏差 |
| **DR (Doubly Robust)** | 结合IPS与模型估计 | 低方差 + 无偏 | 复杂度高 |

**运行离线评估**：

```bash
python bandit_ope.py \
  --events bandit_events.jsonl \
  --method DR \
  --new_policy llm \
  --output ope_result.json
```

**OPE 输出示例**：

```json
{
  "method": "DR",
  "new_policy": "llm",
  "estimated_value": 0.68,
  "confidence_interval": [0.61, 0.75],
  "comparison_to_baseline": "+0.12 (相比 rule 策略 +21%)",
  "recommendation": "llm 策略有显著改进，建议提升权重"
}
```

#### 5.3.5 事件记录与状态管理

**Bandit 事件格式** (`bandit_events.jsonl`)：

```json
{
  "timestamp": "2026-05-02T14:32:15Z",
  "document_id": "doc_12345",
  "subsection_id": "sec_1_1",
  "context": {
    "failure_reason": "topic_alignment",
    "quality_dimensions_failed": ["topic_alignment", "logical_coherence"],
    "iteration": 2,
    "rag_quality": 0.52
  },
  "action": "defect_topic",
  "reward": {
    "relevancy_improvement": 0.08,
    "dimension_fix_rate": 0.50,
    "total_reward": 0.42
  },
  "trajectory": {
    "before_verification": {...},
    "after_verification": {...}
  }
}
```

**Bandit 状态文件** (`bandit_state.json`)：

```json
{
  "update_timestamp": "2026-05-02T15:00:00Z",
  "policy_weights": {
    "llm": 0.35,
    "rule": 0.15,
    "rule_structured": 0.20,
    "defect_topic": 0.20,
    "defect_evidence": 0.10
  },
  "context_specific_qvalues": {
    "topic_alignment_failure": {
      "defect_topic": 0.68,
      "llm": 0.55,
      "rule_structured": 0.42
    },
    "evidence_grounding_failure": {
      "defect_evidence": 0.72,
      "llm": 0.64,
      "rule": 0.35
    }
  },
  "effectiveness_history": {
    "llm": {
      "total_calls": 147,
      "successful_calls": 98,
      "effectiveness_rate": 0.67,
      "average_reward": 0.52
    }
  }
}
```

#### 5.3.6 工程约束与实时决策

虽然使用Bandit算法，但系统仍受到工程约束：

```
1. 单个小节最多 MAX_CONTROLLER_RETRIES 次重试（通常3-5次）
2. 若当前策略 N 次连续失败 → 立即降级到备用策略
3. 若某策略连续有效率 < EFFECTIVENESS_THRESHOLD → 快速失败
4. 若已知失败维度与历史最优策略明确匹配 → 跳过UCB，直接使用
5. 若剩余时间预算不足 → 选择最快的策略（通常是rule）
```

**伪代码示例**：

```python
def select_strategy(context, current_iteration):
    # 约束1：检查是否已达重试上限
    if current_iteration >= MAX_CONTROLLER_RETRIES:
        return FALLBACK_STRATEGY
    
    # 约束2&3：检查策略有效性
    for strategy in all_strategies:
        if strategy.consecutive_failures >= 3:
            remove_from_consideration(strategy)
        if strategy.effectiveness_rate < THRESHOLD:
            reduce_weight(strategy)
    
    # 约束4：已知最优匹配
    if context.failure_reason in known_mappings:
        best_strategy = known_mappings[context.failure_reason]
        if random() > EPSILON:  # 以1-ε概率选择最优
            return best_strategy
    
    # 否则：使用UCB计算并选择
    ucb_scores = {}
    for strategy in feasible_strategies:
        mean_reward = compute_mean_reward(strategy, context)
        confidence_bonus = UCB_COEFFICIENT * sqrt(log(total_samples) / strategy.samples)
        ucb_scores[strategy] = mean_reward + confidence_bonus
    
    # ε-greedy 探索
    if random() < EPSILON:
        return random_choice(feasible_strategies)
    else:
        return argmax(ucb_scores)
```

---

---

## 6. 引用质量优化（3阶段方案）

### 6.1 问题背景

**原始问题**：生成器频繁引用来自不相关学科的论文
- 例子：商业/谈判话题引用"多维随机变量的性质"（多元变量论文）
- 根本原因：RAG 搜索优先考虑关键词相似度，不考虑领域相关性
- 生成器没有约束来验证引用领域与内容的匹配
- 验证器事后检测不匹配，但没有重新生成控制
- **影响**：生成的文档语义不连贯，引用可信度下降

### 6.2 三层防护方案

该方案通过三层递进式过滤消除跨领域引用幻觉，预期将跨域引用率从 45-60% 降低到 5-15%。

#### Stage 1：领域锚定（RAG 层）

**原理**：在 RAG 查询前，从大纲+提示中提取核心领域关键词，用作查询锚点。

**实现**：`flowernet-generator/flowernet_orchestrator_impl.py`

新方法：
```python
def _extract_topic_context(self, outline: str, prompt: str) -> str:
    """
    从大纲+提示中提取 3-5 个核心领域关键词。
    返回: "keyword1 keyword2 keyword3"（最多80字符）
    """
```

改进方法：
```python
def _build_rag_query_candidates(self, ...):
    """
    为每个 RAG 查询前置 [domain_context]
    格式: [核心关键词] [原始查询]
    """
    # 调用 _extract_topic_context() 获取领域锚点
    # 每个查询改为: f"[{topic_context}] {original_query}"
```

**示例对比**：
```
原始：RAG 查询 "谈判策略"
      ↓ 检索结果：[商业论文, 数学论文, 语言学论文]

优化后：RAG 查询 "[谈判 博弈 商业策略] 谈判策略"
        ↓ 检索结果：[商业论文, 商业论文, 商业论文]
```

**预期改进**：减少 70-80% 的跨域论文检索

---

#### Stage 2：证据三步检查（生成层）

**原理**：在 LLM 的系统提示中嵌入显式的 3 步工作流，强制 LLM 在引用前验证领域匹配。

**实现**：`flowernet-generator/flowernet_orchestrator_impl.py`

修改方法：
```python
def _build_enhanced_prompt(self, ..., rag_context, ...):
    """
    在生成提示中添加"优化2.0 - 引用使用的三步证据对齐工作流"部分
    """
```

**内嵌工作流**（中文提示给 LLM）：
```
【优化2.0 - 引用使用的三步证据对齐工作流（必须严格遵循）】

第1步 - 提取摘要：
  读一遍该参考资料的标题、摘要和关键内容

第2步 - 判定匹配：
  问自己："这篇资料的核心主题和我正在写的小节主题是否属于同一个大领域？"

第3步 - 条件引用：
  ✓ 如果判定为"同领域" → 允许使用 [序号] 引用
  ✗ 如果判定为"跨领域" → 宁可不引用，也不强行塞入

示例：
  场景：小节 = "博弈论视角下的谈判"
  资料1 = "谈判中的博弈论应用" ✓ 同领域 → 可以引用
  资料2 = "多维随机变量的性质" ✗ 跨领域（数学) → 不引用
```

**预期改进**：防止"为了填充引用配额而强行引用不相关文献"的反模式

---

#### Stage 3：黑名单检测（验证层）

**原理**：verifier 在验证时扫描参考文献标题，检测已知的跨领域术语，拒绝不匹配的引用并触发控制器重新生成。

**实现**：`flowernet-verifier/main.py`

修改方法：
```python
def __init__(self):
    """
    初始化黑名单关键词集合：
    - _math_terms：数学论文关键词（随机变量、概率、期望等）
    - _ling_terms：语言学论文关键词（第二语言、语言习得等）
    - _negotiation_terms：谈判/商业关键词（谈判、博弈、商业等）
    """

def check_sources(self, outline: str, rag_results: List[Dict]) -> Dict:
    """
    对每个参考文献扫描黑名单：
    1. 确定大纲所属领域（例：谈判主题）
    2. 扫描源标题+正文是否包含跨领域关键词
    3. 若发现：添加到 blacklist_matches[]
    4. 若有任何匹配：passed=False, reason="blacklist_detected"
    5. 返回 trigger_controller=True 信号重新生成
    """
```

**黑名单分类**：

| 分类 | 关键词 | 触发逻辑 |
|------|--------|----------|
| **数学** | 随机变量, 多维, 概率, 期望, 方差, expectation, variance, multivariate, ... | 若数学论文被商业主题引用 → Flag |
| **语言学** | 第二语言, 语言习得, 音韵学, 形态学, L2 acquisition, ... | 若语言学论文被商业主题引用 → Flag |
| **领域特定** | 可通过 `REFERENCE_BLACKLIST_JSON` 环境变量配置 | 完全可扩展 |

**返回格式**：
```python
{
    "check_passed": False,
    "reason": "blacklist_detected",
    "blacklist_matches": [
        {
            "index": 3,
            "title": "多维随机变量的性质",
            "match_keyword": "随机变量",
            "type": "math"
        }
    ],
    "trigger_controller": True
}
```

**预期改进**：跨领域引用被拒绝，控制器改纲使生成器再次尝试

---

### 6.3 集成流程示意

```
用户请求 ("博弈论视角下的谈判")
    ↓
Stage 1: Domain Anchoring (RAG 层)
    ├─ 提取话题上下文: "谈判 博弈 商业策略"
    └─ RAG 查询: "[谈判 博弈 商业策略] 博弈论视角下的谈判"
    ↓ (检索结果已优化到领域内)
Stage 2: Evidence Check (生成层)
    ├─ LLM 接收带有 3 步工作流的提示
    └─ LLM 对每个引用执行: 提取抽象 → 判定匹配 → 条件引用
    ↓ (LLM 主动过滤不相关文献)
Stage 3: Blacklist Detection (验证层)
    ├─ Verifier 扫描引用关键词
    ├─ 若发现跨领域术语 → REJECT + trigger_controller
    └─ Controller 改纲 → Generator 重新生成
    ↓
结果: 最终文档只含领域相关引用 ✓
```

---

### 6.4 性能预期

| 指标 | 优化前 | 优化后 | 改进 |
|------|-------|-------|------|
| 跨域引用率 | 45-60% | 5-15% | -80% |
| 引用质量分 | 0.35-0.50 | 0.65-0.85 | +60-70% |
| 语义连贯度 | 0.45-0.60 | 0.75-0.90 | +50-65% |
| 平均迭代次数 | 5-8 | 3-5 | -40% |
| RAG 检索耗时 | ~1.2s | ~1.3s | +10%（可接受）|
| 生成耗时 | ~25s | ~27s | +2s（可接受）|

---

## 7. 引证漂移问题与解决方案

### 7.1 问题定义

**引证漂移（Citation Drift）**：生成的文档引用了与主题完全无关的学科论文。

**典型案例**：
- 文档主题：商业谈判（"谈判策略"）
- 引用内容：物理学论文（LaFeAsO 超导体、激光等离子体相互作用）
- **后果**：学术严谨性下降，文档可信度大幅降低

**根本原因链**：
1. RAG 检索返回语义无关的论文（关键词污染）
2. Generator 缺乏领域对齐约束（无过滤机制）
3. 无引证质量验证层（事后检测不及时）
4. 跨学科污染未被有效检测

### 7.2 三层解决方案

该方案在前面 6.2 节已详细展开。此处补充实现层面细节与 RAG 工作原理。

#### 7.2.1 RAG 系统基础与漂移根源分析

**RAG（Retrieval-Augmented Generation）工作流**：

```
┌─────────────────────────────────────────────────────────────┐
│                    用户查询                                   │
│              "谈判中的多种博弈模式"                          │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
         ┌─────────────────────────────┐
         │   1. 查询编码 (Query Encoding) │
         │   使用 BERT/BGE 编码器       │
         │   → query_embedding ∈ ℝ^768 │
         └──────────────┬───────────────┘
                        ↓
         ┌──────────────────────────────────────────┐
         │   2. 向量相似度检索                      │
         │   计算 sim(query_emb, doc_emb_i)        │
         │   使用余弦相似度或 L2 距离              │
         │   TopK 检索（通常 K=5-10）              │
         └──────────────┬───────────────────────────┘
                        ↓
         ┌──────────────────────────────────────────┐
         │   3. 检索结果排序                        │
         │   候选文献按相似度降序排列              │
         │   忽略领域匹配度                        │
         └──────────────┬───────────────────────────┘
                        ↓
         ┌──────────────────────────────────────────┐
         │   4. 生成上下文构建                      │
         │   前 K 篇论文片段 → RAG context        │
         │   传给 LLM 进行生成                    │
         └──────────────┬───────────────────────────┘
                        ↓
              ┌─────────────────┐
              │   LLM 生成输出    │
              │   包含引用号 [1] │
              │   [2] 等        │
              └─────────────────┘
```

**RAG 漂移根源**（为什么会出现不相关论文）：

| 根源 | 机制 | 示例 |
|------|------|------|
| **关键词污染** | 查询"谈判策略"匹配"策略"关键词，但源于数学论文"优化策略" | 数学论文被误排高位 |
| **语义相似度陷阱** | 嵌入空间中，"商业谈判"与"商业物理模型"相近，只因共同的"商业"词 | 物理论文混入 |
| **领域无关性** | BERT编码器是通用的，不具领域特异性，无法区分"negotiation" vs "physics negotiation" | 跨领域污染常见 |
| **检索策略单一** | 仅用余弦相似度，不考虑引用、权重、可信度等元信息 | 无法筛选掉低质引用 |
| **文献元数据缺失** | 仅编码文本，忽视论文出版来源、被引次数、期刊级别 | 野鸡期刊与顶刊无区别 |

**量化漂移情况**（真实数据）：

```
实验：生成"博弈论视角下的谈判"（topic="negotiation game theory"）

查询阶段：
  Raw Query: "谈判 博弈论 策略应用"
  ↓ 向量搜索得到 Top 10:
    [1] 商业谈判框架（相似度 0.82）✓ 相关
    [2] 合作博弈理论（相似度 0.79）✓ 相关
    [3] 随机优化方法（相似度 0.71）✗ 无关（数学）
    [4] 商业谈判成功要素（相似度 0.68）✓ 相关
    [5] 多维随机变量（相似度 0.65）✗ 无关（统计学）
    [6] 非合作博弈（相似度 0.63）✓ 相关
    [7] Laser 等离子体相互作用（相似度 0.61）✗ 无关（物理）
    ...

排序后取 Top 5 用于生成：
  [1] 商业谈判框架 ✓
  [2] 合作博弈理论 ✓
  [3] 随机优化方法 ✗ 污染！
  [4] 商业谈判成功要素 ✓
  [5] 多维随机变量 ✗ 污染！

污染率: 2/5 = 40% ← 典型数据

结果：LLM 在生成时混入不相关论文，产生语义断层
```

#### 7.2.2 完整 RAG 漂移防护体系

FlowerNet 采用**多层防护**确保检索结果的领域纯净性：

**防护第1层：检索前优化**

```python
# 在发送 RAG 查询前，进行查询增强

def augment_query_with_domain_context(original_query, document_topic, outline):
    """
    Stage 1: Domain Anchoring 的具体实现
    """
    # 从大纲提取领域关键词
    topic_context = extract_domain_keywords(document_topic, outline)
    # 格式: "domain_kw1 domain_kw2 domain_kw3"
    
    # 增强查询
    augmented_query = f"[{topic_context}] {original_query}"
    
    # 返回增强后的查询，用于向量搜索
    return augmented_query

# 示例：
# 原始查询：   "谈判策略中的合作机制"
# 提取领域词： "谈判 博弈论 商业"
# 增强后：    "[谈判 博弈论 商业] 谈判策略中的合作机制"
# 
# 效果：向量搜索时，会重点匹配含有"谈判""博弈论""商业"的论文标题
```

**防护第2层：检索后重排（Re-ranking）**

```python
def rerank_retrieved_documents(query, top_k_docs, domain_keywords):
    """
    对检索结果进行领域相关性重排
    """
    rerank_scores = []
    
    for doc in top_k_docs:
        # 原始相似度（来自向量搜索）
        retrieval_score = doc['similarity']  # 0.82, 0.79, ...
        
        # 计算领域相关性分
        domain_match_score = compute_domain_match(
            doc['title'], doc['content'], 
            domain_keywords
        )  # 0-1 范围
        
        # 综合评分：加权组合
        final_score = 0.6 * retrieval_score + 0.4 * domain_match_score
        
        rerank_scores.append({
            'doc': doc,
            'original_sim': retrieval_score,
            'domain_match': domain_match_score,
            'final_score': final_score
        })
    
    # 按综合分重排
    rerank_scores.sort(key=lambda x: x['final_score'], reverse=True)
    return rerank_scores[:len(top_k_docs)]  # 保持 K 个结果

# 示例：
# 原排序（只看相似度）：
#   [1] 随机优化（0.71） ← 误排高位
#   [2] 商业谈判（0.68）
#
# 重排后（考虑领域）：
#   [1] 商业谈判（0.71 * 0.6 + 0.95 * 0.4 = 0.8）✓ 升至首位
#   [2] 随机优化（0.71 * 0.6 + 0.05 * 0.4 = 0.48）✗ 降至末位
```

**防护第3层：LLM 层的证据对齐（Stage 2）**

```python
# 嵌入在 LLM 系统提示中的约束
EVIDENCE_ALIGNMENT_PROMPT = """
【关键要求】引用来源必须属于 "{document_domain}" 领域

在生成时，对每个参考文献执行以下3步检查：

第1步 - 识别来源领域
  阅读标题、摘要，问自己：这篇论文属于哪个学科？
  
第2步 - 检查领域匹配
  判断：该论文的领域是否与当前小节的领域相同？
  
第3步 - 条件引用
  若匹配 → 使用 [序号] 引用
  若不匹配 → 宁可不引用，也不强行使用

【示例】
场景：正在写"商业谈判"，领域="business"

✓ 通过：论文标题"Negotiation Tactics in Business Deal"
  → 领域识别：business
  → 领域匹配：是 ✓
  → 使用引用

✗ 拒绝：论文标题"Multi-dimensional Random Variables"
  → 领域识别：mathematics
  → 领域匹配：否 ✗
  → 不使用，找替代来源
"""
```

**防护第4层：Verifier 的黑名单检测（Stage 3）**

```python
def verify_citation_domain_match(generated_content, citations, outline_domain):
    """
    Verifier 执行的引用检测
    """
    blacklist_results = []
    
    for citation_idx, citation in enumerate(citations):
        # 提取论文标题和关键词
        title = citation.get('title', '')
        content = citation.get('abstract', '')
        full_text = f"{title} {content}"
        
        # 扫描黑名单关键词
        matched_keywords = scan_blacklist_keywords(
            full_text, 
            outline_domain,
            REFERENCE_BLACKLIST_JSON
        )
        
        if matched_keywords:
            # 检测到跨领域关键词
            blacklist_results.append({
                'citation_idx': citation_idx,
                'title': title,
                'matched_keywords': matched_keywords,
                'trigger_controller': True
            })
    
    return {
        'check_passed': len(blacklist_results) == 0,
        'blacklist_matches': blacklist_results,
        'recommendation': 'regenerate_without_blacklist' if blacklist_results else 'accept'
    }

# 示例：
# 生成内容包含 5 个引用
# 其中引用 [3] 标题包含"多维随机变量"，在黑名单中
# ↓
# check_passed = False
# trigger_controller = True
# 控制器改纲后重新生成，要求避免数学论文
```

**完整防护流程图**：

```
用户查询：topic_id="negotiation", outline_domain="business"
         query="谈判中的合作机制"
         
         ↓
    【防护 L1：查询增强】
    提取领域词："谈判 博弈 商业"
    增强查询："[谈判 博弈 商业] 谈判中的合作机制"
         ↓
    【防护 L2：检索 + 重排】
    向量搜索 → Top 10 文献
    domain_match 评分 → 重排
    结果：随机优化、多维变量被降权
         ↓
    【防护 L3：LLM 证据对齐】
    LLM 收到 3 步检查提示
    逐个引用进行领域判定
    主动拒绝不匹配的源
         ↓
    【防护 L4：Verifier 黑名单检测】
    扫描生成内容的引用
    若有黑名单术语 → trigger_controller
    Controller 改纲 → 重新生成
         ↓
    最终输出：只含领域相关引用 ✓
```

**配置参数控制各层**：

```bash
# RAG 层配置
RAG_TOP_K=10                                  # 检索 TopK 数量
RAG_DOMAIN_RERANK_ENABLED=true               # 启用领域重排

# Stage 1: 查询增强
DOMAIN_ANCHORING_ENABLED=true
DOMAIN_CONTEXT_KEYWORD_COUNT=5
DOMAIN_CONTEXT_MAX_LENGTH=80

# Stage 2: LLM 约束
EVIDENCE_CHECK_ENABLED=true
EVIDENCE_CHECK_INSTRUCTION_DETAIL=full       # 详细/简洁

# Stage 3: Verifier 检测
REFERENCE_BLACKLIST_ENABLED=true
REFERENCE_BLACKLIST_JSON='{...}'

# 重排调整
RAG_RERANK_DOMAIN_WEIGHT=0.40                # 领域分权重
RAG_RERANK_RECENCY_WEIGHT=0.20               # 论文时效权重
RAG_RERANK_CITATION_WEIGHT=0.20              # 被引用次数权重
```

#### 扩展方案：Citation Verifier Agent（可选后处理层）

在 web 服务的文档组装阶段，可选择添加专门的 Citation Verifier 代理进行二次过滤：

**组件** (`citation_verifier.py`，可选部署）：
```python
class DomainClassifier:
    """检测文档所属的学术领域"""
    def classify(self, document_text: str) -> str:
        # 返回: "business", "physics", "linguistics" 等

class CitationSemanticScorer:
    """计算引用与主题的语义相关性"""
    def score(self, citation_title: str, document_topic: str, domain: str) -> float:
        # 返回 0-1 的相关度分数
        # 指标: title_similarity, domain_alignment, cross_domain_risk, overall_score

class CitationVerifier:
    """主验证编排器"""
    def verify_and_rerank(self, citations: List[str], document: str) -> VerifyResult:
        # 过滤不相关的引用，重排相关度最高的源
        # 返回: 通过的引用 + 质量报告
```

**集成点**：web 服务的组装阶段（~line 1368）

```python
# 收集所有参考文献后调用验证
if CITATION_VERIFIER_ENABLED:
    verifier_result = citation_verifier.verify_and_rerank(all_citations, assembled_doc)
    # 更新最终文档的引用列表
    final_citations = verifier_result['verified_citations']
```

**测试结果**：
```
输入: 5 个引用 (2 个物理论文, 3 个商业论文) 用于 "谈判策略"
输出: 3 个引用 (仅商业相关论文)
过滤掉: LaFeAsO（物理）+ laser-plasma（物理）
精度: 100% (2/2 物理论文移除, 3/3 商业论文保留)
```

---

### 7.3 配置参数

```bash
# 启用 3 阶段优化的关键参数

# Stage 1: Domain Anchoring
DOMAIN_ANCHORING_ENABLED=true                    # 启用领域锚定
DOMAIN_CONTEXT_MAX_LENGTH=80                     # 领域关键词最大长度
DOMAIN_CONTEXT_KEYWORD_COUNT=5                   # 提取的关键词数

# Stage 2: Evidence Check Prompt
EVIDENCE_CHECK_ENABLED=true                      # 启用 3 步证据检查
EVIDENCE_CHECK_LANGUAGE=chinese                  # 提示语言

# Stage 3: Blacklist Detection
REFERENCE_BLACKLIST_ENABLED=true                 # 启用黑名单检测
REFERENCE_BLACKLIST_JSON='{"negotiation": {"math": ["随机变量"], "linguistics": ["第二语言"]}}'

# 可选后处理层
CITATION_VERIFIER_ENABLED=false                  # 后处理 Citation Verifier (可选)
CITATION_SEMANTIC_THRESHOLD=0.35                 # 引用相关度阈值
CITATION_STRICT_MODE=false                       # 严格模式（拒绝低分引用）
```

---

## 8. 指标展示系统

### 8.1 系统概述

FlowerNet 提供了 16+ 个质量指标的完整展示系统，包括 8 个新 API 端点、交互式仪表板、自动化测试脚本。

### 8.2 新增 API 端点（`/api/metrics/*`）

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/metrics/all` | GET | 获取所有 16+ 个指标定义 |
| `/api/metrics/categories` | GET | 获取 6 大分类的组织结构 |
| `/api/metrics/category/{name}` | GET | 获取指定分类的指标 |
| `/api/metrics/metric/{key}` | GET | 获取单个指标的详细信息 |
| `/api/metrics/features` | GET | 获取 6 大核心竞争优势说明 |
| `/api/metrics/dashboard-summary` | GET | 仪表板概览数据 |
| `/api/metrics/comparison` | GET | 指标对比分析表 |
| `/api/metrics/documentation` | GET | 完整使用文档 |

**实现文件**：`flowernet-web/metrics_api.py`（350+ 行）

### 8.3 核心指标概览

| 指标 | 阈值 | 分类 | 说明 |
|------|------|------|------|
| **相关性指数** | ≥ 0.75 | 内容质量 | 内容与查询的语义相似度（SBERT） |
| **冗余度指数** | ≤ 0.40 | 内容质量 | 内容重复程度（unigram+bigram+ROUGE-L） |
| **话题对齐** | 通过/不通过 | 多维质量 | 内容与大纲主题的一致性 |
| **覆盖完整性** | 通过/不通过 | 多维质量 | 内容对大纲要点的覆盖度 |
| **逻辑连贯性** | 通过/不通过 | 多维质量 | 内容逻辑结构和连贯性 |
| **证据充分性** | 通过/不通过 | 多维质量 | 是否有充分的引用证据（Stage 3 检测） |
| **新颖性** | 通过/不通过 | 多维质量 | 内容新颖程度 |
| **结构清晰度** | 通过/不通过 | 多维质量 | 文档组织和表达清晰度 |

### 8.4 系统六大核心竞争优势

API 暴露的 6 大核心特性说明：

1. **多维质量保证**
   - 从 8 个维度全面评估文档质量
   - 相比单一质量指标的系统更具鲁棒性和可信度

2. **领域感知引用过滤**
   - 通过 3 阶段方案防止跨领域污染
   - 确保引用的领域相关性和学术严谨性

3. **冗余度自动检测**
   - 自动检测和过滤重复内容
   - 确保每个引用都提供信息增量价值

4. **迭代自我完善**
   - 高效收敛到优质文档
   - 采用 Bandit 算法选择最优生成策略

5. **多源交叉验证**
   - 多个验证器协同确认
   - 提高评估的可靠性和鲁棒性

6. **不确定性量化**
   - 为每个指标提供置信度评估
   - 体现评估的可靠性和可解释性

### 8.5 交互式仪表板

**文件**：`flowernet-web/static/metrics-dashboard.html`

**功能**：
- 实时调用 API 展示所有指标
- 分类浏览、对比分析、特性展示
- 完全响应式设计（支持手机/平板/桌面）

**访问方式**：
```
http://localhost:8010/static/metrics-dashboard.html
```

### 8.6 API 响应示例

获取所有指标：
```bash
curl http://localhost:8010/api/metrics/all
```

响应结构：
```json
{
  "success": true,
  "metrics_count": 16,
  "metrics": {
    "relevancy_index": {
      "name": "相关性指数",
      "description": "度量生成内容与用户查询和文档大纲的相关程度",
      "category": "内容质量",
      "threshold": 0.75,
      "pass_criteria": "相关性 ≥ 0.75",
      "feature": "SBERT 语义相似度检测",
      "importance": "高"
    },
    ...
  }
}
```

获取仪表板概览：
```bash
curl http://localhost:8010/api/metrics/dashboard-summary
```

响应包含：
- `total_metrics`：总指标数
- `total_categories`：分类数
- `total_features`：核心特性数
- `categories`：各分类详细信息
- `features`：核心特性列表

### 8.7 测试与验证

**运行测试脚本**：
```bash
cd flowernet-web
python3 test_metrics_api.py
```

输出示例：
```
✅ 获取所有指标定义 (16 个指标)
✅ 获取指标分类 (6 个分类)
✅ 获取仪表板概览 (总计: 16 指标, 6 分类)
...
通过率: 8/8 (100%)
```

---

## 8.8 UniEval：语义维度评估服务详解

### 8.8.1 系统概述

**UniEval** 是一个可选的外部语义评估服务，集成自然语言推理（NLI）模型，为 Verifier 的多维质量评估提供深层语义判断能力。

**核心用途**：
1. 生成内容与大纲的语义对齐度评分（topic_alignment）
2. 覆盖完整性检测（是否覆盖了大纲的所有要点）
3. 逻辑连贯性判定（前后内容的因果关系是否成立）
4. 证据充分性验证（引用是否真正支撑了论点）
5. 新颖性评估（内容相对现有知识的新信息量）

### 8.8.2 NLI 模型原理

**自然语言推理（Natural Language Inference）**：
- **任务定义**：给定前提（Premise）和假设（Hypothesis），模型输出三元关系：
  - **Entailment（蕴含）**：前提充分支持假设（相关度高）
  - **Neutral（中立）**：前提与假设无必然关系（相关度中等）
  - **Contradiction（矛盾）**：前提与假设相悖（相关度低/错误）

**在 FlowerNet 中的应用**：

| 评估维度 | Premise（前提） | Hypothesis（假设） | NLI 结果映射 |
|---------|-----------------|-------------------|------------|
| topic_alignment | 生成内容 | 大纲主题 + 关键词 | Entailment → 高分, Neutral → 中分, Contradiction → 低分 |
| coverage_completeness | 生成内容 | "已涵盖大纲的点A、B、C..." | 所有点都Entail → 通过, 缺某点 → 失败 |
| logical_coherence | 当前段落 | 前一段落的结论推导而来 | Entailment → 通过, Contradiction → 失败 |
| evidence_grounding | 生成文本某论述 | 引用内容能支持该论述 | Entailment → 通过, Neutral/Contradiction → 失败 |
| novelty | 生成内容 | 与常识基础知识集有差异 | Contradiction/Neutral → 新颖, Entailment → 重复 |

### 8.8.3 架构与集成

**UniEval 服务架构**：

```
┌─────────────────────────────────────┐
│      flowernet-unieval 容器          │
├─────────────────────────────────────┤
│  FastAPI Application (端口 8004)    │
│  ├─ GET /health/live                │
│  ├─ GET /health/ready               │
│  └─ POST /score                     │
├─────────────────────────────────────┤
│  NLI 模型服务层                      │
│  ├─ 模型加载与初始化               │
│  ├─ 批量推理引擎（Batch Pipeline） │
│  └─ 缓存管理（LRU Cache）          │
├─────────────────────────────────────┤
│  预处理与后处理                      │
│  ├─ 文本规范化                      │
│  ├─ Token 长度限制                  │
│  └─ 置信度计算                      │
├─────────────────────────────────────┤
│  支持的 NLI 模型                    │
│  ├─ MNLI (英文)                    │
│  ├─ MNLI-CN (中文)                 │
│  └─ XLM-R (跨语言)                 │
└─────────────────────────────────────┘
```

**Verifier 调用 UniEval 的流程**：

```
[Verifier] 验证生成内容
    ↓
[决定是否需要深层语义判定]
    ├─ 若 REQUIRE_MULTIDIM_QUALITY = false → 跳过
    └─ 若 true → 继续
    ↓
[构建 NLI 任务对]
    (premise=生成内容, hypothesis=大纲主题/质量约束, ...)
    ↓
[HTTP POST 到 UniEval /score]
    request = {
        "premise": "...",
        "hypothesis": "...",
        "model_name": "mnli-cn",
        "return_confidence": true
    }
    ↓
[UniEval 推理]
    └─ 返回 {"entailment": 0.8, "neutral": 0.15, "contradiction": 0.05, "label": "entailment"}
    ↓
[Verifier 解释结果]
    label = "entailment" → quality_score += 0.15
    label = "neutral" → quality_score += 0.05
    label = "contradiction" → quality_score -= 0.10
    ↓
[更新质量判定]
    final_score = weighted_sum(..., nli_score)
```

### 8.8.4 配置与部署

**配置参数**：

```bash
# UniEval 基础配置
UNIEVAL_ENDPOINT=http://localhost:8004        # UniEval 服务地址
UNIEVAL_TIMEOUT=30                            # 单次调用超时（秒）
UNIEVAL_MODEL_NAME=mnli-cn                    # 使用的 NLI 模型

# 加载与初始化
UNIEVAL_LOAD_TIMEOUT_SEC=120                  # 模型首次加载超时
UNIEVAL_CACHE_SIZE=10000                      # NLI 推理结果缓存大小
UNIEVAL_BATCH_SIZE=32                         # 批量推理批次大小

# 质量门控与加权
REQUIRE_MULTIDIM_QUALITY=true                 # 启用多维质量强制
UNIEVAL_QUALITY_WEIGHT=0.25                   # NLI 维度在总分中的权重
UNIEVAL_ENTAILMENT_THRESHOLD=0.70             # Entailment 置信度阈值
UNIEVAL_CONTRADICTION_PENALTY=-0.15           # Contradiction 惩罚分

# 降级与容错
UNIEVAL_FALLBACK_MODE=soft_disable            # 不可用时的降级策略
  # "fail"：报错并停止
  # "soft_disable"：跳过NLI评估，使用其他维度
  # "use_default_score"：使用预设默认评分

UNIEVAL_RETRY_COUNT=2                         # 调用失败重试次数
```

**Docker 启动**：

```bash
docker run -d \
  --name flowernet-unieval \
  --port 8004:8004 \
  -e MODEL_NAME=mnli-cn \
  -e LOAD_TIMEOUT=120 \
  flowernet-unieval:latest
```

### 8.8.5 性能与成本分析

**推理延迟特征**：

| 操作 | 耗时 | 备注 |
|------|------|------|
| 模型首次加载 | 30-120s | 一次性成本，取决于模型大小 |
| 单个 NLI 任务推理 | 50-200ms | 依赖文本长度和硬件 |
| 批量推理（32条） | 600-1500ms | 平均 20-50ms/条 |
| HTTP 往返开销 | 5-15ms | 网络延迟 + 序列化 |

**内存使用**：

- 模型权重：~500MB (MNLI-CN)
- 缓存（10K条）：~100-200MB
- 运行时buffer：~50-100MB
- **总计**：~700-800MB

**吞吐量**：

- 单 GPU（V100）：~200-300 任务/秒
- 单 CPU（8核）：~50-80 任务/秒
- 推荐配置：GPU + 批量处理

### 8.8.6 缓存机制

**LRU 缓存策略**：

NLI 任务往往重复（例如多个小节都检查"与大纲主题对齐"），系统使用 LRU 缓存避免重复推理：

```python
class UniEvalCache:
    def __init__(self, max_size=10000):
        self.cache = OrderedDict()  # (premise, hypothesis) → result
        self.max_size = max_size
    
    def get(self, premise: str, hypothesis: str):
        key = hash_normalize(premise, hypothesis)
        if key in self.cache:
            self.cache.move_to_end(key)  # 标记最近使用
            return self.cache[key]
        return None
    
    def put(self, premise, hypothesis, result):
        key = hash_normalize(premise, hypothesis)
        self.cache[key] = result
        self.cache.move_to_end(key)
        # 若超出大小，删除最旧的项
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
```

**缓存命中率预期**：

- 小型文档（5-10小节）：30-50% 命中率
- 大型文档（50+小节）：60-80% 命中率
- 批量生成相同类型（100+文档）：75-90% 命中率

### 8.8.7 故障与降级

**当 UniEval 不可用时**：

```
UNIEVAL_FALLBACK_MODE = "soft_disable"

流程：
  1. Verifier 尝试调用 UniEval /score
  2. 超时或连接错误 → 重试 UNIEVAL_RETRY_COUNT 次
  3. 仍然失败 → 进入 fallback 模式
     a) "fail"：立即返回错误，停止生成
     b) "soft_disable"：跳过 NLI 维度，使用其他维度（relevancy, redundancy）
     c) "use_default_score"：赋予默认评分 0.50

  4. 日志记录 WARNING："UniEval unavailable, using fallback mode"
  5. 继续执行（不中断文档生成）
```

**监控与告警**：

```bash
# 监控脚本检查 UniEval 状态
curl -s http://localhost:8004/health/ready | jq .

# 预期输出（健康）：
{
  "ready": true,
  "model_loaded": true,
  "model_name": "mnli-cn",
  "last_inference_time": 85,  # ms
  "cache_hit_rate": 0.72
}

# 若非 200 状态码或 ready=false → 告警
```

### 8.8.8 案例：多维质量评估的完整流程

**场景**：生成"博弈论视角下的谈判"的第一小节，需要进行 5 个 NLI 任务：

```
[1] topic_alignment 检查
    Premise: "谈判中的博弈论应用包括合作博弈、非合作博弈等多种模式..."
    Hypothesis: "这段内容讨论的是谈判与博弈论的关系"
    → NLI: entailment (0.92)  ✓ 通过
    → dimension_score = 0.92

[2] coverage_completeness 检查
    Premise: "文段涵盖了以下要点：A.谈判定义 B.博弈论基础 C.应用案例"
    Hypothesis: "大纲要求：1.谈判定义 2.博弈论基础 3.应用案例 4.实践建议"
    → NLI: neutral (0.65, 缺 4 点)  ✗ 部分通过
    → dimension_score = 0.65
    → trigger: "content_incomplete"

[3] logical_coherence 检查
    Premise: "第一段介绍博弈论基础...第二段阐述谈判中的应用..."
    Hypothesis: "第二段逻辑上是第一段的自然推导"
    → NLI: entailment (0.88)  ✓ 通过
    → dimension_score = 0.88

[4] evidence_grounding 检查
    Premise: "生成文本中的论述：'合作博弈模式能提高谈判成功率'
    含引用[3]关于博弈论合作性的论文"
    Hypothesis: "引用[3]的内容能充分支持该论述"
    → NLI: entailment (0.85)  ✓ 通过
    → dimension_score = 0.85

[5] novelty 检查
    Premise: "生成内容：关于谈判策略的新框架..."
    Hypothesis: "这个内容与主流商业谈判教科书的标准内容不同"
    → NLI: contradiction (0.78)  ✓ 新颖
    → dimension_score = 0.78

综合评分：
  final_score = 0.4*0.92 + 0.2*0.65 + 0.2*0.88 + 0.1*0.85 + 0.1*0.78
              = 0.368 + 0.130 + 0.176 + 0.085 + 0.078
              = 0.837

结果：
  ✓ 4/5 维度通过，总分 0.837 > threshold (0.75)
  ⚠ 但 coverage_completeness 未完全通过
  → 触发 Controller 执行 "defect_structure" 改纲
  → 要求补充实践建议小节
```

---

## 9. 稳定性机制（超时、重试、降级、恢复）

### 9.1 超时机制

系统有两层超时：

1. **网关总超时**：整个文档生成的总超时预算
2. **下游服务调用超时**：单个 HTTP 调用的超时时间

支持**动态超时预算**（`TIMEOUT_ADAPTIVE_ENABLED=true`）：
- 根据近期样本估算推荐超时时间
- 结合安全系数与上下限裁剪

### 9.2 重试与退避

多个层面都有重试：

- 下游 HTTP 调用重试（可配置次数和退避策略）
- provider 调用重试（含指数退避）
- outliner/generator 流程重试（带渐进式延迟）

退避策略由以下参数共同控制：
- `BACKOFF`：初始退避系数
- `MAX_BACKOFF`：最大退避时间
- `JITTER`：随机抖动因子

### 9.3 Provider 降级链

outliner/generator 支持 provider chain，例如：
```
sensenova -> ollama -> dashscope -> openrouter
```

当某 provider 异常达到阈值时：
1. 进入 cooldown 期（不再调用）
2. 切换到下一个后备 provider
3. 支持动态恢复

### 9.4 小节失败处理

当某小节反复失败，系统会：

1. 尝试 controller 修复（改纲后重新生成）
2. 达到上限后强制收敛或标记失败
3. **尽量继续后续小节，避免整篇任务直接中断**

### 9.5 恢复机制

可通过 `/api/recover-document` 从历史重构文档，适合：
- 中断后恢复
- 审查产物
- 部分返回场景

---

## 10. 数据与状态存储模型

history_store 支持内存与 SQLite 两种模式。

### 常见数据域

1. **history**：普通历史内容
2. **outlines**：文档/章节/小节大纲
3. **subsection_tracking**：小节级状态与指标
4. **passed_history**：通过验证的小节内容
5. **progress_events**：进度事件流
6. **bandit_events**：controller bandit 策略选择事件

### 用途

这些数据用于：
- 生成上下文回填
- 失败诊断
- 任务恢复
- 前端进度展示
- 评估与分析

---

## 11. 文档格式化逻辑（当前代码）

### 11.1 Markdown 组装

web 端文档组装函数会统一输出专业化结构，支持 IEEE 风格：

1. 标题
2. Abstract
3. Index Terms
4. Contents（自动生成目录）
5. 分章节正文（章节与子章节编号）
6. References（统一格式）

当前项目已将章节与子章节编号规范化（例如 I/II 与 A/B），并在文末统一输出参考文献占位。

### 11.2 DOCX 导出

DOCX 导出来自 markdown_to_docx：

- Normal 样式统一字体与字号
- 标题样式统一间距
- 过滤锚点和分隔线等 Markdown 标记
- 保留文档结构层级

---

## 12. 配置体系（按影响力分层说明）

本项目环境变量较多，建议按以下优先级管理。

### 12.1 必配类

- 各服务 URL（OUTLINER_URL、GENERATOR_URL、VERIFIER_URL、CONTROLLER_URL）
- provider key 与 endpoint（Azure、SenseNova、DashScope 等）
- 端口 PORT

### 12.2 质量门控类

- WEB_DEFAULT_REL_THRESHOLD
- WEB_DEFAULT_RED_THRESHOLD
- QUALITY_SCORE_THRESHOLD
- REQUIRE_MULTIDIM_QUALITY
- QUALITY_DIMENSION_WEIGHTS_JSON

### 12.3 稳定性类

- REQUEST_TIMEOUT
- DOWNSTREAM_RETRIES / BACKOFF / MAX_BACKOFF / JITTER
- PROVIDER_RETRIES / BACKOFF / MAX_BACKOFF / COOLDOWN
- MAX_SUBSECTION_ATTEMPTS
- MAX_CONTROLLER_RETRIES
- MAX_GENERATOR_FAILURES_PER_SUBSECTION

### 12.4 存储与恢复类

- USE_DATABASE
- DATABASE_PATH
- USE_REMOTE_HISTORY
- HISTORY_HTTP_TIMEOUT

### 12.5 Bandit 与控制器类

- CONTROLLER_BANDIT_ENABLED
- CONTROLLER_BANDIT_EVENTS_PATH
- CONTROLLER_BANDIT_STATE_PATH
- CONTROLLER_MIN_*_GAIN
- CONTROLLER_CONSTRAINT_* 与漂移检测参数

### 12.6 引用质量优化类（新增）

- DOMAIN_ANCHORING_ENABLED
- EVIDENCE_CHECK_ENABLED
- REFERENCE_BLACKLIST_ENABLED
- REFERENCE_BLACKLIST_JSON
- CITATION_VERIFIER_ENABLED（可选后处理）

### 12.7 UniEval 类

- UNIEVAL_ENDPOINT
- UNIEVAL_TIMEOUT
- UNIEVAL_MODEL_NAME
- UNIEVAL_LOAD_TIMEOUT_SEC

---

## 13. Docker 运行说明

### 13.1 启动

```bash
docker compose up -d --build
```

### 13.2 健康检查

```bash
curl -s http://localhost:8010/health
curl -s http://localhost:8003/
curl -s http://localhost:8002/health
curl -s http://localhost:8001/
curl -s http://localhost:8000/
curl -s http://localhost:8004/health/ready
```

### 13.3 关键注意事项

1. docker-compose.yml 中 outliner-app 存在同一变量多次声明的情况（例如 USE_DATABASE），后面的值会覆盖前面的值。
2. 若启用多维质量强制门控，请确保 unieval 服务 ready。
3. provider key 未配置时，provider chain 会表现为快速降级或失败。

---

## 14. 测试与验证体系

### 14.1 功能回归

- **full_regression_check.py**
- **run_remote_full_validation.py**

用途：验证全链路可用性、字段完整性、基础成功率。

### 14.2 压力与稳定性

- **quick_pressure_test.py**
- **run_stress_2x2_3x2.py**
- **stress_async_runner.py**

用途：评估时延、超时概率、重试行为、强制通过比例。

### 14.3 统计采样

- **run_2x2_full_stats.py**

用途：输出生成统计 JSON，分析质量维度均值、controller 调用、bandit 指标。

### 14.4 Bandit 离线评估

- **bandit_ope.py**

用途：基于事件日志做 IPS/SNIPS/DR 评估。

---

## 15. 常见问题与排查路径

### 15.1 现象：大量进入 controller

**排查顺序**：

1. 阈值是否过严（rel_threshold 太高、red_threshold 太低）
2. verifier 失败维度分布是否集中于某一维
3. controller 最小收益阈值是否导致大量 ineffective

### 15.2 现象：频繁超时

**排查顺序**：

1. REQUEST_TIMEOUT 与动态预算参数
2. provider 超时与重试预算叠加是否过大
3. unieval 冷启动是否拖慢验证
4. 是否串行锁等待导致看似超时

### 15.3 现象：文档内容有但返回 partial

**可能原因**：

1. 小节通过数不足
2. 引用质量检查不通过
3. 生成过程有 forced_subsections

### 15.4 现象：引用质量差，跨域论文混入

**排查与修复**：

1. 检查是否启用了 3 阶段优化（所有 3 个 ENABLED 参数应为 true）
2. 检查 DOMAIN_ANCHORING 是否有效（查看生成日志中的 RAG 查询）
3. 检查 REFERENCE_BLACKLIST_JSON 是否正确配置
4. 若仍有问题，启用可选的 CITATION_VERIFIER_ENABLED 后处理层

### 15.5 现象：REQUIRE_MULTIDIM_QUALITY 报错

如果开启强制多维质量但 UNIEVAL_ENDPOINT 未配置或服务不可达，系统会直接报错而不是静默降级。

---

## 16. 推荐调参路线（实战）

建议按以下顺序做，不要一次同时改太多：

1. **固定 provider chain 与 key**
   - 确保 provider 链配置正确且 key 有效

2. **先稳定超时与重试**
   - 调整 REQUEST_TIMEOUT 使整体流程收敛
   - 微调 PROVIDER_RETRIES 和 BACKOFF 参数

3. **再校准 rel/red 阈值**
   - rel_threshold：从 0.55 逐渐提高到 0.85
   - red_threshold：从 0.75 逐渐降低到 0.60

4. **再校准多维阈值与权重**
   - 开启 REQUIRE_MULTIDIM_QUALITY
   - 调整 QUALITY_DIMENSION_WEIGHTS_JSON

5. **启用 3 阶段引用质量优化**
   - 逐个启用 3 个参数观察效果
   - 调整 REFERENCE_BLACKLIST_JSON 以适配特定领域

6. **最后再调 controller 最小收益与 bandit 约束参数**

**每次调参后的验证**：

至少跑一轮固定规模用例（例如 2x2、3x2），记录：
- 通过率
- 平均迭代轮次
- 强制通过比例
- 平均耗时与 P95
- controller 有效率
- 引用质量指标（新增）

---

## 17. 论文写作指南

可以从三层贡献组织你的论文：

### 17.1 方法层

**多维语义验证 + 上下文 bandit 定向修复**
- 3 阶段引用质量优化方案
- 黑名单检测机制
- Controller 策略选择算法

### 17.2 系统层

**可恢复、可观测、可部署的闭环文档生成架构**
- 小节级质量门控设计
- 多源交叉验证框架
- 稳定性和可恢复性机制

### 17.3 实证层

**回归与压测下的质量-时延-成本平衡**
- 完整的测试体系（功能/压力/统计）
- Bandit OPE 离线评估结果
- 引用质量指标的改进

### 建议报告指标

- 小节通过率、文档成功率
- 平均迭代与长尾迭代
- 时延分位数（P50/P90/P95）
- **引用质量通过率（新增）**
- **跨域引用率下降百分比（新增）**
- bandit 臂选择分布与 OPE 结果
- 不同 provider 的 fallback 比例

### 17.4 PPT Diagram Prompts (BioRender / Academic Blueprint Style)

1. **Problem Statement: Why Long-Document Generation Fails Without FlowerNet**
A clean, flat, engineering schematic diagram in academic blueprint style on a white background with blue and gray lines, high information density, no 3D rendering.
Three horizontal panels connected by bold arrows.
Panel 1 header: 'Pain Point A: Hallucination Risk', icon set: unstable AI text block + warning triangle, labels: Goal='Reduce fabricated claims', Action='Raw long-context generation without grounded retrieval', Output='Fact drift and unsupported statements'.
Panel 2 header: 'Pain Point B: Weak Structure & No Feedback', icon set: broken outline tree + isolated generation blocks, labels: Goal='Maintain section-level coherence', Action='One-pass generation with no verifier-controller loop', Output='Poor hierarchy, repetition, missing key points'.
Panel 3 header: 'Pain Point C: Low Observability', icon set: faded process pipeline + crossed magnifier + missing trace logs, labels: Goal='Trace and debug generation lifecycle', Action='No checkpoint/history/eval persistence', Output='Hard to audit, hard to recover, hard to improve'.
Add concise caption boxes: 'Hallucination', 'Poor Structure', 'No Feedback Loop', 'Untraceable Process'.
Clean sans-serif typography, subtle red accents only for risk markers.

2. **FlowerNet End-to-End Workflow Architecture: Multi-Service Closed Loop**
A professional flat vector blueprint architecture diagram on a white background, blue/gray line system, technical academic style, no 3D.
Arrange modules left-to-right with top control lane and bottom data lane.
Main flow: 'Web Interface / API Gateway' -> 'Outliner Service' -> 'Generator Service' -> 'Verifier Service' -> 'Controller Service' -> loop back to Outliner/Generator when failed -> 'Final Markdown/DOCX Output'.
Add side module 'History Store' connected to all services for passed_history, progress events, subsection tracking, recover-document flow.
Add orchestration layer labels: 'Multi-Agent Collaboration', 'LangGraph State Transitions', 'Controller Policy Selection (Contextual Bandit)'.
Add retrieval lane: Generator and Verifier call 'RAG Pipeline' which queries 'Vector DB Abstraction' and external literature sources.
Include service-level labels: timeouts, retries, fallback providers, section-level quality gate, force-pass safeguards.
Show clear arrow labels: Input, Structure Plan, Section Draft, Quality Scores, Repair Decision, Regeneration, Final Assembly.

3. **Module Logic A: Outliner + Generator + Verifier + Controller Closed-Loop**
A clean academic schematic with four connected functional panels and explicit feedback arrows, flat blueprint style, white background, blue/gray accents.
Panel 1 'Outliner': hierarchy icon, labels Goal='Create exact chapter/subsection structure', Action='Generate title, sections, content prompts, normalize counts', Output='Structured outline + subsection prompts'.
Panel 2 'Generator': document-with-gear icon, labels Goal='Section-wise grounded drafting', Action='Use prompt + history + RAG evidence + provider chain fallback', Output='Draft subsection + citation candidates'.
Panel 3 'Verifier': shield + metric chart icon, labels Goal='Quality gate per subsection', Action='Compute relevancy_index, redundancy_index, multidim quality, citation/domain checks', Output='Pass/Fail + advice + uncertainty + failed dimensions'.
Panel 4 'Controller': dual-path decision icon, labels Goal='Targeted repair strategy', Action='Rule/LLM rewrite + contextual bandit arm selection + gain threshold check', Output='Refined prompt/outline or force-pass signal'.
Add loop annotation: 'Fail -> refine -> retry (bounded iterations)'.

4. **Module Logic B: Vector DB + RAG + Reranker Retrieval Pipeline**
A flat engineering retrieval schematic on white background with blueprint line aesthetics, no artistic rendering.
Show pipeline blocks: 'Query Builder (topic context + outline anchors)' -> 'Vector DB Abstraction Layer' -> 'Retriever (dense + optional lexical)' -> 'Reranker' -> 'Top-k Evidence Context' -> 'Generator/Verifier Consumers'.
Add side blocks: 'Citation Metadata Filter', 'Domain Blacklist / Drift Prevention', 'Deduplication'.
Label critical actions: embed query, namespace routing, similarity search, rerank by relevance and citation trust, assemble grounded context window.
Show dual outputs: (1) 'Generation Context Pack' to Generator, (2) 'Evidence Cross-Check Inputs' to Verifier.
Include concise quality labels: 'higher source alignment', 'lower citation drift', 'better factual grounding'.

5. **Module Logic C: Orchestration and Evaluation Infrastructure**
A high-density academic blueprint module map in flat vector style, white background with blue/gray connections and small red risk markers only where needed.
Central orchestrator node labeled 'FlowerNet Multi-Agent Runtime'.
Around it place linked modules: 'Tool Registry' (available tools + schemas), 'MCP Bridge' (external tool protocol), 'Tool-Use Executor' (safe tool calls), 'Contextual Bandit Policy' (adaptive strategy arm selection), 'UniEval Service' (semantic dimension scoring), 'Checkpoint Store' (state snapshots for resume/recovery), 'Eval Store' (scores, traces, outcomes), and 'History/Observability Timeline'.
Add directed arrows for data/control: policy chooses strategy -> tool invocation via registry/MCP -> generation result -> UniEval and verifier metrics -> persisted to eval store/checkpoint -> feedback to policy update.
Add short labels for outputs: 'selected action', 'tool response', 'quality signal', 'recoverable state', 'offline OPE-ready logs'.
Keep typography compact and research-slide ready.

---

## 18. 快速开始（最短路径）

### 18.1 复制并配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入必配项：
# - OUTLINER_URL, GENERATOR_URL, VERIFIER_URL, CONTROLLER_URL
# - 各 provider 的 key 和 endpoint
```

### 18.2 启动服务

```bash
docker compose up -d --build
```

### 18.3 发起生成请求

```bash
curl -X POST http://localhost:8010/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "topic":"大学新生时间管理与学习习惯指南",
    "chapter_count":2,
    "subsection_count":2,
    "user_background":"大一新生",
    "extra_requirements":"包含可执行方法与案例",
    "rel_threshold":0.55,
    "red_threshold":0.70,
    "timeout_seconds":1800
  }'
```

### 18.4 查看输出并做回归

```bash
python3 full_regression_check.py
```

### 18.5 访问指标仪表板

```
http://localhost:8010/static/metrics-dashboard.html
```

---

## 19. 文档维护说明

本 README 是工程的**单一主文档**。所有文档内容都应在此维护，不应创建新的解释说明文档。

### 维护指南

若继续更新代码，建议同步更新以下内容：

1. **新增/修改 endpoint**
   - 在第 3 节"服务接口总览"中添加
   - 说明参数、功能和职责

2. **关键阈值和默认值变化**
   - 在第 12 节"配置体系"中更新
   - 记录新增的环境变量

3. **闭环流程变化**（生成、验证、修复、降级）
   - 在第 4 节"端到端运行流程"中更新
   - 如有新的优化方案，在相应节添加

4. **输出格式变化**（Markdown / DOCX）
   - 在第 11 节"文档格式化逻辑"中更新

5. **新增功能与优化**
   - 如引用质量优化（第 6 节）、引证漂移解决（第 7 节）、指标系统（第 8 节）
   - 在相应位置添加完整的说明、示例、预期改进

### 禁止事项

❌ 不再创建独立的 `.md` 或 `.txt` 解释说明文档
❌ 不再创建 QUICK_START、DEPLOYMENT_GUIDE、COMPLETION_SUMMARY 等文件
❌ 所有更新必须集中在 README.md 中

### 文档完整性检查

定期检查 README 是否涵盖：
- [ ] 所有 API 端点及其参数
- [ ] 所有关键的环境变量
- [ ] 所有优化方案的完整说明（原理 + 实现 + 预期效果）
- [ ] 所有服务的职责和流程
- [ ] 完整的快速开始和故障排查指南

---

## 附录 A：完整的 3 阶段引用优化工作流日志示例

```
[Orchestrator] 文档生成开始: "博弈论视角下的谈判"

=== Stage 1: Domain Anchoring ===
[Orchestrator] 从大纲中提取话题上下文
[Orchestrator] 话题上下文: "谈判 博弈 商业策略"
[Orchestrator] RAG 查询 1: "[谈判 博弈 商业策略] 博弈论视角下的谈判"
[RAG Search] 查询: "[谈判 博弈 商业策略] 博弈论视角下的谈判"
[RAG Search] 结果: 5 篇论文 (4 商业, 1 经济学 - 全部领域内)

=== Stage 2: Evidence Check ===
[Generator] 接收 5 个引用
[Generator] 处理引用 [1]: "谈判中的策略应用"
[Generator] 3-步检查: 提取摘要 → 判断匹配 → 引用 ✓
[Generator] 处理引用 [2]: "商业谈判框架"
[Generator] 3-步检查: 提取摘要 → 判断匹配 → 引用 ✓
[Generator] 草稿生成完成，包含 5 个引用

=== Stage 3: Blacklist Detection ===
[Verifier] 检查源...
[Verifier] 黑名单扫描: 未检测到匹配
[Verifier] 引用检查: is_passed=True, reason="all_citations_domain_matched"

=== 最终结果 ===
[Orchestrator] 文档验证成功
[Controller] 无需重新生成
[Output] 最终文档包含领域相关引用 ✅
```

---

**版本**: 1.0  
**最后更新**: 2026-05-02  
**状态**: ✅ 生产就绪

---

*本文档是 FlowerNet 系统的完整技术参考。所有后续更新应在本文档中进行，不应创建新的独立文档。*
