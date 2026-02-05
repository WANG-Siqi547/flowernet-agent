# 🌟 Google Gemini API 免费使用指南

## ✅ 为什么选择 Gemini？

- **完全免费** - 每分钟 60 次请求，每天 1500 次请求
- **无需信用卡** - 不需要绑定支付方式
- **性能优秀** - Gemini 1.5 Flash 速度快，质量高
- **中文支持** - 对中文内容生成支持良好

---

## 📝 获取 Google Gemini API Key（5分钟）

### 步骤 1：访问 Google AI Studio

打开浏览器，访问：

```
https://aistudio.google.com/app/apikey
```

### 步骤 2：登录 Google 账号

使用你的 Google 账号登录（Gmail 账号即可）

### 步骤 3：创建 API Key

1. 点击页面上的 **"Create API Key"** 按钮
2. 选择一个 Google Cloud 项目（如果没有，会自动创建一个新项目）
3. 点击 **"Create API key in new project"**
4. 几秒钟后，你的 API Key 就生成了！

### 步骤 4：复制 API Key

复制生成的 API Key，格式类似：

```
AIzaSyA1234567890abcdefghijklmnopqrstuvwx
```

⚠️ **重要提示**：请妥善保管你的 API Key，不要分享给他人！

---

## 🚀 配置 FlowerNet 使用 Gemini

### 方法 1：设置环境变量（推荐）

在终端中运行：

```bash
export GOOGLE_API_KEY="你的API密钥"
```

如果希望永久保存，添加到 `~/.zshrc` 文件：

```bash
echo 'export GOOGLE_API_KEY="你的API密钥"' >> ~/.zshrc
source ~/.zshrc
```

### 方法 2：在代码中直接传入

修改 `flowernet-generator/main.py`，在初始化时传入 API Key：

```python
generator = FlowerNetGenerator(
    api_key="你的API密钥",
    model="gemini-1.5-flash",
    provider="gemini"
)
```

---

## 📦 安装依赖

安装 Google Generative AI SDK：

```bash
cd flowernet-agent/flowernet-generator
pip install -r requirements.txt
```

或单独安装：

```bash
pip install google-generativeai
```

---

## 🎯 启动服务

### 1. 停止现有服务

```bash
pkill -f "main.py"
```

### 2. 启动 Gemini 版本的 Generator

```bash
cd /Users/k1ns9sley/Desktop/msc\ project/flowernet-agent

# 设置 API Key（如果还没设置）
export GOOGLE_API_KEY="你的API密钥"

# 启动 Verifier (端口 8000)
python3 flowernet-verifier/main.py 8000 &

# 启动 Controller (端口 8001)
python3 flowernet-controler/main.py 8001 &

# 启动 Generator with Gemini (端口 8002)
python3 flowernet-generator/main.py 8002 gemini &
```

### 3. 验证服务状态

```bash
# 检查服务进程
ps aux | grep main.py

# 测试 Generator
curl http://localhost:8002/

# 应该看到类似输出：
# {"status": "ok", "message": "FlowerNet Generator 已启动", "provider": "gemini"}
```

---

## 🧪 快速测试

创建测试脚本 `test_gemini.py`：

```python
from flowernet_client import FlowerNetClient

# 初始化客户端
client = FlowerNetClient(
    generator_url="http://localhost:8002",
    verifier_url="http://localhost:8000",
    controller_url="http://localhost:8001"
)

# 测试生成
print("🧪 测试 Gemini 生成...")
result = client.generate_with_loop(
    outline="人工智能的基本概念",
    initial_prompt="请用200字介绍人工智能的基本概念",
    max_iterations=3,
    rel_threshold=0.5,
    red_threshold=0.8
)

if result['success']:
    print("✅ 生成成功！")
    print(f"\n生成内容:\n{result['draft']}\n")
    print(f"迭代次数: {result['iterations']}")
    print(f"相关性: {result['relevancy_index']:.2f}")
    print(f"冗余度: {result['redundancy_index']:.2f}")
else:
    print(f"❌ 生成失败: {result.get('error', 'Unknown error')}")
```

运行测试：

