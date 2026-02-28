#!/usr/bin/env python3
"""Service Starter with Error Handling"""
import subprocess
import time
import sys
import os

os.chdir("/Users/k1ns9sley/Desktop/msc project/flowernet-agent")
PYTHON = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/.venv/bin/python"

# Kill existing processes
print("[1] Killing old services...")
os.system("ps aux | grep '[p]ython.*main' | awk '{print $2}' | xargs kill -9 2>/dev/null || true")
time.sleep(2)

service_configs = [
    ("Verifier", "flowernet-verifier/main.py", 8000),
    ("Controller", "flowernet-controler/main.py", 8001),
    ("Generator", "flowernet-generator/main.py", 8002),
    ("Outliner", "flowernet-outliner/main.py", 8003),
]

print("[2] Starting services...")
for name, script, port in service_configs:
    print(f"   • {name} (port {port})...", end=" ", flush=True)
    try:
        p = subprocess.Popen([PYTHON, script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"PID {p.pid}")
        time.sleep(3)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

print(f"\n[3] Waiting for initialization...")
time.sleep(5)

# Verify database
print("[4] Checking database schema...")
db_path = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet_history.db"
import sqlite3
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"✅ Database tables: {', '.join(tables) if tables else 'NONE (empty)'}")
    if len(tables) == 0:
        print("   ⚠️  Database empty - services may not have initialized properly")
    conn.close()
except Exception as e:
    print(f"❌ Database error: {e}")

# Check ports
print("[5] Checking service ports...")
for name, _, port in service_configs:
    result = os.system(f"lsof -i :{port} > /dev/null 2>&1")
    status = "✅" if result == 0 else "❌"
    print(f"   {status} {name} port {port}")

print("\n✅ Setup complete!")
