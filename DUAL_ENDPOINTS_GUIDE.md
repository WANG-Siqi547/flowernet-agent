# FlowerNet 双独立端点部署指南

## 架构说明

本指南展示如何为 FlowerNet 的 Controller 和 Verifier 服务创建两个独立的 Ngrok 公网端点。

```
┌─────────────────────────────────────────────────────────────┐
│                     外网访问                                  │
│  Controller: https://xxx.ngrok-free.dev                     │
│  Verifier:   https://yyy.ngrok-free.dev                     │
└─────────────────────────────────────────────────────────────┘
         ↓                              ↓
┌──────────────────┐      ┌──────────────────┐
│  Ngrok Process 1  │      │  Ngrok Process 2  │
│  (8001 tunnel)    │      │  (8000 tunnel)    │
└──────────────────┘      └──────────────────┘
         ↓                              ↓
┌──────────────────────────────────────────────────────┐
│            Docker Compose Network (127.0.0.1)        │
│                                                        │
│  ┌─────────────────┐         ┌──────────────────┐   │
│  │ Controller App   │         │  Verifier App    │   │
│  │ (localhost:8001) │         │ (localhost:8000) │   │
│  └─────────────────┘         └──────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## 为什么使用主机 Ngrok 而不是 Docker 容器?

**问题所在:**
- Docker for Mac 有网络隔离限制
- 容器内部无法可靠地连接到外部 Ngrok 认证服务器
- 导致 `network is unreachable` 错误

**解决方案:**
- 在主机上运行 Ngrok 进程
- 避免 Docker 网络限制
- 更简洁、更稳定、更易调试

## 快速开始

### 步骤 1: 安装 Ngrok

**macOS:**
```bash
brew install ngrok
```

**或访问:** https://ngrok.com/download

### 步骤 2: 启动 Docker 服务

```bash
cd /path/to/flowernet-agent

# 停止任何旧容器
docker-compose down

# 启动 Controller 和 Verifier
docker-compose up -d

# 验证服务已启动
docker-compose ps
```

输出应该显示:
- ✅ flower-verifier (running)
- ✅ flower-controller (running)

### 步骤 3: 启动 Ngrok 隧道

**方式 A: 使用自动化脚本 (推荐)**
```bash
chmod +x start-ngrok-tunnels.sh
./start-ngrok-tunnels.sh
```

**方式 B: 手动启动两个隧道**

打开两个终端窗口:

终端 1 - Controller 隧道:
```bash
ngrok authtoken 38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR
ngrok http 8001 --region=us
```

终端 2 - Verifier 隧道:
```bash
ngrok authtoken 38bwDJs8sMknK17RpFvQYzbje6A_4n2bZFtn2gao8U4qCf7gR
ngrok http 8000 --region=us
```

### 步骤 4: 获取公网 URL

每个 Ngrok 进程会输出类似:
```
ngrok                                       (Ctrl+C to quit)

Session Status                online
Account                       k1ns9sley
Version                        3.x.x
Region                         United States (us)
Forwarding                     https://xxx-yyy-zzz.ngrok-free.dev -> http://localhost:8001
```

记录两个 URL:
- **Controller URL**: `https://xxx-yyy-zzz.ngrok-free.dev` (从第一个隧道)
- **Verifier URL**: `https://aaa-bbb-ccc.ngrok-free.dev` (从第二个隧道)

## 使用端点

### Controller 端点

```bash
# 生成内容
curl -X POST https://xxx-yyy-zzz.ngrok-free.dev/process \
  -H "Content-Type: application/json" \
  -d '{
    "outline": "article about machine learning",
    "max_iterations": 3
  }'
```

### Verifier 端点

```bash
# 验证内容
curl -X POST https://aaa-bbb-ccc.ngrok-free.dev/verify \
  -H "Content-Type: application/json" \
  -d '{
    "generated_text": "...",
    "source_text": "...",
    "topic": "machine learning"
  }'
```

## 故障排查

### 问题 1: ngrok 命令未找到

**解决方案:**
```bash
# 确认 ngrok 安装
which ngrok

# 如果未安装
brew install ngrok

# 重新添加到 PATH
export PATH="/usr/local/bin:$PATH"
```

### 问题 2: Ngrok 无法连接到认证服务器

**原因:** 网络问题或代理设置

**解决方案:**
```bash
# 验证网络连接
curl https://api.ngrok.com

# 检查代理设置
env | grep -i proxy

# 尝试使用 VPN 或检查防火墙
```

### 问题 3: Docker 容器无法启动

**解决方案:**
```bash
# 检查日志
docker-compose logs verifier-app
docker-compose logs controller-app

# 重建镜像
docker-compose build --no-cache

# 再次启动
docker-compose up -d
```

### 问题 4: 本地测试（不使用 Ngrok）

```bash
# 直接访问本地端口

# Verifier
curl http://localhost:8000/

# Controller
curl http://localhost:8001/
```

## 监控端点状态

### 方式 1: Ngrok Web UI

Ngrok 会在本地创建检查界面:
- Controller: `http://localhost:4040`
- Verifier: `http://localhost:4041` (如果在不同进程)

### 方式 2: 检查 Docker 日志

```bash
# 实时查看日志
docker-compose logs -f verifier-app
docker-compose logs -f controller-app
```

### 方式 3: 健康检查

```bash
# Controller 健康检查
curl http://localhost:8001/

# Verifier 健康检查
curl http://localhost:8000/
```

## 优雅停止

### 停止 Ngrok

按 `Ctrl+C` 在每个 Ngrok 窗口中

### 停止 Docker 服务

```bash
docker-compose down
```

## 高级配置

### 自定义区域

Ngrok 支持多个区域以降低延迟:

```bash
# 亚洲区域
ngrok http 8001 --region=ap

# 欧洲区域
ngrok http 8001 --region=eu

# 澳洲区域
ngrok http 8001 --region=au
```

### 自定义子域名 (付费功能)

```bash
# 预留子域名
ngrok http 8001 --subdomain=mycontroller

# 结果: https://mycontroller.ngrok-free.dev
```

## 环境变量配置

在 `docker-compose.yml` 中更新:

```yaml
controller-app:
  environment:
    - VERIFIER_URL=http://verifier-app:8000
    - CONTROLLER_PUBLIC_URL=https://xxx-yyy-zzz.ngrok-free.dev
    - VERIFIER_PUBLIC_URL=https://aaa-bbb-ccc.ngrok-free.dev
```

## 备注

- 免费 Ngrok URL 每 2 小时重新生成一次
- 付费计划可获得静态 URL
- Ngrok 隧道密度限制: 免费账户最多 4 个同时隧道

## 下一步

1. 将公网 URL 配置到你的前端应用
2. 添加认证层 (API Keys, JWT)
3. 设置监控和告警
4. 升级到 Ngrok 付费计划以获得静态 URL
