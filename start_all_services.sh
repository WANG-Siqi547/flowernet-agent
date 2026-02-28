#!/bin/bash
set -e

PYTHON="/Users/k1ns9sley/Desktop/msc project/flowernet-agent/.venv/bin/python"
WORKDIR="/Users/k1ns9sley/Desktop/msc project/flowernet-agent"

cd "$WORKDIR"

echo "=== Starting FlowerNet Services ==="

# Start Verifier
echo "Starting Verifier on port 8000..."
nohup $PYTHON flowernet-verifier/main.py > /tmp/verifier.log 2>&1 &
VERIFIER_PID=$!
echo "Verifier PID: $VERIFIER_PID"
sleep 2

# Start Controller  
echo "Starting Controller on port 8001..."
nohup $PYTHON flowernet-controler/main.py > /tmp/controller.log 2>&1 &
CONTROLLER_PID=$!
echo "Controller PID: $CONTROLLER_PID"
sleep 2

# Start Generator
echo "Starting Generator on port 8002..."
nohup $PYTHON flowernet-generator/main.py > /tmp/generator.log 2>&1 &
GENERATOR_PID=$!
echo "Generator PID: $GENERATOR_PID"
sleep 2

# Start Outliner
echo "Starting Outliner on port 8003..."
nohup $PYTHON flowernet-outliner/main.py > /tmp/outliner.log 2>&1 &
OUTLINER_PID=$!
echo "Outliner PID: $OUTLINER_PID"
sleep 3

echo "=== Checking Services ==="
sleep 2
$PYTHON -c "
import subprocess
result = subprocess.run(['lsof', '-i', '-P', '-n'], capture_output=True, text=True)
for line in result.stdout.split('\n'):
    if any(p in line for p in [':8000', ':8001', ':8002', ':8003']):
        print(line)
" || echo "Services check output"

echo "✅ All services started!"
