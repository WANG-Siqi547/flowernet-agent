# 🌸 FlowerNet - 完整文档生成系统

一个基于 LLM 的智能内容生成系统，采用**生成-验证-修改**的循环流程，确保生成内容的相关性和原创性。

## 📋 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                     FlowerNet 工作流程                        │
└─────────────────────────────────────────────────────────────┘

                    用户提交大纲和 prompt
                            ↓
    ┌───────────────────────┴───────────────────────┐
    ↓                                               ↓
┌──────────────────────┐                ┌──────────────────────┐
│   🎯 Generator       │                │   📚 历史内容        │
│  (LLM 生成 Draft)    │                │   (避免冗余)         │
└──────────┬───────────┘                └──────────┬───────────┘
           ↓                                       ↓
    ┌──────────────────────────────────────────┐
    │      生成的 Draft 内容                    │
    └─────────────┬──────────────────────────────┘
                  ↓
    ┌──────────────────────────────────────────┐
    │   🔍 Verifier                            │
    │  (验证相关性 & 冗余度)                   │
    └──────────┬──────────┬────────────────────┘
               ↓          ↓
        ✅ 通过验证   ❌ 验证失败
        (返回结果)     ↓
                ┌──────────────────────────────┐
                │   🔧 Controller              │
                │  (根据反馈修改 Prompt)      │
                └──────────────┬───────────────┘
                              ↓
                    修改后的新 Prompt
                              ↓
                     (回到 Generator，循环)
```

## 🏗️ 项目结构

```
flowernet-agent/
├── flowernet-generator/       # 生成模块
│   ├── main.py               # FastAPI 服务入口
│   ├── generator.py          # 核心生成逻辑
│   ├── requirements.txt       # 依赖包
│   └── Dockerfile            # Docker 配置
├── flowernet-verifier/        # 验证模块
│   ├── main.py               # FastAPI 服务入口
│   ├── verifier.py           # 核心验证逻辑
│   ├── requirements.txt       # 依赖包
│   └── Dockerfile            # Docker 配置
├── flowernet-controler/       # 控制模块
│   ├── main.py               # FastAPI 服务入口
│   ├── controler.py          # 核心控制逻辑
│   ├── algo_toolbox.py       # 算法工具库
│   ├── requirements.txt       # 依赖包
│   └── Dockerfile            # Docker 配置
├── test_flowernet_e2e.py     # 端到端测试脚本
├── start-flowernet.sh        # 启动所有服务
├── stop-flowernet.sh         # 停止所有服务
└── README_FLOWERNET.md       # 本文档
```

## 🚀 快速开始

### 前置条件

- Python 3.8+
- `pip` 包管理器
- Anthropic API Key（用于 Claude LLM）

### 步骤 1：环境设置

```bash
# 1. 克隆或进入项目目录
cd flowernet-agent

# 2. 设置 Anthropic API Key
export ANTHROPIC_API_KEY="your-api-key-here"

# 3. (可选) 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows
```

### 步骤 2：启动服务

#### 方式一：使用启动脚本（推荐）

```bash
# 使脚本可执行
chmod +x start-flowernet.sh stop-flowernet.sh

# 启动所有服务
./start-flowernet.sh
```

这将自动：
- 安装所有依赖
- 启动三个 FastAPI 服务（端口 8000, 8001, 8002）
- 显示实时日志

#### 方式二：手动启动

在三个不同的终端中分别运行：

```bash
# 终端 1：启动 Verifier（验证层）
cd flowernet-verifier
python3 main.py 8000

# 终端 2：启动 Controller（控制层）
cd flowernet-controler
python3 main.py 8001

# 终端 3：启动 Generator（生成层）
cd flowernet-generator
python3 main.py 8002
```

### 步骤 3：验证服务

```bash
# 检查服务状态
curl http://localhost:8000/
curl http://localhost:8001/
curl http://localhost:8002/

# 查看 API 文档
# 访问浏览器：
# http://localhost:8000/docs  (Verifier)
# http://localhost:8001/docs  (Controller)
# http://localhost:8002/docs  (Generator)
```

### 步骤 4：运行端到端测试

```bash
python3 test_flowernet_e2e.py
```

## 📖 使用指南

### API 端点概览

#### Generator (端口 8002)

| 端点 | 方法 | 描述 |
|------|------|------|
| `/` | GET | 检查服务状态 |
| `/generate` | POST | 简单生成模式（只生成，不验证） |
| `/generate_with_context` | POST | 带上下文的生成 |
| `/generate_section` | POST | 生成一个段落（完整循环） |
| `/generate_document` | POST | 生成完整文档（多段落） |

**例子：简单生成**

```bash
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "请介绍人工智能的基本概念",
    "max_tokens": 500
  }'
