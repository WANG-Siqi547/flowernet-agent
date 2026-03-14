#!/usr/bin/env python3
"""
获取当前 Ollama Ngrok 隧道 URL 的辅助脚本
"""
import subprocess
import json
import sys
import urllib.request

def get_ollama_ngrok_url():
    """从 ngrok API 获取 Ollama 隧道的公网 URL"""
    try:
        # 查询 ngrok API
        response = urllib.request.urlopen('http://localhost:4040/api/tunnels', timeout=5)
        data = json.loads(response.read().decode())
        
        # 查找 localhost:11434 的隧道
        for tunnel in data.get('tunnels', []):
            if '11434' in tunnel.get('config', {}).get('addr', ''):
                return tunnel['public_url']
        
        # 如果没找到具体端口，就返回第一个隧道
        if data.get('tunnels'):
            return data['tunnels'][0]['public_url']
        
        return None
    except Exception as e:
        return None

def check_ngrok_running():
    """检查 ngrok 是否在运行"""
    try:
        subprocess.run(['pgrep', '-f', 'ngrok.*11434'], 
                      check=True, 
                      capture_output=True,
                      timeout=2)
        return True
    except:
        return False

def main():
    print("🔍 检查 Ollama Ngrok 隧道状态...")
    print()
    
    # 检查 ngrok 是否在运行
    if not check_ngrok_running():
        print("❌ Ngrok 隧道未运行")
        print()
        print("启动隧道:")
        print('  chmod +x ./start-ollama-ngrok.sh')
        print('  ./start-ollama-ngrok.sh')
        return 1
    
    # 尝试获取 URL
    url = get_ollama_ngrok_url()
    if not url:
        print("⚠️  无法获取隧道 URL")
        print()
        print("请检查:")
        print("  1. Ngrok 是否正确启动")
        print("  2. Ollama 是否在运行 (http://localhost:11434)")
        return 1
    
    print("✅ Ngrok 隧道运行中")
    print()
    print(f"📡 Ollama 公网 URL:")
    print(f"   {url}")
    print()
    print("📋 使用此 URL 更新 Render 环境变量:")
    print(f"   OLLAMA_URL={url}")
    print("   提示: 当前 render.yaml 已更新为最近一次可用地址；如果隧道重启，请重新同步该值")
    print()
    print("🌐 更新以下 Render 服务中的环境变量:")
    print("   - flowernet-outliner")
    print("   - flowernet-generator")
    print()
    print("⏰ 部署后验证:")
    print("   curl -i -X POST https://flowernet-web.onrender.com/api/poffices/generate \\")
    print('     -H "Content-Type: application/json" -d \'{"query":"test"}\'')
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
