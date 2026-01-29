# 🎯 FlowerNet 双端点设置完成总结

## ✅ 已完成的内容

### 1. Docker 服务
- ✅ Verifier 服务运行在 **localhost:8000**
- ✅ Controller 服务运行在 **localhost:8001**
- ✅ 两个服务已成功启动并健康

### 2. 可执行脚本
```
flowernet-agent/
├── ngrok-controller.sh          ← 启动 Controller 隧道
├── ngrok-verifier.sh             ← 启动 Verifier 隧道
├── start-dual-endpoints.sh       ← 一键启动脚本 (支持 tmux)
└── setup-dual-endpoints.sh       ← 初始化脚本
```

### 3. 文档
```
flowernet-agent/
├── QUICK_START_DUAL_ENDPOINTS.md      ← 快速开始 (3 步)
├── DUAL_ENDPOINTS_GUIDE.md            ← 完整指南
├── ARCHITECTURE_DUAL_ENDPOINTS.md     ← 架构说明
└── 本文件: README_DUAL_ENDPOINTS.md
```

## 🚀 立即开始 (3 步)

### 步骤 1️⃣ : 验证 Docker 运行
```bash
cd /path/to/flowernet-agent
docker-compose ps

# 应该显示:
# flower-verifier    UP (healthy)   0.0.0.0:8000->8000/tcp
# flower-controller  UP            0.0.0.0:8001->8001/tcp
```

### 步骤 2️⃣ : 启动 Controller 隧道 (终端 1)
```bash
chmod +x ngrok-controller.sh
./ngrok-controller.sh
```

**输出示例:**
```
Session Status                online

Forwarding                     https://abc-xxx.ngrok-free.dev -> http://localhost:8001
```

👉 **记录这个 URL**: `https://abc-xxx.ngrok-free.dev`

### 步骤 3️⃣ : 启动 Verifier 隧道 (终端 2)
```bash
chmod +x ngrok-verifier.sh
./ngrok-verifier.sh
```

**输出示例:**
```
Session Status                online

Forwarding                     https://xyz-yyy.ngrok-free.dev -> http://localhost:8000
```

👉 **记录这个 URL**: `https://xyz-yyy.ngrok-free.dev`

## ✨ 完成！你现在有两个公网端点

```
🔵 Controller Public URL:  https://abc-xxx.ngrok-free.dev
   └─ 本地: http://localhost:8001
   
🔴 Verifier Public URL:    https://xyz-yyy.ngrok-free.dev
   └─ 本地: http://localhost:8000
```

## 🧪 测试端点

### 本地测试
```bash
# 测试 Verifier
curl http://localhost:8000/

# 测试 Controller
curl http://localhost:8001/
```

### 公网测试
```bash
# 测试 Controller 公网地址
curl https://abc-xxx.ngrok-free.dev/

# 测试 Verifier 公网地址
curl https://xyz-yyy.ngrok-free.dev/
```

## 📊 监控

### 查看 Ngrok 统计

每个 Ngrok 进程都提供本地 Web UI:

```
Controller: http://localhost:4040
├─ 查看所有 HTTP 请求/响应
├─ 性能统计
└─ 连接详情
```

### 查看 Docker 日志
```bash
# 实时查看 Controller 日志
docker-compose logs -f controller-app

# 实时查看 Verifier 日志
docker-compose logs -f verifier-app
```

## 💡 高级启动方式

### 方式 A: 使用 tmux 自动启动两个隧道
```bash
./start-dual-endpoints.sh
# 选择选项 1
```

这会自动在 tmux 中创建两个窗口并分别启动两个 Ngrok 隧道。

### 方式 B: 手动启动 (最推荐用于调试)
```bash
# 终端 1
./ngrok-controller.sh

# 终端 2 (新窗口)
./ngrok-verifier.sh
```

## 🔄 工作流示例

### 场景: 部署到生产环境

