# FlowerNet Agent

FlowerNet 是一个面向长文档生产的多服务闭环系统。它不是一次性生成全文，而是将任务拆分为“结构规划 -> 小节生成 -> 质量验证 -> 失败修复 -> 文档组装”的工程流水线，并在每个阶段提供显式状态、可追踪数据和可调参数。

本 README 基于当前仓库代码编写，目标是把原理、运行流程、核心逻辑、配置策略、部署方法、故障排查和测试方案完整讲清楚，便于你在开发、实验、部署和论文写作中直接使用。

---

## 1. 系统目标与设计哲学

FlowerNet 的目标不是“尽快生成一段看起来像样的文本”，而是“稳定地产出结构正确、主题对齐、低重复、可验证、可追踪、可恢复的长文档”。

核心设计哲学：

1. 先结构后正文。先把章节和小节任务定义清楚，再逐小节写作。
2. 小节级质量门控。每个小节都要经过验证，不通过就修，不是整篇一把梭。
3. 控制器做定向修复。不是简单重试，而是根据失败维度改写小节大纲。
4. 工程稳定性优先。内建超时、重试、退避、串行锁、恢复和部分返回。
5. 全程可观测。每次生成、验证、修复都可落库，支持回放和离线评估。

这套设计非常适合“有质量约束、有可追溯要求、有线上稳定性要求”的文档生产场景。

---

## 2. 仓库结构与服务角色

主要服务目录：

- flowernet-web
  - 网关与编排入口，负责调用下游服务、超时预算、结果汇总、Markdown/DOCX 输出
- flowernet-outliner
  - 结构规划服务，负责文档标题、章节/小节结构、content prompt 生成、历史数据服务
- flowernet-generator
  - 内容生成编排服务，逐小节执行生成-验证-修复闭环
- flowernet-verifier
  - 质量验证服务，输出相关性、冗余度、多维质量、不确定性和失败维度
- flowernet-controler
  - 控制器服务，在验证失败时选择修复策略并改纲
- flowernet-unieval
  - 可选语义评估服务（NLI 模型），为 verifier 提供维度评分

辅助与运维脚本：

- docker-compose.yml：本地容器化编排
- start-flowernet-full.sh、start_all_services.sh、start_services.py：服务启动
- stop-flowernet.sh、restart_services.py：停止/重启
- health-check.sh、check-system.sh：健康检查与系统诊断
- full_regression_check.py、run_2x2_full_stats.py、run_stress_2x2_3x2.py、stress_async_runner.py：回归与压测
- bandit_ope.py：控制器 bandit 事件离线评估

---

## 3. 服务接口总览（与当前代码一致）

### 3.1 flowernet-web

- GET /
- GET /health
- GET /api/generate-stream
- GET /api/recover-document
- POST /api/generate
- POST /api/download-docx
- POST /api/poffices/generate
- POST /api/poffices/task-status

职责：

1. 接收用户请求并生成 document_id
2. 调用 outliner 生成结构与 content prompts
3. 调用 generator 执行闭环生成
4. 汇总统计并组装最终 Markdown
5. 在需要时输出 DOCX 下载流
6. 提供流式进度和任务式接口

### 3.2 flowernet-outliner

- GET /
- POST /generate-outline
- POST /generate-structure
- POST /outline/generate-and-save
- POST /outline/save
- POST /outline/get
- POST /history/add
- POST /history/get
- POST /history/get-text
- POST /history/clear
- POST /history/statistics
- POST /history/progress
- POST /progress/add
- POST /subsection-tracking/create
- POST /subsection-tracking/update
- POST /subsection-tracking/get
- POST /passed-history/add
- POST /passed-history/get
- POST /passed-history/get-text
- POST /passed-history/clear

职责：

1. 生成文档结构（章节与小节）
2. 生成每个小节的写作提示
3. 保存并提供历史内容、通过内容和进度事件
4. 作为恢复流程的数据来源

### 3.3 flowernet-generator

- GET /
- GET /health
- GET /debug
- POST /generate
- POST /generate_with_context
- POST /generate_section
- POST /generate_document

职责：

1. 调用 LLM provider 生成小节草稿
2. 调用 verifier 进行质量判定
3. 失败时调用 controller 改纲
4. 维护文档级统计和小节状态

### 3.4 flowernet-verifier

- GET /
- POST /verify

职责：

1. 评估相关性与冗余度
2. 产出多维质量分和失败维度
3. 输出质量权重、不确定性、阈值信息
4. 在配置要求下强制多维质量门控

### 3.5 flowernet-controler

- GET /
- POST /refine_prompt
- POST /analyze_failures
- POST /improve-outline

职责：

1. 在验证失败时给出改纲方案
2. 基于上下文 bandit 选择策略臂
3. 记录事件用于离线评估

### 3.6 flowernet-unieval

- GET /
- GET /health/live
- GET /health/ready
- POST /score

职责：

1. 加载 NLI 模型并提供评分服务
2. 供 verifier 获取语义维度评估

---

## 4. 端到端运行流程（详细）

以 POST /api/generate 为例：

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

