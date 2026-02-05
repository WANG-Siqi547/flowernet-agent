# 🌸 FlowerNet 完整使用指南（2026）

## ⚡ 快速开始（5分钟）

### 前置要求

1. **Python 3.8+**
   ```bash
   python3 --version  # 检查版本
   ```

2. **Anthropic API Key**
   - 访问 https://console.anthropic.com/
   - 复制你的 API Key（格式：`sk-ant-...`）

### 步骤 1：设置 API Key

```bash
# 设置环境变量
export ANTHROPIC_API_KEY="sk-ant-your-api-key-here"

# 验证设置
echo $ANTHROPIC_API_KEY
```

### 步骤 2：启动所有服务

进入项目目录，启动所有三个服务：

```bash
cd flowernet-agent

# 方式 A：使用 Python 启动脚本（推荐）
python3 start_services.py

# 方式 B：使用 bash 脚本
bash start-flowernet.sh
```

**预期输出：**
```
==================================================
🌸 FlowerNet 启动脚本
==================================================
🚀 启动 Verifier (端口 8000)...
🚀 启动 Controller (端口 8001)...
🚀 启动 Generator (端口 8002)...

==================================================
✅ 所有服务已启动
==================================================

📋 服务地址:
  Generator:  http://localhost:8002
  Verifier:   http://localhost:8000
  Controller: http://localhost:8001
```

### 步骤 3：验证系统

```bash
python3 << 'EOF'
from flowernet_client import FlowerNetClient

client = FlowerNetClient()
status = client.health_check()

for service, online in status.items():
    print(f"{service}: {'✅ 在线' if online else '❌ 离线'}")
EOF
```

**预期输出：**
```
Generator: ✅ 在线
Verifier: ✅ 在线
Controller: ✅ 在线
```

## 🎯 常用操作

### 生成单个段落

```python
from flowernet_client import FlowerNetClient

client = FlowerNetClient(verbose=True)

result = client.generate_with_loop(
    outline="介绍人工智能的基本概念",
    initial_prompt="请用简洁的语言介绍人工智能。",
    max_iterations=3,
    rel_threshold=0.6,
    red_threshold=0.7
)

if result.get("success"):
    print(f"✅ 成功！")
    print(f"迭代次数: {result['iterations']}")
    print(f"\n生成内容:\n{result['draft']}")
```

### 生成完整文档

```python
from flowernet_client import FlowerNetClient, FlowerNetDocumentGenerator

client = FlowerNetClient(verbose=True)
doc_gen = FlowerNetDocumentGenerator(client)

document = doc_gen.generate_document(
    title="人工智能入门指南",
    outlines=[
        "基本概念和定义",
        "发展历史和现状",
        "主要应用领域",
        "未来发展前景"
    ],
    system_prompt="使用简洁、易懂的语言，适合初学者",
    max_iterations=3
)

print(f"文档: {document['title']}")
print(f"段落: {len(document['sections'])}")
print(f"成功: {document['success_count']}/{len(document['sections'])}")
```

### 查看 API 文档

访问 FastAPI 自动生成的交互式文档：

- **Generator API**: http://localhost:8002/docs
- **Verifier API**: http://localhost:8000/docs
- **Controller API**: http://localhost:8001/docs

## 🔧 配置参数

### 相关性阈值（rel_threshold）

控制生成内容必须与大纲相关的程度：

| 值 | 模式 | 特点 |
|----|------|------|
| 0.3-0.5 | 宽松 | 快速，但可能偏离主题 |
| 0.5-0.7 | 标准 | **推荐** |
| 0.7-0.9 | 严格 | 高质量，耗时较长 |

### 冗余度阈值（red_threshold）

控制生成内容与历史内容的重复程度：

| 值 | 模式 | 特点 |
|----|------|------|
| 0.5-0.6 | 严格 | 高度原创，可能生成困难 |
| 0.7-0.8 | 标准 | **推荐** |
| 0.8-0.9 | 宽松 | 快速，允许部分重复 |

### 最大迭代次数（max_iterations）

| 值 | 模式 | 特点 |
|----|------|------|
| 1-2 | 快速 | 一次性生成，不进行验证循环 |
| 3-5 | 平衡 | **推荐** |
| 5+ | 完美 | 持续优化直到最佳质量 |

## 📊 工作流程说明

### 完整的生成-验证-修改循环

```
初始 Prompt
    ↓
[Generator] 生成 Draft
    ↓
[Verifier] 验证 (相关性 & 冗余度)
    ├─→ ✅ 通过 → 返回结果
    └─→ ❌ 失败 → [Controller] 修改 Prompt
           ↓
         (回到 Generator)
```

### 参数影响

```
rel_threshold ↑  →  需要更多迭代  →  生成时间 ↑
red_threshold ↓  →  需要更多迭代  →  生成时间 ↑
max_iterations ↑  →  生成时间 ↑  →  质量 ↑
```