1. **配置环境变量**
   ```bash
   export CONTROLLER_URL="https://abc-xxx.ngrok-free.dev"
   export VERIFIER_URL="https://xyz-yyy.ngrok-free.dev"
   ```

2. **在前端应用中使用**
   ```javascript
   const API_ENDPOINTS = {
     controller: process.env.CONTROLLER_URL,
     verifier: process.env.VERIFIER_URL
   };
   
   // 调用 Controller API
   fetch(`${API_ENDPOINTS.controller}/process`, {...})
   
   // 调用 Verifier API
   fetch(`${API_ENDPOINTS.verifier}/verify`, {...})
   ```

3. **监控生产环境**
   - 定期检查 Ngrok Web UI (http://localhost:4040)
   - 监控 Docker 日志
   - 设置告警规则

## 🛑 停止服务

### 停止 Ngrok 隧道
```bash
# 在各隧道窗口按 Ctrl+C
```

### 停止 Docker 服务
```bash
docker-compose down
```

### 完全清理
```bash
docker-compose down -v  # 删除所有卷
docker system prune -a  # 清理所有不用的 Docker 资源
```

## 📝 架构一览

```
┌─────────────────────────────────────────┐
│        外网访问 (Ngrok 隧道)              │
├─────────────────────────────────────────┤
│ Controller: https://abc-xxx...  (8001)   │
│ Verifier:   https://xyz-yyy...  (8000)   │
├─────────────────────────────────────────┤
│     本地应用访问 (Docker)                 │
├─────────────────────────────────────────┤
│ Controller: http://localhost:8001        │
│ Verifier:   http://localhost:8000        │
├─────────────────────────────────────────┤
│   内部通信 (Docker Network)               │
├─────────────────────────────────────────┤
│ Controller → Verifier:                   │
│   http://verifier-app:8000               │
└─────────────────────────────────────────┘
```

## 🔍 故障排查

### Q: Ngrok 找不到
**A:** 需要安装 Homebrew 和 Ngrok
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install ngrok
```

### Q: Docker 容器无法启动
**A:** 检查 Docker 日志
```bash
docker-compose logs verifier-app
docker-compose logs controller-app
```

### Q: 公网 URL 无法访问
**A:** 验证 Ngrok 隧道正在运行
```bash
curl http://localhost:4040/api/tunnels  # 查看隧道状态
```

### Q: 端口已被占用
**A:** 检查占用的进程
```bash
lsof -i :8000
lsof -i :8001
lsof -i :4040

# 杀死进程 (替换 <PID>)
kill -9 <PID>
```

## 📚 更多文档

- **快速开始**: `QUICK_START_DUAL_ENDPOINTS.md`
- **完整指南**: `DUAL_ENDPOINTS_GUIDE.md`
- **架构详解**: `ARCHITECTURE_DUAL_ENDPOINTS.md`

## 🎯 下一步

1. ✅ **启动 Docker 和 Ngrok** (已完成)
2. ⏭️ **集成到前端** - 配置前端应用使用公网 URL
3. ⏭️ **添加认证** - 实现 API Key 或 JWT 认证
4. ⏭️ **监控和告警** - 设置日志聚合和性能监控
5. ⏭️ **升级计划** - 考虑 Ngrok Pro 以获得静态 URL

## 🆘 需要帮助?

1. 检查 `QUICK_START_DUAL_ENDPOINTS.md` 快速开始指南
2. 查阅 `DUAL_ENDPOINTS_GUIDE.md` 完整故障排查
3. 参考 `ARCHITECTURE_DUAL_ENDPOINTS.md` 架构说明
4. 查看 Docker 日志: `docker-compose logs`

---

**系统信息:**
- Docker Desktop 已就绪 ✓
- Controller 服务: http://localhost:8001 ✓
- Verifier 服务: http://localhost:8000 ✓
- Ngrok Token 已配置 ✓

**现在可以立即启动双端点了！** 🚀
