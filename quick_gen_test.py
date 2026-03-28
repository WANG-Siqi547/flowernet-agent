#!/usr/bin/env python3
"""Quick test for generator hang"""
import time
import requests
import sys
import os

# Disable SSL verification and proxy
requests.packages.urllib3.disable_warnings()
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

print("Creating outline...")
print("  URL: http://localhost:8003/outline/generate-and-save")
payload = {
    'document_id': f'quick_test_{int(time.time())}', 
    'user_background': 'Test',
    'user_requirements': 'Test',
    'max_sections': 1,
    'max_subsections_per_section': 1
}
print(f"  Payload: {payload}")

try:
    r = requests.post(
        'http://localhost:8003/outline/generate-and-save',
        json=payload,
        timeout=180,
        verify=False
    )
    print(f"  Status: {r.status_code}")
    if r.status_code != 200:
        print(f"  Error body: {r.text[:300]}")
        sys.exit(1)

    data = r.json()
    print(f'Outline OK: {data.get("document_id")}')
except Exception as e:
    print(f'Exception: {e}')
    sys.exit(1)

print(f'Calling generator with 30s timeout...')
sys.stdout.flush()

start = time.time()
try:
    r = requests.post(
        'http://localhost:8002/generate_document',
        json={
            'document_id': data['document_id'],
            'title': data.get('document_title'),
            'structure': data.get('structure'),
            'content_prompts': data.get('content_prompts'),
            'user_background': 'Test',
            'user_requirements': 'Test',
            'rel_threshold': 0.75,
            'red_threshold': 0.50
        },
        timeout=30,
        verify=False
    )
    elapsed = time.time() - start
    print(f'Generator response received in {elapsed:.0f}s: HTTP {r.status_code}')
    if r.status_code == 200:
        print("SUCCESS!")
    else:
        print(f"Error: {r.text[:500]}")
except requests.Timeout:
    elapsed = time.time() - start
    print(f'TIMEOUT after {elapsed:.0f}s')
except Exception as e:
    elapsed = time.time() - start
    print(f'ERROR after {elapsed:.0f}s: {type(e).__name__}: {e}')