1. generator 依据 outline + prompt + 历史构造输入
2. 生成 draft
3. 调用 verifier /verify
4. 若通过：写入 passed history，进入下一个小节
5. 若失败：调用 controller /improve-outline 获取修复后的 outline
6. 进入下一轮生成尝试

循环终止条件：

- 小节通过
- 达到 MAX_SUBSECTION_ATTEMPTS
- 连续生成失败达到 MAX_GENERATOR_FAILURES_PER_SUBSECTION

### 阶段 D：组装与返回

1. web 从 history 与结构组装 Markdown
2. 执行引用质量检查
3. 根据通过率、失败情况决定返回 success 或 partial
4. 统一返回统计信息（通过/失败/强制、迭代数、质量均值、bandit 信息等）

---

## 5. 质量控制原理（Verifier + Controller）

### 5.1 三层门控

核心通过逻辑至少包含：

1. relevancy_index 达标
2. redundancy_index 不超限
3. source_check 通过（若启用）

在 REQUIRE_MULTIDIM_QUALITY 为 true 时，还需要满足多维质量相关规则。

### 5.2 多维质量

常见维度：

- topic_alignment
- coverage_completeness
- logical_coherence
- evidence_grounding
- novelty
- structure_clarity

verifier 会输出：

- quality_dimensions
- quality_dimensions_failed
- quality_weights
- uncertainty / confidence 信息
- dimension_thresholds

这些结果会被 controller 用于定向修复。

### 5.3 Controller 策略与 bandit

controller 不是只会一种改纲方式，而是从多种策略中选择：

- llm
- rule
- rule_structured
- defect_topic
- defect_evidence
- defect_structure

并根据上下文特征、收益反馈、约束条件做策略更新。事件会写入 bandit 事件文件，供 OPE 离线评估。

---

## 6. 稳定性机制（超时、重试、降级、恢复）

### 6.1 超时机制

系统有两层超时：

1. 网关总超时
2. 下游服务调用超时

且支持动态超时预算（TIMEOUT_ADAPTIVE_ENABLED=true），根据近期样本估算推荐超时时间，再结合安全系数与上下限裁剪。

### 6.2 重试与退避

多个层面都有重试：

- 下游 HTTP 调用重试
- provider 调用重试
- outliner/generator 流程重试

退避策略一般由：

- BACKOFF
- MAX_BACKOFF
- JITTER

共同控制，避免瞬时放大故障。

### 6.3 provider 降级链

outliner/generator 支持 provider chain，例如：

- sensenova -> ollama -> dashscope

当某 provider 异常达到阈值时，可进入 cooldown 并切换后备 provider。

### 6.4 小节失败处理

当某小节反复失败，系统会：

1. 尝试 controller 修复
2. 达到上限后强制收敛或标记失败
3. 尽量继续后续小节，避免整篇任务直接中断

### 6.5 恢复机制

可通过 /api/recover-document 从历史重构文档，适合中断后恢复或审查产物。

---

## 7. 数据与状态存储模型

history_store 支持内存与 SQLite 两种模式。

常见数据域：

1. history：普通历史内容
2. outlines：文档/章节/小节大纲
3. subsection_tracking：小节级状态与指标
4. passed_history：通过验证的小节内容
5. progress_events：进度事件流

这些数据用于：

- 生成上下文回填
- 失败诊断
- 任务恢复
- 前端进度展示
- 评估与分析

---

## 8. 最终文档格式化逻辑（当前代码）

### 8.1 Markdown 组装

当前 web 端文档组装函数会统一输出专业化结构，并支持 IEEE 风格：

1. 标题
2. Abstract
3. Index Terms
4. Contents
5. 分章节正文（章节/子章节编号）
6. References

你当前项目已将章节与子章节编号规范化（例如 I/II 与 A/B），并在文末统一输出参考文献占位。

### 8.2 DOCX 导出

DOCX 导出来自 markdown_to_docx：

- Normal 样式统一字体与字号
- 标题样式统一间距
- 过滤锚点和分隔线等 Markdown 标记
- 保留文档结构层级

---

## 9. 配置体系（按影响力分层说明）

本项目环境变量较多，建议按以下优先级管理。

### 9.1 必配类

- 各服务 URL（OUTLINER_URL、GENERATOR_URL、VERIFIER_URL、CONTROLLER_URL）
- provider key 与 endpoint（Azure、SenseNova、DashScope 等）
- 端口 PORT

### 9.2 质量门控类

- WEB_DEFAULT_REL_THRESHOLD
- WEB_DEFAULT_RED_THRESHOLD
- QUALITY_SCORE_THRESHOLD
- REQUIRE_MULTIDIM_QUALITY
- QUALITY_DIMENSION_WEIGHTS_JSON

### 9.3 稳定性类

- REQUEST_TIMEOUT
- DOWNSTREAM_RETRIES / BACKOFF / MAX_BACKOFF / JITTER
- PROVIDER_RETRIES / BACKOFF / MAX_BACKOFF / COOLDOWN
- MAX_SUBSECTION_ATTEMPTS
- MAX_CONTROLLER_RETRIES
- MAX_GENERATOR_FAILURES_PER_SUBSECTION

