# 🚀 FlowerNet 双端点快速启动指南

## 目标
为 Controller 和 Verifier 创建两个独立的公网 URL

```
Controller: https://xxx.ngrok-free.dev  →  localhost:8001
Verifier:   https://yyy.ngrok-free.dev  →  localhost:8000
```

## 快速启动 (3 步)

### 1️⃣ 启动 Docker 服务
```bash
cd /path/to/flowernet-agent
docker-compose up -d
```

验证服务已启动:
```bash
docker-compose ps
# 应该显示 flower-verifier 和 flower-controller 都在运行
```

### 2️⃣ 启动 Controller 隧道 (打开终端 1)

```bash
chmod +x ngrok-controller.sh
./ngrok-controller.sh
```

输出示例:
```
Session Status                online
Forwarding                     https://abc-def-ghi.ngrok-free.dev -> http://localhost:8001
```

记录 URL: **https://abc-def-ghi.ngrok-free.dev** ← Controller 的公网地址

### 3️⃣ 启动 Verifier 隧道 (打开终端 2)

```bash
chmod +x ngrok-verifier.sh
./ngrok-verifier.sh
```

输出示例:
```
Session Status                online
Forwarding                     https://xyz-uvw-rst.ngrok-free.dev -> http://localhost:8000
```

记录 URL: **https://xyz-uvw-rst.ngrok-free.dev** ← Verifier 的公网地址

## ✅ 完成！

现在你有两个独立的公网端点:
- **Controller**: https://abc-def-ghi.ngrok-free.dev
- **Verifier**: https://xyz-uvw-rst.ngrok-free.dev

## 🧪 测试

### 本地测试 (不使用 Ngrok)

```bash
# 测试 Verifier
curl http://localhost:8000/

# 测试 Controller
curl http://localhost:8001/
```

### 公网测试 (使用 Ngrok URL)

```bash
# 测试 Controller 端点
curl https://abc-def-ghi.ngrok-free.dev/

# 测试 Verifier 端点
curl https://xyz-uvw-rst.ngrok-free.dev/
```

## 📊 监控

### 查看 Ngrok 统计信息

每个 Ngrok 隧道都会在本地提供 Web UI:

- **Terminal 1 (Controller)**: http://localhost:4040
- **Terminal 2 (Verifier)**: http://localhost:4041 (如果使用不同端口的话)

### 查看 Docker 日志

```bash
# 实时查看 Verifier 日志
docker-compose logs -f verifier-app

# 实时查看 Controller 日志
docker-compose logs -f controller-app
```

## 🛑 停止服务

### 停止 Ngrok
在各终端按 **Ctrl+C**

### 停止 Docker
```bash
docker-compose down
```

## 🔧 故障排查

### 问题 1: ngrok 命令未找到

**解决方案:**
1. 安装 Homebrew (如果未安装):
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. 安装 ngrok:
```bash
brew install ngrok
```

3. 配置认证令牌:
```bash
ngrok authtoken 38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR
```

### 问题 2: Docker 容器无法启动

```bash
# 查看错误
docker-compose logs verifier-app
docker-compose logs controller-app

# 重新构建
docker-compose build --no-cache

# 重新启动
docker-compose up -d
```

### 问题 3: Ngrok 无法连接

检查网络连接:
```bash
curl https://ngrok.com
```

### 问题 4: 端口已被占用

```bash
# 查看占用 8000 的进程
lsof -i :8000

# 查看占用 8001 的进程
lsof -i :8001

# 杀死进程 (替换 <PID>)
kill -9 <PID>
```

## 📝 URL 说明

- **免费 URL**: 每次启动会改变，24 小时内有效
- **付费 URL**: 可以选择固定的子域名 (Ngrok Pro 功能)

## 💡 高级用法

### 使用不同的区域

降低延迟，选择最近的区域:

```bash
# 亚洲
ngrok http 8001 --region=ap

# 欧洲
ngrok http 8001 --region=eu

# 澳洲
ngrok http 8001 --region=au

# 美国 (默认)
ngrok http 8001 --region=us
```

### 添加密码保护

```bash
ngrok http 8001 --auth="username:password"
```

### 自定义 User-Agent

```bash
ngrok http 8001 --subdomain=mycontroller
```

## 📚 完整工作流示例

```bash
# 终端 1: 启动 Docker
cd /path/to/flowernet-agent
docker-compose up -d
sleep 10  # 等待服务启动

# 终端 2: Controller 隧道
./ngrok-controller.sh
# 记录 URL: https://abc-def-ghi.ngrok-free.dev

# 终端 3: Verifier 隧道
./ngrok-verifier.sh
# 记录 URL: https://xyz-uvw-rst.ngrok-free.dev

# 现在可以在任何地方使用这两个 URL!
```

## 🎯 下一步

1. **集成到前端**: 将公网 URL 配置到你的前端应用
2. **添加认证**: 实现 API Key 或 JWT 认证
3. **监控告警**: 设置日志聚合和性能监控
4. **升级计划**: 考虑 Ngrok Pro 以获得静态 URL 和更多功能

---

**有问题?** 检查 `DUAL_ENDPOINTS_GUIDE.md` 获取更多详细信息
