#!/usr/bin/env python3
"""Simple service starter"""
import subprocess
import sys
import time

WORKDIR = "/Users/k1ns9sley/Desktop/msc project/flowernet-agent"
PYTHON = f"{WORKDIR}/.venv/bin/python"

services = [
    ("Verifier", "flowernet-verifier/main.py"),
    ("Controller", "flowernet-controler/main.py"),
    ("Generator", "flowernet-generator/main.py"),
    ("Outliner", "flowernet-outliner/main.py"),
]

print("killing old processes...")
subprocess.run(["pkill", "-f", "main.py"], stderr=subprocess.DEVNULL)
time.sleep(2)

print("Starting services...")
for name, script in services:
    print(f"  > Starting {name}...")
    subprocess.Popen([PYTHON, f"{WORKDIR}/{script}"], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL, 
                    cwd=WORKDIR)
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