### 9.4 存储与恢复类

- USE_DATABASE
- DATABASE_PATH
- USE_REMOTE_HISTORY
- HISTORY_HTTP_TIMEOUT

### 9.5 bandit 与控制器类

- CONTROLLER_BANDIT_ENABLED
- CONTROLLER_BANDIT_EVENTS_PATH
- CONTROLLER_BANDIT_STATE_PATH
- CONTROLLER_MIN_*_GAIN
- CONTROLLER_CONSTRAINT_* 与漂移检测参数

### 9.6 UniEval 类

- UNIEVAL_ENDPOINT
- UNIEVAL_TIMEOUT
- UNIEVAL_MODEL_NAME
- UNIEVAL_LOAD_TIMEOUT_SEC

---

## 10. docker-compose 运行说明

### 10.1 启动

```bash
docker compose up -d --build
```

### 10.2 健康检查

```bash
curl -s http://localhost:8010/health
curl -s http://localhost:8003/
curl -s http://localhost:8002/health
curl -s http://localhost:8001/
curl -s http://localhost:8000/
curl -s http://localhost:8004/health/ready
```

### 10.3 关键注意事项

1. docker-compose.yml 中 outliner-app 存在同一变量多次声明的情况（例如 USE_DATABASE），后面的值会覆盖前面的值。
2. 若启用多维质量强制门控，请确保 unieval 服务 ready。
3. provider key 未配置时，provider chain 会表现为快速降级或失败。

---

## 11. 本地脚本运行说明

常用命令：

```bash
python3 start_services.py
bash start_all_services.sh
bash stop-flowernet.sh
python3 restart_services.py
bash health-check.sh
bash check-system.sh
```

如果你在做论文实验，建议固定一次完整配置后使用脚本统一启动，避免手工启动造成环境漂移。

---

## 12. 测试与验证体系

### 12.1 功能回归

- full_regression_check.py
- run_remote_full_validation.py

用途：验证全链路可用性、字段完整性、基础成功率。

### 12.2 压力与稳定性

- quick_pressure_test.py
- run_stress_2x2_3x2.py
- stress_async_runner.py

用途：评估时延、超时概率、重试行为、强制通过比例。

### 12.3 统计采样

- run_2x2_full_stats.py

用途：输出生成统计 JSON，分析质量维度均值、controller 调用、bandit 指标。

### 12.4 bandit 离线评估

- bandit_ope.py

用途：基于事件日志做 IPS/SNIPS/DR 评估。

---

## 13. 常见问题与排查路径

### 13.1 现象：大量进入 controller

排查顺序：

1. 阈值是否过严（rel_threshold 太高、red_threshold 太低）
2. verifier 失败维度分布是否集中于某一维
3. controller 最小收益阈值是否导致大量 ineffective

### 13.2 现象：频繁超时

排查顺序：

1. REQUEST_TIMEOUT 与动态预算参数
2. provider 超时与重试预算叠加是否过大
3. unieval 冷启动是否拖慢验证
4. 是否串行锁等待导致看似超时

### 13.3 现象：文档内容有但返回 partial

可能原因：

1. 小节通过数不足
2. 引用质量检查不通过
3. 生成过程有 forced_subsections

### 13.4 现象：REQUIRE_MULTIDIM_QUALITY 报错

如果开启强制多维质量但 UNIEVAL_ENDPOINT 未配置或服务不可达，系统会直接报错而不是静默降级。

---

## 14. 推荐调参路线（实战）

建议按以下顺序做，不要一次同时改太多：

1. 固定 provider chain 与 key
2. 先稳定超时与重试
3. 再校准 rel/red 阈值
4. 再校准多维阈值与权重
5. 最后再调 controller 最小收益与 bandit 约束参数

每次调参后至少跑一轮固定规模用例（例如 2x2、3x2），记录：

- 通过率
- 平均迭代轮次
- 强制通过比例
- 平均耗时与 P95
- controller 有效率

---

## 15. 你可以如何把这套系统写进论文

可以从三层贡献组织：

1. 方法层：多维语义验证 + 上下文 bandit 定向修复
2. 系统层：可恢复、可观测、可部署的闭环文档生成架构
3. 实证层：回归与压测下的质量-时延-成本平衡

建议报告指标：

- 小节通过率、文档成功率
- 平均迭代与长尾迭代
- 时延分位数（P50/P90/P95）
- 引用质量通过率
- bandit 臂选择分布与 OPE 结果

---

## 16. 快速开始（最短路径）

1. 复制并配置环境变量

```bash
cp .env.example .env
```

2. 启动服务

```bash
docker compose up -d --build
```

3. 发起生成请求

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

4. 查看输出并做回归

```bash
python3 full_regression_check.py
```

---

## 17. 文档维护说明

本 README 属于工程主文档。若你继续更新代码，建议同步更新以下四类内容：

1. 新增/修改 endpoint
2. 关键阈值和默认值变化
3. 闭环流程变化（生成、验证、修复、降级）
4. 输出格式变化（Markdown / DOCX）

这样可确保“代码行为”和“使用文档”始终一致。