```

**例子：生成段落（带验证循环）**

```bash
curl -X POST http://localhost:8002/generate_section \
  -H "Content-Type: application/json" \
  -d '{
    "outline": "介绍人工智能的基本概念",
    "initial_prompt": "请详细介绍人工智能的基本概念。",
    "history": [],
    "rel_threshold": 0.6,
    "red_threshold": 0.7
  }'
```

#### Verifier (端口 8000)

| 端点 | 方法 | 描述 |
|------|------|------|
| `/` | GET | 检查服务状态 |
| `/verify` | POST | 验证草稿内容 |

**验证响应格式：**

```json
{
  "is_passed": true,
  "relevancy_index": 0.75,
  "redundancy_index": 0.25,
  "feedback": "Content looks good.",
  "raw_data": {
    "relevancy": {...},
    "redundancy": {...}
  }
}
```

- **relevancy_index**: 0-1，越高越好（≥0.6 为合格）
- **redundancy_index**: 0-1，越低越好（≤0.7 为合格）

#### Controller (端口 8001)

| 端点 | 方法 | 描述 |
|------|------|------|
| `/` | GET | 检查服务状态 |
| `/refine_prompt` | POST | 根据验证反馈修改 Prompt |
| `/analyze_failures` | POST | 分析失败模式 |

### 完整工作流程示例

```python
# Python 客户端示例
import requests

# 配置
generator_url = "http://localhost:8002"
verifier_url = "http://localhost:8000"
controller_url = "http://localhost:8001"

# 1. 初始 prompt
outline = "介绍人工智能的基本概念"
prompt = "请详细介绍人工智能的基本概念、发展历程和应用领域。"
history = []

# 2. 循环生成-验证-修改
for iteration in range(5):
    # Generator 生成
    gen_result = requests.post(
        f"{generator_url}/generate",
        json={"prompt": prompt, "max_tokens": 500}
    ).json()
    
    draft = gen_result["draft"]
    print(f"迭代 {iteration}: 生成了 {len(draft)} 字符内容")
    
    # Verifier 验证
    ver_result = requests.post(
        f"{verifier_url}/verify",
        json={
            "draft": draft,
            "outline": outline,
            "history": history,
            "rel_threshold": 0.6,
            "red_threshold": 0.7
        }
    ).json()
    
    print(f"  相关性: {ver_result['relevancy_index']:.4f}")
    print(f"  冗余度: {ver_result['redundancy_index']:.4f}")
    
    if ver_result["is_passed"]:
        print("✅ 验证通过！")
        history.append(draft)
        break
    
    # Controller 修改
    ctl_result = requests.post(
        f"{controller_url}/refine_prompt",
        json={
            "old_prompt": prompt,
            "failed_draft": draft,
            "feedback": ver_result,
            "outline": outline,
            "history": history,
            "iteration": iteration + 1
        }
    ).json()
    
    prompt = ctl_result["prompt"]
    print("🔧 Prompt 已修改，进入下一轮...")
```

## 🔧 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ANTHROPIC_API_KEY` | - | Anthropic API Key（必需） |
| `GENERATOR_PUBLIC_URL` | http://localhost:8002 | Generator 公网 URL |
| `VERIFIER_PUBLIC_URL` | http://localhost:8000 | Verifier 公网 URL |
| `CONTROLLER_PUBLIC_URL` | http://localhost:8001 | Controller 公网 URL |
| `MAX_ITERATIONS` | 5 | 最大迭代次数 |
| `PORT` | 8001/8000/8002 | 服务端口 |

### 验证参数调整

在 Generator 的 `generate_section` 中可调整：

- **rel_threshold**: 相关性阈值（0-1），推荐 0.5-0.7
- **red_threshold**: 冗余度阈值（0-1），推荐 0.6-0.8
- **max_iterations**: 最大循环次数，推荐 3-5

```bash
# 例子：更严格的验证
curl -X POST http://localhost:8002/generate_section \
  -H "Content-Type: application/json" \
  -d '{
    "outline": "...",
    "initial_prompt": "...",
    "rel_threshold": 0.7,
    "red_threshold": 0.6,
    "history": []
  }'
```

## 🧪 测试和调试