## 🆘 常见问题

### Q1: "API Key not found" 错误

**症状：** 启动 Generator 时报错

**原因：** ANTHROPIC_API_KEY 未设置

**解决：**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python3 start_services.py
```

### Q2: 连接被拒绝

**症状：** `Connection refused`

**原因：** 服务未启动

**解决：**
```bash
# 检查服务
ps aux | grep main.py

# 重新启动
python3 start_services.py
```

### Q3: 生成速度很慢

**症状：** 等待 30+ 秒

**原因：** 正常的 API 延迟或参数要求太高

**解决方案：**
```python
# 降低阈值以加快速度
result = client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    max_iterations=2,        # 减少迭代
    rel_threshold=0.4,       # 降低相关性要求
    red_threshold=0.8        # 放松冗余度检查
)
```

### Q4: 验证一直失败

**症状：** 多次迭代仍不通过

**原因：** 阈值设置过高或 Prompt 不够清晰

**解决方案：**
```python
# 方案 1：降低阈值
result = client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.4,  # 从 0.6 改为 0.4
    red_threshold=0.8   # 从 0.7 改为 0.8
)

# 方案 2：改进 Prompt
better_prompt = """
请编写一段内容，要求：
1. 主题明确关于「人工智能」
2. 字数在 200-300 字
3. 用易理解的语言
4. 包含 2-3 个具体例子
"""

# 方案 3：增加历史内容
result = client.generate_with_loop(
    outline="...",
    initial_prompt=better_prompt,
    history=["前面已生成的内容"],  # 帮助 Verifier 判断
    max_iterations=5
)
```

### Q5: 内存不足

**症状：** `MemoryError` 或进程被杀死

**原因：** 模型太大或数据过多

**解决：**
```bash
# 使用轻量级模式
export USE_LIGHTWEIGHT=true
python3 start_services.py

# 或减少文档长度
doc_gen.generate_document(
    title="...",
    outlines=[...[:5]],  # 只生成前 5 个段落
)
```

## 📋 服务管理

### 停止服务

```bash
# 如果使用 start_services.py
# 按 Ctrl+C

# 或手动停止
bash stop-flowernet.sh

# 或使用 pkill
pkill -f "main.py"
```

### 查看日志

```bash
# 所有日志
tail -f /tmp/*.log

# 特定服务
tail -f /tmp/Generator.log
tail -f /tmp/Verifier.log
tail -f /tmp/Controller.log
```

### 清理端口

如果端口被占用：

```bash
# 查找占用的进程
lsof -i :8000
lsof -i :8001
lsof -i :8002

# 杀死进程
kill -9 <PID>

# 或使用其他端口
python3 flowernet-generator/main.py 8022
```

## 🚀 高级用法

### 自定义验证参数

```python
# 为不同类型的内容设置不同参数

# 快速生成模式
fast_result = client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.3,
    red_threshold=0.9,
    max_iterations=1
)

# 高质量模式
quality_result = client.generate_with_loop(
    outline="...",
    initial_prompt="...",
    rel_threshold=0.8,
    red_threshold=0.5,
    max_iterations=10
)
```

### 批量处理多个文档

```python
from flowernet_client import FlowerNetClient, FlowerNetDocumentGenerator

client = FlowerNetClient()
doc_gen = FlowerNetDocumentGenerator(client)

documents = [
    {"title": "AI 基础", "outlines": [...] },
    {"title": "ML 算法", "outlines": [...] },
    {"title": "DL 框架", "outlines": [...] },
]

results = []
for doc in documents:
    result = doc_gen.generate_document(
        title=doc["title"],
        outlines=doc["outlines"],
        max_iterations=2
    )
    results.append(result)
    
# 保存结果
import json
with open("results.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
```

### 集成到其他应用

```python
# 作为模块导入使用
from flowernet_client import FlowerNetClient

def generate_report(title, sections):
    """生成报告"""
    client = FlowerNetClient()
    
    doc_gen = FlowerNetDocumentGenerator(client)
    document = doc_gen.generate_document(
        title=title,
        outlines=sections
    )
    
    return document

# 在其他地方使用
report = generate_report(
    "月度工作总结",
    ["工作完成情况", "主要成就", "存在问题", "下月计划"]
)
```

## 📚 相关文件

| 文件 | 说明 |
|------|------|
| `README_FLOWERNET.md` | 完整系统文档 |
| `CONFIG_GUIDE.md` | 配置和调试指南 |
| `flowernet_client.py` | Python 客户端库 |
| `test_flowernet_e2e.py` | 端到端测试脚本 |
| `start_services.py` | 服务启动脚本 |

## 📞 获取帮助

- **API 文档**: http://localhost:8002/docs
- **问题排查**: 查看 `CONFIG_GUIDE.md` 故障排除部分
- **日志查看**: `tail -f /tmp/*.log`

---

**现在开始使用 FlowerNet，生成高质量内容！** 🚀
