#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 baselines/run_longwriter.py --limit 1
python3 baselines/vanilla_llm_baseline.py --topic-id fw24_007 --max-tokens 3200 --output results/week1/vanilla_outputs.json
python3 baselines/self_refine_baseline.py --topic-id fw24_007 --max-tokens 3200 --output results/week1/self_refine_outputs.json
python3 baselines/run_cogwriter.py --topic-id fw24_007 --max-tokens 3200 --output results/week1/cogwriter_outputs.json --dataset-output results/week1/cogwriter_topics.json
python3 experiments/evaluate_week1.py
