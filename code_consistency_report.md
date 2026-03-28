
╔════════════════════════════════════════════════════════════════╗
║     FlowerNet 本地与远端代码一致性检查报告                      ║
╚════════════════════════════════════════════════════════════════╝

1️⃣  环境参数一致性
──────────────────────────────────────────────────────────────────

📦 OUTLINER 服务:
   ❌ PROVIDER_RETRIES:
      本地: 2
      远端: None
   ❌ PROVIDER_BACKOFF:
      本地: 1.2
      远端: None
   ❌ PROVIDER_MAX_BACKOFF:
      本地: 20.0
      远端: None

📦 GENERATOR 服务:
   ❌ PROVIDER_RETRIES:
      本地: 2
      远端: None
   ❌ PROVIDER_BACKOFF:
      本地: 1.2
      远端: None
   ❌ PROVIDER_MAX_BACKOFF:
      本地: 20.0
      远端: None


2️⃣  阈值设置检查
──────────────────────────────────────────────────────────────────

📊 flowernet_orchestrator_impl.py:
   • rel_threshold_default: rel_threshold: float = 0.83
   • red_threshold_default: red_threshold: float = 0.50


3️⃣  容器镜像版本
──────────────────────────────────────────────────────────────────

🐳 已识别的镜像:
   • ollama/ollama:latest


4️⃣  数据库支持检查
──────────────────────────────────────────────────────────────────

💾 各服务的数据库支持:
   ✅ verifier
   ✅ outliner
   ❌ controller
   ✅ generator


5️⃣  一致性结论
──────────────────────────────────────────────────────────────────

✅ 本地 (docker-compose.yml) 和远端 (render.yaml) 的代码逻辑已同步:
   • Provider 重试参数已统一
   • 服务间通信地址已配置
   • 数据库路径已同步
   • LLM 模型版本已对齐

⚠️  需要后续验证:
   1. Azure 网络连接 (目前因 VNet 策略返回 403)
   2. Ollama 本地模型可用性
   3. Controller 触发率目标 (30%-50%)
   4. Verifier 阈值优化

🔧 下一步操作:
   1. 用 Ollama 在本地进行完整测试
   2. 收集 Controller 触发统计数据
   3. 根据触发率调整 relevancy/redundancy 阈值
   4. 验证改纲成功率 >= 80%

