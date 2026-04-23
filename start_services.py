#!/usr/bin/env python3
"""
FlowerNet 启动脚本 - 启动完整服务栈（verifier/controller/outliner/generator/web）
"""

import subprocess
import time
import sys
import os
import signal


def load_dotenv_file(path):
    """Load simple KEY=VALUE pairs from a .env file."""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            env[key] = value
    return env

def run_service(name, command, cwd=None, env_override=None):
    """启动一个服务"""
    log_file = f"/tmp/{name}.log"

    print(f"🚀 启动 {name} ...")

    try:
        effective_env = {
            **os.environ,
            **(env_override or {}),
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            env=effective_env,
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

    env_from_file = load_dotenv_file(os.path.join(project_dir, ".env"))
    common_env = {
        **env_from_file,
        "UNIEVAL_ENDPOINT": env_from_file.get("UNIEVAL_ENDPOINT", "http://localhost:8004/score"),
        "REQUIRE_MULTIDIM_QUALITY": env_from_file.get("REQUIRE_MULTIDIM_QUALITY", "true"),
    }
    
    # 启动服务
    processes = {}
    
    try:
        # 先启动 UniEval，确保 verifier 能连通 endpoint
        processes['UniEval'] = run_service('UniEval', [sys.executable, "main.py", "8004"], os.path.join(os.getcwd(), 'flowernet-unieval'), common_env)
        time.sleep(6)

        # 启动完整服务栈
        processes['Verifier'] = run_service('Verifier', [sys.executable, "main.py", "8000"], os.path.join(os.getcwd(), 'flowernet-verifier'), common_env)
        time.sleep(2)

        processes['Controller'] = run_service('Controller', [sys.executable, "main.py", "8001"], os.path.join(os.getcwd(), 'flowernet-controler'), common_env)
        time.sleep(2)

        processes['Outliner'] = run_service('Outliner', [sys.executable, "main.py", "8003"], os.path.join(os.getcwd(), 'flowernet-outliner'), common_env)
        time.sleep(3)

        processes['Generator'] = run_service('Generator', [sys.executable, "main.py", "8002"], os.path.join(os.getcwd(), 'flowernet-generator'), common_env)
        time.sleep(3)

        web_env = {**os.environ, **common_env}
        web_env.update({
            "OUTLINER_URL": "http://localhost:8003",
            "GENERATOR_URL": "http://localhost:8002",
            "REQUEST_TIMEOUT": web_env.get("REQUEST_TIMEOUT", "3600"),
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        })
        print("🚀 启动 Web ...")
        processes['Web'] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"],
            cwd=os.path.join(os.getcwd(), 'flowernet-web'),
            stdout=open('/tmp/Web.log', 'w'),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
            env=web_env,
        )
        time.sleep(3)

        print("\n" + "="*50)
        print("✅ 所有服务已启动")
        print("="*50)

        print("\n📋 服务地址:")
        print("  UniEval:    http://localhost:8004")
        print("  Verifier:   http://localhost:8000")
        print("  Controller: http://localhost:8001")
        print("  Outliner:   http://localhost:8003")
        print("  Generator:  http://localhost:8002")
        print("  Web:        http://localhost:8010")

        print("\n📝 日志文件:")
        print("  /tmp/UniEval.log")
        print("  /tmp/Verifier.log")
        print("  /tmp/Controller.log")
        print("  /tmp/Outliner.log")
        print("  /tmp/Generator.log")
        print("  /tmp/Web.log")

        print("\n🧪 快速检查:")
        print("  curl -s http://localhost:8004/health/ready")
        print("  curl -s http://localhost:8010/health")
        print("  curl -s http://localhost:8002/health")
        
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
