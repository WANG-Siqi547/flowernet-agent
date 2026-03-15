# 🌸 FlowerNet - AI 内容生成系统

完整的 AI 驱动内容生成系统，通过生成-验证-优化循环产生高质量内容。

## 📚 目录

- [快速开始](#快速开始)
- [系统架构](#系统架构)
- [核心算法](#核心算法)
- [本地部署](#本地部署)
- [云端部署](#云端部署)
- [API 文档](#api-文档)
- [配置指南](#配置指南)
- [故障排查](#故障排查)

---

## 🚀 快速开始

### 前置要求

- Python 3.8+
- Google Gemini API Key（免费）或 Anthropic Claude API Key

### 1. 获取 API Key

**Google Gemini（推荐，完全免费）**:
1. 访问 https://aistudio.google.com/app/apikey
2. 登录 Google 账号
3. 点击 "Create API Key"
4. 复制生成的 Key（格式：`AIza...`）

**限额**: 1500 请求/天，60 请求/分钟

### 2. 设置环境变量

```bash
# 设置 Gemini API Key
export GOOGLE_API_KEY="your-api-key-here"

# 或使用 Claude（付费）
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. 启动服务

```bash
cd flowernet-agent

# 启动所有三个服务
python3 start_services.py

# 或使用脚本
bash start-flowernet.sh
```

**服务端口**:
- Verifier: http://localhost:8000
- Controller: http://localhost:8001
- Generator: http://localhost:8002
- Outliner: http://localhost:8003

### 4. 测试生成

```python
from flowernet_client import FlowerNetClient

client = FlowerNetClient()

result = client.generate_with_loop(
    outline="人工智能基础",
    initial_prompt="详细介绍人工智能的定义、特点和应用",
    max_iterations=3
)

print(result['draft'])
```

---

## 🏗️ 系统架构

### 核心组件

```
┌─────────────────────────────────────────────────────┐
│                  FlowerNet 系统                      │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────┐      ┌───────────┐      ┌──────────┐ │
│  │Generator │ ───→ │ Verifier  │ ───→ │Controller│ │
│  │  (8002)  │      │  (8000)   │      │  (8001)  │ │
│  └──────────┘      └───────────┘      └──────────┘ │
│       │                  │                  │       │
│       │                  ▼                  │       │
│       │            ┌──────────┐             │       │
│       │            │  通过？  │             │       │
│       │            └──────────┘             │       │
│       │               ✓    ✗                │       │
│       │               │    │                │       │
│       │            [存储]  └─────[优化]─────┘       │
│       │                                             │
│       └────────────────────┬────────────────────────┘
│                            │
│                         循环直到
│                        通过或达上限
└─────────────────────────────────────────────────────┘
```

**新增组件**:
- **Outliner (8003)**: 生成文档结构大纲与 Content Prompts

### 工作流程

1. **Generator**: 使用 LLM (Gemini/Claude) 根据提示词生成内容
2. **Verifier**: 验证内容的相关性和冗余度
   - 相关性指数 (Relevancy Index)
   - 冗余度指数 (Redundancy Index)
3. **Controller**: 分析未通过原因，优化提示词
4. **循环**: 重复 1-3 直到通过或达到最大迭代次数

---

## 🧮 核心算法

### 相关性检测

**公式**:
```
Relevancy = 0.4 × 关键词覆盖率 + 0.4 × 语义相似度 + 0.2 × 主题一致性
```

**实现**:
- 关键词覆盖率: 提取大纲中的实体和名词短语，计算在生成内容中的出现比例
- 语义相似度: 使用 sentence-transformers 计算向量余弦相似度
- 主题一致性: BM25 算法计算主题匹配度

**阈值**: 默认 ≥ 0.6 通过

### 冗余度检测

**公式**:
```
Redundancy = 0.6 × 语义重复度 + 0.4 × 事实重叠度
```

**实现**:
- 语义重复度: 将新内容与历史内容向量化，计算最大相似度
- 事实重叠度: 提取实体和关键短语，计算与历史的 Jaccard 相似度

**阈值**: 默认 ≤ 0.7 通过

### 提示词优化策略

基于验证反馈自动调整：

| 问题类型 | 优化策略 |
|---------|---------|
| 相关性不足 | 添加 Entity Recall 指令 |
| 冗余度过高 | 添加 Diversity Boost 指令 |
| 内容过短 | 增加详细度要求 |
| 偏离主题 | 强化主题约束 |

---

## 💻 本地部署

### 安装依赖

```bash
# Generator
cd flowernet-generator
pip install -r requirements.txt

# Verifier
cd ../flowernet-verifier
pip install -r requirements.txt

# Controller
cd ../flowernet-controler
pip install -r requirements.txt
```

### 启动服务

**方式一: Python 脚本（推荐）**
```bash
python3 start_services.py
```

**方式二: 手动启动**
```bash
# 终端 1: Verifier
cd flowernet-verifier
python3 main.py 8000

# 终端 2: Controller
cd flowernet-controler
python3 main.py 8001

# 终端 3: Generator
cd flowernet-generator
python3 main.py 8002 gemini
```

**方式三: Docker**
```bash
docker-compose up -d
```

### 验证服务

```bash
# 检查服务状态
curl http://localhost:8000/health  # Verifier
curl http://localhost:8001/health  # Controller
curl http://localhost:8002/health  # Generator

# 测试完整流程
python3 test_flowernet_e2e.py
```

---

## ☁️ 云端部署（Render）

### Poffices 集成（公网 HTTPS + 统一入口）

如果你要在 Poffices 的 Block 中输入 `query` 后直接自动出文档，建议使用 `flowernet-web` 提供的统一入口：

- `POST /api/poffices/generate`
- `POST /api/poffices/task-status`（异步轮询）
- `POST /api/download-docx`（下载 docx）

#### 1) 暴露公网 HTTPS API

推荐使用 Render 部署 `flowernet-web`，部署后你会得到 HTTPS 域名，例如：

`https://your-flowernet-web.onrender.com`

同时配置以下环境变量：

```bash
OUTLINER_URL=https://flowernet-outliner.onrender.com
GENERATOR_URL=https://flowernet-generator.onrender.com
REQUEST_TIMEOUT=3600
DOWNSTREAM_RETRIES=3
DOWNSTREAM_BACKOFF=1.0

# 鉴权（建议开启）
API_AUTH_ENABLED=true
FLOWERNET_API_KEY=your-strong-api-key
# 或者使用 Bearer
FLOWERNET_BEARER_TOKEN=your-strong-bearer-token

# 用于返回下载地址（可选）
PUBLIC_BASE_URL=https://your-flowernet-web.onrender.com
```

#### 1.1) 推荐的“免费额度优先 + 自动容灾”策略（主 Gemini + 备 OpenRouter）

为 `flowernet-outliner` 与 `flowernet-generator` 同时配置：

```bash
# 主 + 备（自动切换）
OUTLINER_PROVIDER=gemini,openrouter
GENERATOR_PROVIDER=gemini,openrouter

# 主模型：Gemini Developer API 免费层
OUTLINER_MODEL=models/gemini-2.5-flash-lite
GENERATOR_MODEL=models/gemini-2.5-flash-lite
GOOGLE_API_KEY=你的_google_api_key

# 备模型：OpenRouter 免费模型
OUTLINER_OPENROUTER_MODEL=qwen/qwen3-32b:free
GENERATOR_OPENROUTER_MODEL=qwen/qwen3-32b:free
OPENROUTER_API_KEY=你的_openrouter_api_key

# 可选（用于 OpenRouter 控制台来源标识）
OPENROUTER_HTTP_REFERER=https://your-flowernet-web.onrender.com
OPENROUTER_APP_NAME=FlowerNet
```

工作机制：

- 优先走 Gemini 免费层；
- 遇到限流、配额不足、区域不可用或临时故障时，自动降级到 OpenRouter；
- 所有切换都在 outliner/generator 内部完成，`flowernet-web` 和 Poffices 侧无需改 API。

#### 1.1) 如果你坚持在 Render 上继续使用本地 Ollama

`flowernet-outliner` 和 `flowernet-generator` 绝对不能继续使用：

```bash
OLLAMA_URL=http://localhost:11434
```

必须先在你的电脑上把本地 Ollama 暴露为公网 HTTPS 地址，再把该地址填入 Render：

```bash
./start-ollama-ngrok.sh
python3 show_ollama_url.py
```

然后把输出的 `OLLAMA_URL=https://xxx.ngrok-free.dev` 同时配置到：

- `flowernet-outliner`
- `flowernet-generator`

项目代码已经内置了 ngrok 所需请求头，因此通过该 HTTPS 地址访问 Ollama 时不会再触发常见的浏览器警告拦截。

默认会自动启动一个本地桥接，将 `localhost(IPv6)` 转发到真正的 `127.0.0.1:11434`，这样 ngrok 能稳定连到本机上速度更快的 Ollama 进程。

#### 2) Poffices Block 请求映射

在 Poffices Block 中，将用户输入的 `query` 映射到 FlowerNet 的 `topic`（以及 `user_requirements` 语义）：

- `query -> topic`
- `query + extra_requirements -> user_requirements`（由后端自动组装）

推荐请求体（同步，等待结果）：

```json
{
  "query": "介绍猫咪不同品种的特点和饲养建议",
  "chapter_count": 5,
  "subsection_count": 3,
  "user_background": "普通读者",
  "extra_requirements": "风格简洁、可执行",
  "async_mode": false,
  "timeout_seconds": 1200
}
```

如果你的平台超时较短，改为异步：

```json
{
  "query": "介绍猫咪不同品种的特点和饲养建议",
  "async_mode": true,
  "timeout_seconds": 1200
}
```

然后轮询：

```json
{
  "task_id": "task_xxx"
}
```

#### 3) 鉴权、重试与错误处理

- 请求头传 `X-API-Key: <your-api-key>` 或 `Authorization: Bearer <token>`
- 下游调用（outliner/generator）已内置重试：`DOWNSTREAM_RETRIES` + `DOWNSTREAM_BACKOFF`
- 返回值包含清晰 `success/status/error/message` 字段，便于 Poffices 显示失败原因

#### 4) 统一入口返回结构

`/api/poffices/generate` 在同步完成后会直接返回：

- `title`
- `content`（markdown 文本）
- `stats`
- `download`（包含 `url + body`，可直接调用 `/api/download-docx`）

这样 Poffices 只需对接一个主入口接口即可完成参数转换、生成和下载链路。

### Generator 部署

1. **创建服务**
   - 登录 https://dashboard.render.com/
   - New + → Web Service
   - 连接 GitHub: `WANG-Siqi547/flowernet-agent`

2. **配置**
   - Name: `flowernet-generator`
   - Root Directory: `flowernet-generator`
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

3. **环境变量**
   ```
  GENERATOR_PROVIDER=ollama
  GENERATOR_MODEL=qwen2.5:3b
  OLLAMA_URL=https://你的-ollama-ngrok地址.ngrok-free.dev
   ```

4. **验证**
   ```bash
   curl https://flowernet-generator.onrender.com/health
   curl https://flowernet-generator.onrender.com/debug
   ```

### Verifier、Controller 和 Outliner 部署

类似步骤，Root Directory 分别设为：
- `flowernet-verifier`
- `flowernet-controler`
- `flowernet-outliner`

**Outliner 环境变量**:
```
OUTLINER_PROVIDER=ollama
OUTLINER_MODEL=qwen2.5:3b
OLLAMA_URL=https://你的-ollama-ngrok地址.ngrok-free.dev
USE_DATABASE=false
DATABASE_PATH=flowernet_history.db
```

### 使用云端服务

```python
client = FlowerNetClient(
    verifier_url="https://flowernet-verifier.onrender.com",
    controller_url="https://flowernet-controller.onrender.com",
    generator_url="https://flowernet-generator.onrender.com"
)
```

---

## 📖 API 文档

### Generator API

**POST /generate**
```json
{
  "prompt": "介绍人工智能",
  "max_tokens": 2000
}
```

**Response**:
```json
{
  "success": true,
  "draft": "人工智能（AI）是计算机科学的一个分支...",
  "metadata": {
    "provider": "gemini",
    "prompt_tokens": 15,
    "output_tokens": 342,
    "finish_reason": "STOP"
  }
}
```

**POST /generate_with_context**
```json
{
  "prompt": "详细介绍机器学习",
  "outline": "人工智能核心技术",
  "history": ["前面生成的内容1", "前面生成的内容2"],
  "max_tokens": 2000
}
```

### Verifier API

**POST /verify**
```json
{
  "draft": "生成的内容...",
  "outline": "主题大纲",
  "history": ["历史内容1", "历史内容2"],
  "rel_threshold": 0.6,
  "red_threshold": 0.7
}
```

**Response**:
```json
{
  "success": true,
  "relevancy_index": 0.85,
  "redundancy_index": 0.23,
  "passed": true,
  "feedback": "内容相关性良好，无明显重复"
}
```

### Controller API

**POST /refine_prompt**
```json
{
  "original_prompt": "原始提示词",
  "outline": "大纲",
  "feedback": "相关性不足",
  "history": [],
  "iteration": 1
}
```

**Response**:
```json
{
  "refined_prompt": "优化后的提示词...",
  "changes_made": ["添加了实体召回", "增强了主题约束"],
  "iteration": 1
}
```

### Outliner API

**POST /generate-outline**
```json
{
  "user_background": "背景信息...",
  "user_requirements": "需求描述...",
  "max_sections": 4,
  "max_subsections_per_section": 3
}
```

**POST /generate-structure**
```json
{
  "user_background": "背景信息...",
  "user_requirements": "需求描述...",
  "max_sections": 4,
  "max_subsections_per_section": 3
}
```

**POST /history/add**
```json
{
  "document_id": "doc_001",
  "section_id": "section_1",
  "subsection_id": "subsection_1_1",
  "content": "生成的内容...",
  "metadata": {"tokens": 120}
}
```

---

## 🗄️ History 数据库与工作流程

### 存储内容（只存 History）
每条记录包含：
- `document_id`
- `section_id`
- `subsection_id`
- `content`
- `timestamp`
- `metadata`

### 工作流程（DB 作为唯一 History 源）
1. **Outliner/生成流程写入**：只把“通过验证的最终内容”写入数据库
2. **Verifier 读取**：通过 `document_id` 从数据库读取历史内容
3. **验证完成清理**：文档全部完成后清空该 `document_id` 的历史

### 读写方式（两种模式）
- **SQLite 模式**（推荐）：设置 `USE_DATABASE=true`，并指定 `DATABASE_PATH`
- **内存模式**：`USE_DATABASE=false`（重启即丢）

### Verifier 调用示例（推荐走数据库）
```json
{
  "draft": "生成内容...",
  "outline": "主题大纲",
  "document_id": "doc_001",
  "rel_threshold": 0.6,
  "red_threshold": 0.7
}
```

---

## ⚙️ 配置指南

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `GOOGLE_API_KEY` | - | Gemini API 密钥 |
| `ANTHROPIC_API_KEY` | - | Claude API 密钥 |
| `GENERATOR_PROVIDER` | `gemini` | LLM 提供商 |
| `GENERATOR_MODEL` | `models/gemini-2.5-flash` | 使用的模型 |
| `OUTLINER_MODEL` | `models/gemini-2.5-flash` | Outliner 使用的模型 |
| `USE_DATABASE` | `false` | Outliner History 是否使用 SQLite |
| `DATABASE_PATH` | `flowernet_history.db` | Outliner SQLite 路径 |
| `MAX_ITERATIONS` | `5` | 最大迭代次数 |

### 验证阈值调整

```python
# 宽松模式（更容易通过）
client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.5,  # 降低相关性要求
    red_threshold=0.8   # 提高冗余容忍度
)

# 严格模式（高质量）
client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.8,  # 提高相关性要求
    red_threshold=0.5   # 降低冗余容忍度
)
```

### 性能优化

**生成长度控制**:
```python
# 短内容（快速）
result = generator.generate_draft(prompt, max_tokens=500)

# 长内容（详细）
result = generator.generate_draft(prompt, max_tokens=4000)
```

**并发控制**:
```python
# 单线程顺序生成
for outline in outlines:
    result = client.generate_with_loop(outline, prompt)

# 多线程并发（提高吞吐量）
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=3) as executor:
    results = list(executor.map(lambda o: client.generate_with_loop(o, prompt), outlines))
```

---

## 🔧 故障排查

### Generator 未初始化

**症状**: `Generator not initialized`

**解决**:
```bash
# 1. 检查环境变量
echo $GOOGLE_API_KEY

# 2. 查看调试信息
curl http://localhost:8002/debug

# 3. 重启服务
pkill -f "python.*main.py"
python3 start_services.py
```

### API 密钥被拒绝

**症状**: `403 PERMISSION_DENIED`

**原因**: 密钥泄露或失效

**解决**:
1. 访问 https://aistudio.google.com/app/apikey
2. 删除旧密钥
3. 生成新密钥
4. 更新环境变量并重启

### 相关性始终不通过

**症状**: 多次迭代仍无法通过验证

**解决**:
```python
# 降低阈值
result = client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.4,  # 从 0.6 降到 0.4
    max_iterations=5
)

# 或优化初始提示词
initial_prompt = f"""
请围绕"{outline}"这个主题，详细介绍以下内容：
1. 定义和基本概念
2. 核心特征
3. 实际应用
确保涵盖主题中的所有关键点。
"""
```

### 冗余度过高

**症状**: 内容与历史重复

**解决**:
```python
# 在提示词中强调新颖性
prompt = f"""
请介绍{topic}，注意：
1. 避免重复前面已经提到的内容
2. 从不同角度展开
3. 提供新的例子和观点

已生成内容概要：
{summary_of_history}
"""

# 或调整阈值
result = client.generate_with_loop(
    outline="...",
    initial_prompt=prompt,
    red_threshold=0.8  # 提高容忍度
)
```

### 服务启动失败

**症状**: 端口被占用

**解决**:
```bash
# 查找占用端口的进程
lsof -i :8000
lsof -i :8001
lsof -i :8002

# 终止进程
kill -9 <PID>

# 或使用其他端口
python3 main.py 8010 gemini
```

### 依赖安装失败

**症状**: ModuleNotFoundError

**解决**:
```bash
# 确保 Python 版本正确
python3 --version  # 需要 >= 3.8

# 清理并重新安装
pip cache purge
pip install --no-cache-dir -r requirements.txt

# 检查特定包
pip show google-genai
pip show anthropic
pip show fastapi
```

---

## 📊 性能指标

### 生成速度

| 模型 | 平均速度 | Token 成本 |
|------|---------|----------|
| Gemini Flash | 2-5秒 | 免费 |
| Gemini Pro | 5-10秒 | 免费（有限额）|
| Claude Sonnet | 3-8秒 | $3/MTok (输入) |

### 质量指标

基于测试数据集：
- 相关性达标率: 92%
- 冗余度达标率: 88%
- 平均迭代次数: 1.8
- 首次通过率: 67%

### 资源消耗

- CPU: ~10-20% (单请求)
- 内存: ~200MB (Verifier), ~150MB (Controller), ~180MB (Generator)
- 磁盘: 无状态，无持久化存储

---

## 🎯 使用场景

### 1. 长文档生成

```python
client = FlowerNetClient()

# 定义文档结构
sections = [
    "人工智能的定义与历史",
    "机器学习核心技术",
    "深度学习的应用",
    "AI 伦理与挑战",
    "未来发展趋势"
]

# 逐段生成
history = []
for section in sections:
    result = client.generate_with_loop(
        outline=section,
        initial_prompt=f"详细介绍 {section}，字数 500-800",
        history=history,
        max_iterations=3
    )
    history.append(result['draft'])
    print(f"✅ {section} 已生成")

# 合并成完整文档
full_document = "\n\n".join(history)
```

### 2. 批量内容生成

```python
topics = ["AI", "区块链", "量子计算", "5G", "物联网"]

results = []
for topic in topics:
    result = client.generate_with_loop(
        outline=f"{topic}技术简介",
        initial_prompt=f"介绍{topic}的基本概念和应用场景",
        max_iterations=2
    )
    results.append({
        'topic': topic,
        'content': result['draft'],
        'iterations': result['iterations_used']
    })
```

### 3. 多语言内容

```python
# 中文生成
result_zh = client.generate_with_loop(
    outline="人工智能应用",
    initial_prompt="用中文详细介绍人工智能在医疗领域的应用",
)

# 英文生成
result_en = client.generate_with_loop(
    outline="AI Applications",
    initial_prompt="Describe AI applications in healthcare in English",
)
```

---

## 📦 项目结构

```
flowernet-agent/
├── flowernet-generator/       # 内容生成服务
│   ├── generator.py           # 核心生成逻辑
│   ├── main.py                # FastAPI 服务
│   ├── requirements.txt       # 依赖
│   └── Dockerfile
├── flowernet-verifier/        # 内容验证服务
│   ├── verifier.py            # 验证算法
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── flowernet-controler/       # 提示词优化服务
│   ├── controler.py           # 优化逻辑
│   ├── algo_toolbox.py        # 算法工具箱
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── flowernet_client.py        # 客户端库
├── start_services.py          # 启动脚本
├── test_flowernet_e2e.py      # 端到端测试
├── docker-compose.yml         # Docker 编排
└── README.md                  # 本文档
```

---

## 🤝 贡献

欢迎贡献代码、报告 Bug 或提出改进建议！

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/WANG-Siqi547/flowernet-agent.git
cd flowernet-agent

# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试
python3 -m pytest tests/

# 代码格式化
black .
```

---

## 📄 许可证

MIT License

---

## 📧 联系方式

- GitHub: https://github.com/WANG-Siqi547/flowernet-agent
- Issues: https://github.com/WANG-Siqi547/flowernet-agent/issues

---

## 🙏 致谢

- Google Gemini API - 免费的高质量 LLM 服务
- Anthropic Claude - 优秀的对话式 AI
- Render - 简单易用的云部署平台
- FastAPI - 现代化的 Python Web 框架

---

**最后更新**: 2026年2月8日
