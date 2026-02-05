# FlowerNet 配置和故障排除指南

## 📋 目录

1. [初始配置](#初始配置)
2. [环境变量配置](#环境变量配置)
3. [性能优化](#性能优化)
4. [故障排除](#故障排除)
5. [高级配置](#高级配置)

## 初始配置

### 1. Python 环境设置

```bash
# 检查 Python 版本
python3 --version  # 需要 3.8+

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/macOS
# 或
venv\Scripts\activate  # Windows
```

### 2. 安装依赖

```bash
# 一次性安装所有依赖
pip install -r flowernet-generator/requirements.txt
pip install -r flowernet-verifier/requirements.txt
pip install -r flowernet-controler/requirements.txt

# 或使用 install 脚本
chmod +x install-dependencies.sh
./install-dependencies.sh
```

### 3. 配置 Anthropic API

```bash
# 获取 API Key
# 访问 https://console.anthropic.com/

# 设置环境变量（临时）
export ANTHROPIC_API_KEY="sk-ant-..."

# 设置环境变量（永久）
# 在 ~/.bashrc 或 ~/.zshrc 中添加：
# export ANTHROPIC_API_KEY="sk-ant-..."

# 验证配置
python3 -c "import os; print('API Key set!' if os.getenv('ANTHROPIC_API_KEY') else 'Not set')"
```

## 环境变量配置

### Generator (flowernet-generator)

```bash
# API 配置
export ANTHROPIC_API_KEY="your-api-key"

# 服务配置
export GENERATOR_PUBLIC_URL="http://localhost:8002"
export GENERATOR_PORT=8002

# 其他服务地址（用于编排）
export VERIFIER_URL="http://localhost:8000"
export CONTROLLER_URL="http://localhost:8001"

# 最大迭代次数
export MAX_ITERATIONS=5
```

### Verifier (flowernet-verifier)

```bash
# 服务配置
export VERIFIER_PUBLIC_URL="http://localhost:8000"
export VERIFIER_PORT=8000

# 验证参数默认值
export REL_THRESHOLD=0.6
export RED_THRESHOLD=0.7

# 模型配置
export USE_LIGHTWEIGHT_MODE=true  # 使用轻量级模型
```

### Controller (flowernet-controler)

```bash
# 服务配置
export CONTROLLER_PUBLIC_URL="http://localhost:8001"
export CONTROLLER_PORT=8001

# 算法配置
export CONTROLLER_DEBUG=false  # 调试模式
```

## 性能优化

### 1. 内存优化

#### Verifier 内存优化

```python
# 在 verifier.py 中已预设优化，使用轻量级模型
# 如需进一步优化，编辑以下参数：

# 减小批处理大小
BATCH_SIZE = 32  # 改为 16 或 8

# 使用更小的模型
MODEL_NAME = "distiluse-base-multilingual-cased-v2"  # 代替 paraphrase-multilingual-MiniLM-L12-v2
```

#### Generator 内存优化

```bash
# 使用流式处理
export GENERATOR_STREAMING=true

# 减小 max_tokens
# 在 API 调用中修改：
max_tokens = 1000  # 从 2000 改为 1000
```

### 2. 速度优化

#### 禁用验证循环（仅生成）

```bash
# 使用 /generate 端点而不是 /generate_section
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "...", "max_tokens": 500}'
```

#### 并行处理

```python
# 使用 Python 并发库
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def generate_multiple_sections(outlines):
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=3)
    
    tasks = [
        loop.run_in_executor(executor, client.generate_with_loop, outline)
        for outline in outlines
    ]
    
    results = await asyncio.gather(*tasks)
    return results
```

### 3. 网络优化

#### 连接池复用

```python
# 在 flowernet_client.py 中已实现
# session = requests.Session()  # 自动使用连接池
```

#### 超时调整

```bash
# 对于慢速网络，增加超时
export REQUEST_TIMEOUT=120

# 在客户端代码中：
client = FlowerNetClient(timeout=120)
```

## 故障排除

### 常见错误及解决方案

#### 1. `ModuleNotFoundError: No module named 'anthropic'`

**原因**: 依赖未安装

**解决**:
```bash
pip install anthropic
# 或
pip install -r flowernet-generator/requirements.txt
```

#### 2. `ConnectionRefusedError: [Errno 111] Connection refused`

**原因**: 服务未启动或地址错误

**解决**:
```bash
# 检查服务是否运行
curl http://localhost:8002/
curl http://localhost:8000/
curl http://localhost:8001/

# 如果返回 Connection refused，启动服务：
./start-flowernet.sh

# 或手动启动
python3 flowernet-generator/main.py 8002 &
python3 flowernet-verifier/main.py 8000 &
python3 flowernet-controler/main.py 8001 &
```

#### 3. `401 Unauthorized - Invalid API key`

**原因**: Anthropic API Key 无效或未设置

**解决**:
```bash
# 验证 API Key
echo $ANTHROPIC_API_KEY

# 获取新 API Key：https://console.anthropic.com/
# 设置环境变量
export ANTHROPIC_API_KEY="your-new-key"

# 重启 Generator 服务
pkill -f "flowernet-generator"
python3 flowernet-generator/main.py 8002 &
```

#### 4. `RuntimeError: Could not load CUDA library`

**原因**: GPU 库问题（通常可以忽略）

**解决**:
```bash
# 禁用 GPU，使用 CPU
export CUDA_VISIBLE_DEVICES=""

# 或在 Python 中
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
```

#### 5. `TimeoutError: Request timed out`

**原因**: LLM API 响应慢或网络延迟

**解决**:
```bash
# 方案 1: 增加超时时间
export REQUEST_TIMEOUT=180

# 方案 2: 降低生成长度
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "...", "max_tokens": 500}'  # 从 2000 改为 500

# 方案 3: 检查网络连接
ping api.anthropic.com
```

#### 6. 验证一直失败

**原因**: 阈值设置过高或 Prompt 质量差

**解决**:
```bash
# 方案 1: 降低阈值
rel_threshold = 0.4  # 从 0.6 改为 0.4
red_threshold = 0.8  # 从 0.7 改为 0.8

# 方案 2: 改进 Prompt
# 更清晰的指令，包括具体要求：
"""
请编写一段关于[主题]的内容，要求：
1. 长度 300 字
2. 包含 3-5 个具体例子
3. 逻辑清晰，易于理解
"""

# 方案 3: 增加迭代次数
max_iterations = 10  # 从 5 改为 10

# 方案 4: 检查 Prompt 是否在修改
# 查看 Controller 日志
tail -f logs/Controller.log
```

#### 7. 内存不足 (Out of Memory)

**原因**: 模型太大或数据处理过多

**解决**:
```bash
# 方案 1: 减少并发
# 在 docker-compose.yml 中限制容器内存：
# memory: 2g

# 方案 2: 使用轻量级模型
# 在 verifier.py 中已配置

# 方案 3: 处理较小的文档
# 减少 outlines 数量或文本长度

# 方案 4: 重启服务释放内存
./stop-flowernet.sh
sleep 5
./start-flowernet.sh
```

#### 8. Port 已被占用

**原因**: 端口被其他进程使用

**解决**:
```bash
# 查看占用进程
lsof -i :8002
lsof -i :8000
lsof -i :8001

# 杀死进程
kill -9 <PID>

# 或使用不同的端口
python3 flowernet-generator/main.py 8022 &  # 使用 8022
```

### 调试技巧

#### 启用详细日志

```bash
# 查看实时日志
tail -f logs/*.log

# 按 Ctrl+C 退出
```

#### 测试单个模块

```python
# 测试 Generator
python3 -c "
from flowernet-generator.generator import FlowerNetGenerator
gen = FlowerNetGenerator()
result = gen.generate_draft('Hello')
print(result)
"

# 测试 Verifier
python3 -c "
from flowernet-verifier.verifier import FlowerNetVerifier
ver = FlowerNetVerifier()
result = ver.verify('test', 'test', [])
print(result)
"
```

#### HTTP 调试

```bash
# 使用 curl 测试 API
curl -X POST http://localhost:8002/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "test", "max_tokens": 100}' \
  -v  # 显示详细信息

# 使用 httpie 更友好的输出
pip install httpie

http POST localhost:8002/generate \
  prompt="test" max_tokens=100
```

## 高级配置

### 1. 自定义算法参数

编辑 `flowernet-controler/algo_toolbox.py`:

```python
class FlowerNetAlgos:
    @staticmethod
    def entity_recall(outline):
        # 修改关键词提取逻辑
        words = outline.split()
        key_terms = [w for w in words if len(w) > 3]  # 改为 > 2
        
        if key_terms:
            return f"必须包含: {', '.join(key_terms[:10])}"  # 改为 10 个
        return "严格按照大纲展开"
```

### 2. 自定义验证阈值

创建 `config.json`:

```json
{
  "generator": {
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 2000,
    "temperature": 0.7
  },
  "verifier": {
    "rel_threshold": 0.6,
    "red_threshold": 0.7,
    "use_lightweight": true
  },
  "controller": {
    "max_iterations": 5,
    "debug": false
  }
}
```

然后在代码中读取:

```python
import json

with open("config.json") as f:
    config = json.load(f)

rel_threshold = config["verifier"]["rel_threshold"]
```

### 3. 与外部系统集成

```python
# 与数据库集成
from sqlalchemy import create_engine

engine = create_engine('sqlite:///flowernet.db')

# 保存生成结果
def save_result(title, content, metadata):
    with engine.connect() as conn:
        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?)",
            (title, content, json.dumps(metadata))
        )
        conn.commit()
```

### 4. 使用 Docker 部署

创建 `docker-compose.yml`:

```yaml
version: '3.8'

services:
  verifier:
    build: ./flowernet-verifier
    ports:
      - "8000:8000"
    environment:
      - VERIFIER_PUBLIC_URL=http://verifier:8000
    networks:
      - flowernet

  controller:
    build: ./flowernet-controler
    ports:
      - "8001:8001"
    environment:
      - CONTROLLER_PUBLIC_URL=http://controller:8001
    networks:
      - flowernet

  generator:
    build: ./flowernet-generator
    ports:
      - "8002:8002"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - GENERATOR_PUBLIC_URL=http://generator:8002
      - VERIFIER_URL=http://verifier:8000
      - CONTROLLER_URL=http://controller:8001
    depends_on:
      - verifier
      - controller
    networks:
      - flowernet

networks:
  flowernet:
    driver: bridge
```

启动:

```bash
export ANTHROPIC_API_KEY="your-key"
docker-compose up -d
```

### 5. 监控和统计

```python
# 添加监控
from prometheus_client import Counter, Histogram
import time

gen_counter = Counter('flowernet_generates_total', 'Total generates')
gen_time = Histogram('flowernet_generate_duration_seconds', 'Generate time')

@gen_time.time()
def monitored_generate(prompt):
    gen_counter.inc()
    return generate(prompt)
```

## 📞 获取帮助

- 查看官方文档: https://docs.anthropic.com
- GitHub Issues: [项目 GitHub]
- FAQ: README_FLOWERNET.md

---

**祝你使用愉快！**
