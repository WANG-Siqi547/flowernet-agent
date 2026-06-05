#!/bin/bash
set -e

WORKDIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$WORKDIR/.venv/bin/python" ]; then
  PYTHON="$WORKDIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

cd "$WORKDIR"

echo "=== Starting FlowerNet Services ==="

if [ -f "$WORKDIR/.env" ]; then
  echo "Loading .env from $WORKDIR/.env"
  set -a
  . "$WORKDIR/.env"
  set +a
else
  echo "Warning: .env not found; LLM-backed services may fail without API keys."
fi

# Enforce UniEval endpoint for verifier multidimensional checks.
export UNIEVAL_ENDPOINT="${UNIEVAL_ENDPOINT:-http://localhost:8004/score}"
export REQUIRE_MULTIDIM_QUALITY="${REQUIRE_MULTIDIM_QUALITY:-true}"
export NO_PROXY="localhost,127.0.0.1"
export no_proxy="localhost,127.0.0.1"

# Start UniEval
echo "Starting UniEval on port 8004..."
nohup $PYTHON flowernet-unieval/main.py > /tmp/unieval.log 2>&1 &
UNIEVAL_PID=$!
echo "UniEval PID: $UNIEVAL_PID"
sleep 4

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

# Start Web UI
echo "Starting Web UI on port 8010..."
(
  cd flowernet-web
  OUTLINER_URL="${OUTLINER_URL:-http://localhost:8003}" \
  GENERATOR_URL="${GENERATOR_URL:-http://localhost:8002}" \
  REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-7200}" \
  FRONTEND_SIGNATURE_ENFORCED="${FRONTEND_SIGNATURE_ENFORCED:-true}" \
  NO_PROXY="localhost,127.0.0.1" \
  no_proxy="localhost,127.0.0.1" \
  nohup "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8010 > /tmp/web.log 2>&1 &
  echo $! > /tmp/web.pid
)
WEB_PID=$(cat /tmp/web.pid)
echo "Web UI PID: $WEB_PID"
sleep 3

echo "=== Checking Services ==="
sleep 2
$PYTHON -c "
import subprocess
result = subprocess.run(['lsof', '-i', '-P', '-n'], capture_output=True, text=True)
for line in result.stdout.split('\n'):
    if any(p in line for p in [':8000', ':8001', ':8002', ':8003', ':8004', ':8010']):
        print(line)
" || echo "Services check output"

echo "✅ All services started!"
echo "Web UI: http://localhost:8010"