```bash
python3 test_gemini.py
```

---

## 🎛️ 模型选择

Gemini 提供多个模型，你可以根据需求选择：

### Gemini 1.5 Flash（推荐，默认）
- **速度**: 非常快
- **免费额度**: 高
- **适用场景**: 快速生成、大量请求
- **使用方式**: `model="gemini-1.5-flash"`

### Gemini 1.5 Pro
- **速度**: 较慢
- **质量**: 更高
- **免费额度**: 较低
- **适用场景**: 高质量内容
- **使用方式**: `model="gemini-1.5-pro"`

### Gemini 1.0 Pro
- **速度**: 中等
- **稳定性**: 高
- **使用方式**: `model="gemini-pro"`

修改模型在 `flowernet-generator/main.py` 中：

```python
generator = FlowerNetGenerator(
    model="gemini-1.5-flash",  # 改成你想要的模型
    provider="gemini"
)
```

---

## 📊 免费额度说明

### Gemini 1.5 Flash（免费版）

| 指标 | 限制 |
|------|------|
| 每分钟请求数 | 15 RPM |
| 每天请求数 | 1500 RPD |
| 每分钟 Token 数 | 1,000,000 TPM |
| 每天 Token 数 | 无限制 |

### Gemini 1.5 Pro（免费版）

| 指标 | 限制 |
|------|------|
| 每分钟请求数 | 2 RPM |
| 每天请求数 | 50 RPD |
| 每分钟 Token 数 | 32,000 TPM |

**💡 提示**：对于大多数使用场景，Gemini 1.5 Flash 的免费额度完全够用！

---

## 🔧 故障排除

### 问题 1: "需要安装 google-generativeai"

**解决方案**：
```bash
pip install google-generativeai
```

### 问题 2: "请设置 GOOGLE_API_KEY 环境变量"

**解决方案**：
```bash
export GOOGLE_API_KEY="你的API密钥"
```

### 问题 3: "API Key 无效"

**检查清单**：
1. API Key 是否正确复制（没有多余空格）
2. 是否在 Google AI Studio 中成功创建了 API Key
3. 尝试重新生成一个新的 API Key

### 问题 4: "超出配额限制"

**解决方案**：
- 等待 1 分钟后重试（可能超出了每分钟请求限制）
- 切换到 Gemini 1.5 Flash（额度更高）
- 降低 `max_iterations` 参数减少请求次数

### 问题 5: 生成内容质量不理想

**调优建议**：
```python
# 提高质量模式
result = client.generate_with_loop(
    outline="你的主题",
    initial_prompt="详细的生成指令",
    max_iterations=5,        # 增加迭代次数
    rel_threshold=0.7,       # 提高相关性要求
    red_threshold=0.6,       # 降低冗余度容忍度
    max_tokens=3000          # 增加生成长度
)
```

---

## 🌐 对比：Gemini vs Claude

| 特性 | Google Gemini | Anthropic Claude |
|------|---------------|------------------|
| 免费额度 | ✅ 每天 1500 次 | ⚠️ $5 初始额度 |
| 中文支持 | ✅ 优秀 | ✅ 优秀 |
| 生成速度 | ✅ 非常快 | ⚠️ 较慢 |
| 内容质量 | ✅ 高 | ✅ 非常高 |
| API 稳定性 | ✅ 稳定 | ✅ 非常稳定 |
| 需要信用卡 | ❌ 不需要 | ✅ 需要 |

**建议**：
- 🎓 **学习/测试**：使用 Gemini（完全免费）
- 🏢 **生产环境**：使用 Claude（质量更稳定）

---

## 📚 相关链接

- **Google AI Studio**: https://aistudio.google.com/
- **Gemini API 文档**: https://ai.google.dev/docs
- **定价和配额**: https://ai.google.dev/pricing
- **Python SDK 文档**: https://github.com/google/generative-ai-python

---

## 🎉 完成！

现在你已经成功配置了免费的 Google Gemini API！

立即开始测试：

```bash
export GOOGLE_API_KEY="你的API密钥"
python3 start_services.py
python3 test_gemini.py
```

祝你使用愉快！ 🚀
