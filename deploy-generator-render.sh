#!/bin/bash

# FlowerNet Generator - Render 一键部署指南
# 
# 使用方法：
# 1. 访问 https://dashboard.render.com/
# 2. 点击 "New +" → "Web Service"
# 3. 选择仓库: WANG-Siqi547/flowernet-agent
# 4. 使用以下配置：

echo "========================================="
echo "🚀 FlowerNet Generator Render 部署配置"
echo "========================================="
echo ""
echo "📋 基本配置："
echo "  Name: flowernet-generator"
echo "  Region: Singapore (Southeast Asia)"
echo "  Branch: main"
echo "  Root Directory: flowernet-generator"
echo ""
echo "🔧 运行配置："
echo "  Runtime: Python 3"
echo "  Build Command: pip install -r requirements.txt"
echo "  Start Command: python main.py 8002 gemini"
echo ""
echo "🌍 环境变量："
echo "  GOOGLE_API_KEY=AIzaSyBfB9tUHoEl0NjtuW8nNo_AXtpBGfa0REo"
echo ""
echo "💰 计费："
echo "  Instance Type: Free"
echo ""
echo "========================================="
echo "✅ 部署后你将获得类似这样的 URL："
echo "   https://flowernet-generator.onrender.com"
echo ""
echo "📖 详细步骤请查看: RENDER_DEPLOYMENT_GENERATOR.md"
echo "========================================="