### 运行端到端测试

```bash
# 完整测试
python3 test_flowernet_e2e.py

# 输出示例：
# 🔍 检查服务状态...
#   ✅ Generator: 在线
#   ✅ Verifier: 在线
#   ✅ Controller: 在线
# ✅ 所有测试通过！
```

### 查看实时日志

```bash
# 查看所有日志
tail -f logs/*.log

# 查看特定服务日志
tail -f logs/Generator.log
tail -f logs/Verifier.log
tail -f logs/Controller.log
```

### 停止服务

```bash
# 使用脚本
./stop-flowernet.sh

# 或手动停止
pkill -f "python.*main.py"
```

## 📊 输出示例

### 成功的验证

```json
{
  "success": true,
  "draft": "人工智能是计算机科学的一个重要分支...",
  "iterations": 2,
  "verification": {
    "relevancy_index": 0.75,
    "redundancy_index": 0.25,
    "feedback": "Content looks good."
  }
}
```

### 完整文档生成

```json
{
  "title": "人工智能概论",
  "sections": [
    {
      "outline": "基本概念",
      "content": "...",
      "iterations": 2,
      "verification": {...}
    },
    {
      "outline": "发展历程",
      "content": "...",
      "iterations": 1,
      "verification": {...}
    }
  ],
  "total_iterations": 3,
  "success_count": 2
}
```

## 🐳 Docker 部署

### 单个服务

```bash
# 构建 Generator 镜像
cd flowernet-generator
docker build -t flowernet-generator .
docker run -p 8002:8002 \
  -e ANTHROPIC_API_KEY="your-key" \
  flowernet-generator

# 同样构建 Verifier 和 Controller
```

### Docker Compose

```bash
# 在项目根目录创建 docker-compose.yml
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

## 🎯 最佳实践

### 1. 初始 Prompt 编写

```
✅ 好的 Prompt：
"请详细介绍 [主题]，包括：
1. 基本概念和定义
2. 发展历史
3. 实际应用
4. 未来前景
字数：300-500 字"

❌ 不好的 Prompt：
"写点关于 [主题] 的东西"
```

### 2. 调整验证阈值

```
相关性阈值：
- 0.4-0.5: 较宽松，快速生成
- 0.6-0.7: 标准，推荐
- 0.8+: 严格，质量最高但耗时

冗余度阈值：
- 0.5-0.6: 严格去重
- 0.7-0.8: 标准，推荐
- 0.9+: 宽松，允许部分重复
```

### 3. 处理循环过多

如果多次迭代仍未通过：

```
1. 降低相关性阈值（允许内容更灵活）
2. 增加 max_iterations
3. 简化大纲和 prompt
4. 检查 Controller 的 prompt 修改是否有效
```

## ⚠️ 常见问题

### Q: 服务无法启动

**A:** 检查：
1. Python 版本 >= 3.8
2. 依赖已安装：`pip install -r requirements.txt`
3. 端口未被占用：`lsof -i :8000`

### Q: API 返回 timeout

**A:** 原因可能是：
1. LLM API 响应慢 - 增加 timeout
2. 网络问题 - 检查连接
3. 内存不足 - 增加系统资源

### Q: 验证一直失败

**A:** 尝试：
1. 降低 rel_threshold 和 red_threshold
2. 改进初始 prompt 的质量
3. 检查 Controller 是否正确修改了 prompt

### Q: 生成内容重复

**A:** 解决方案：
1. 降低 red_threshold（更严格去重）
2. 增加历史内容长度
3. 改进 Controller 的 prompt 修改逻辑

## 📝 开发日志

### 版本 1.0 功能

- ✅ Generator：使用 Claude LLM 生成内容
- ✅ Verifier：验证相关性和冗余度
- ✅ Controller：根据反馈优化 prompt
- ✅ 完整的生成-验证-修改循环
- ✅ 支持单段落和多段落生成
- ✅ 完整的 API 接口
- ✅ 端到端测试
- ✅ Docker 支持

### 未来计划

- [ ] 支持多种 LLM（GPT-4, Gemini 等）
- [ ] 更高级的冗余度检测（使用向量数据库）
- [ ] 网页 UI 界面
- [ ] 批量处理和队列系统
- [ ] 内容持久化存储
- [ ] 更详细的统计分析

## 📄 许可证

本项目采用 MIT 许可证。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📞 联系方式

如有问题或建议，请提交 GitHub Issue。

---

**🌸 祝你使用愉快！**
