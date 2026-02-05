#!/usr/bin/env python3
"""
简化的 FlowerNet 启动脚本 - 同时启动所有三个服务
"""

import subprocess
import time
import sys
import os
import signal

def run_service(name, port, service_dir):
    """启动一个服务"""
    cwd = os.path.join(os.getcwd(), service_dir)
    log_file = f"/tmp/{name}.log"
    
    print(f"🚀 启动 {name} (端口 {port})...")
    
    try:
        process = subprocess.Popen(
            [sys.executable, "main.py", str(port)],
            cwd=cwd,
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid  # 创建新的进程组
        )
        return process
    except Exception as e:
        print(f"❌ {name} 启动失败: {e}")
        return None

def main():
    print("\n" + "="*50)
    print("🌸 FlowerNet 启动脚本")
    print("="*50)
    
    # 更改到项目目录
    project_dir = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent"
    os.chdir(project_dir)
    
    # 启动服务
    processes = {}
    
    try:
        # 启动三个服务
        processes['Verifier'] = run_service('Verifier', 8000, 'flowernet-verifier')
        time.sleep(2)
        
        processes['Controller'] = run_service('Controller', 8001, 'flowernet-controler')
        time.sleep(2)
        
        processes['Generator'] = run_service('Generator', 8002, 'flowernet-generator')
        time.sleep(3)
        
        # 检查服务
        print("\n" + "="*50)
        print("✅ 所有服务已启动")
        print("="*50)
        
        print("\n📋 服务地址:")
        print("  Generator:  http://localhost:8002")
        print("  Verifier:   http://localhost:8000")
        print("  Controller: http://localhost:8001")
        
        print("\n📝 日志文件:")
        print("  /tmp/Generator.log")
        print("  /tmp/Verifier.log")
        print("  /tmp/Controller.log")
        
        print("\n🧪 运行测试:")
        print("  python3 test_flowernet_e2e.py")
        
        print("\n按 Ctrl+C 停止所有服务...\n")
        
        # 保持运行
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n🛑 停止所有服务...")
        for name, proc in processes.items():
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=5)
                except:
                    pass
        print("✅ 服务已停止")
        sys.exit(0)

if __name__ == "__main__":
    main()
