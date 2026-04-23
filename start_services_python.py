#!/usr/bin/env python3
"""Simple service starter"""
import subprocess
import sys
import time
import os

WORKDIR = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent"
PYTHON = f"{WORKDIR}/.venv/bin/python"


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
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env

services = [
    ("UniEval", "flowernet-unieval/main.py"),
    ("Verifier", "flowernet-verifier/main.py"),
    ("Controller", "flowernet-controler/main.py"),
    ("Generator", "flowernet-generator/main.py"),
    ("Outliner", "flowernet-outliner/main.py"),
]

print("killing old processes...")
subprocess.run(["pkill", "-f", "main.py"], stderr=subprocess.DEVNULL)
time.sleep(2)

print("Starting services...")
env_from_file = load_dotenv_file(f"{WORKDIR}/.env")
common_env = {
    **os.environ,
    **env_from_file,
    "UNIEVAL_ENDPOINT": os.environ.get("UNIEVAL_ENDPOINT", "http://localhost:8004/score"),
    "REQUIRE_MULTIDIM_QUALITY": os.environ.get("REQUIRE_MULTIDIM_QUALITY", "true"),
    "NO_PROXY": "localhost,127.0.0.1",
    "no_proxy": "localhost,127.0.0.1",
}

for name, script in services:
    print(f"  > Starting {name}...")
    subprocess.Popen([PYTHON, f"{WORKDIR}/{script}"], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL, 
                    cwd=WORKDIR,
                    env=common_env)
    time.sleep(3)

print("Waiting for services to initialize...")
time.sleep(5)

# Check if database exists
import os
db_path = f"{WORKDIR}/flowernet_history.db"
if os.path.exists(db_path):
    print(f"✅ Database exists: {db_path}")
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"   Tables: {tables}")
    conn.close()
else:
    print(f"❌ Database not found: {db_path}")

print("✅ Done!")
