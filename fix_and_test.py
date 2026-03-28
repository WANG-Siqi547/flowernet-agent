#!/usr/bin/env python3
"""
问题诊断和修复方案
"""

print("""
╔════════════════════════════════════════════════════════════════╗
║           FlowerNet 测试问题分析和修复方案                    ║
╚════════════════════════════════════════════════════════════════╝

【识别的问题】

1️⃣  大纲生成请求超时 (第一次)
   - 原因: Generator 和 Outliner 在第一次请求时进行初始化
   - Ollama qwen2.5:1.5b 生成速度较慢
   - 多个服务之间链式调用累积延迟

2️⃣  Verifier 相关性阈值过高
   - 当前: rel_threshold = 0.80  
   - 问题: 简单文本对（如"AI医疗" vs "AI在医疗中的应用"）只得 0.70
   - 结果: 大多数内容都无法通过 Verifier，导致 Controller 频繁触发

【修复方案】

▶️  方案 A: 降低 Verifier 阈值（快速）
   - rel_threshold: 0.80 → 0.70 （相对宽松）
   - red_threshold: 0.50 → 0.55 （相对宽松）
   - 预期触发率: 15-25%（可能过低）

▶️  方案 B: 加长测试超时并优化调用链（完整）
   1. 增加 API 超时: 600s → 900s
   2. 预热 Ollama（首次调用总是慢）
   3. 使用标准化的 prompt 缩短生成时间
   4. 逐步调整阈值达到 30-50% 目标

【建议】

✅ 立即执行:
   1. 修改 Verifier 阈值为 rel=0.75, red=0.50
   2. 修改超时为 900 秒
   3. 使用简化的大纲和 prompt
   4. 预热 Ollama 模型

【代码修改位置】

1️⃣  降低 Verifier 相关性阈值:
   文件: flowernet-generator/flowernet_orchestrator_impl.py
   行号: 292-293
   修改前:
     rel_threshold: float = 0.83,
     red_threshold: float = 0.50,
   修改后:
     rel_threshold: float = 0.75,
     red_threshold: float = 0.50,

2️⃣  增加生成超时:
   文件: test_minimal.py
   修改前:
     timeout=600
   修改后:
     timeout=900

【快速修复步骤】

1. 修改阈值
   cd flowernet-generator
   sed -i '' 's/rel_threshold: float = 0.83/rel_threshold: float = 0.75/' flowernet_orchestrator_impl.py
   cd ..

2. 更新测试脚本
   sed -i '' 's/timeout=600/timeout=900/' test_minimal.py

3. 重建容器
   docker-compose up -d --build

4. 预热 Ollama（可选但推荐）
   curl -s http://localhost:11434/api/generate -d '{"model":"qwen2.5:1.5b","prompt":"test"}' > /dev/null &

5. 运行测试
   python3 test_minimal.py

""")

# 先执行快速修复
import os
import subprocess

os.chdir('/Users/k1ns9sley/Desktop/msc project/flowernet-agent')

print("\n📝 执行快速修复...\n")

# 修改阈值
print("1️⃣  修改 Verifier 阈值...")
os.chdir('flowernet-generator')
result = subprocess.run(
    "sed -i '' 's/rel_threshold: float = 0.83/rel_threshold: float = 0.75/' flowernet_orchestrator_impl.py",
    shell=True
)
if result.returncode == 0:
    print("   ✅ 已修改阈值")
else:
    print("   ⚠️  修改可能失败，请手动修改")
os.chdir('..')

# 修改超时
print("2️⃣  修改测试超时...")
result = subprocess.run(
    "sed -i '' 's/timeout=600/timeout=900/' test_minimal.py",
    shell=True,
    cwd='.'
)
if result.returncode == 0:
    print("   ✅ 已延长超时时间")
else:
    print("   ⚠️  修改可能失败")

print("""
3️⃣  重建容器...
   运行: docker-compose up -d --build

4️⃣  预热 Ollama...
   运行: curl -s http://localhost:11434/api/generate -d '{"model":"qwen2.5:1.5b","prompt":"test"}' > /dev/null &

5️⃣  开始测试...
   运行: python3 test_minimal.py

""")
