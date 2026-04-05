#!/usr/bin/env python3
"""
测试 Azure OpenAI LLM 是否能正常使用
"""

import os
import sys
import json

# Load .env file manually
env_file = ".env"
if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key not in os.environ:  # Don't override existing env vars
                    os.environ[key.strip()] = value.strip()

# Check required Azure environment variables
print("=" * 60)
print("Azure OpenAI Configuration Check")
print("=" * 60)

azure_api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
azure_api_base = os.getenv("AZURE_OPENAI_API_BASE", "").strip()
azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip()
azure_model = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o-mini").strip()
azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview").strip()

print(f"\n✓ AZURE_OPENAI_API_KEY: {'✓ Set' if azure_api_key else '✗ NOT SET'}")
print(f"✓ AZURE_OPENAI_API_BASE: {azure_api_base if azure_api_base else '✗ NOT SET'}")
print(f"✓ AZURE_OPENAI_DEPLOYMENT_NAME: {azure_deployment if azure_deployment else '✗ NOT SET'}")
print(f"✓ AZURE_OPENAI_MODEL: {azure_model}")
print(f"✓ AZURE_OPENAI_API_VERSION: {azure_version}")

# Check if all required fields are set
if not all([azure_api_key, azure_api_base, azure_deployment]):
    print("\n❌ Missing required Azure configuration!")
    print("Please set the following environment variables:")
    print("  - AZURE_OPENAI_API_KEY")
    print("  - AZURE_OPENAI_API_BASE")
    print("  - AZURE_OPENAI_DEPLOYMENT_NAME")
    sys.exit(1)

print("\n✓ All Azure configuration found!")

# Test Azure API call
print("\n" + "=" * 60)
print("Testing Azure OpenAI API Call")
print("=" * 60)

import requests

try:
    base = azure_api_base.rstrip("/")
    if not base.endswith("/openai"):
        base = f"{base}/openai"
    
    url = f"{base}/deployments/{azure_deployment}/chat/completions"
    print(f"\n📍 URL: {url}")
    
    payload = {
        "messages": [{"role": "user", "content": "Say 'Hello from Azure!' in English. Keep it concise."}],
        "temperature": 0.7,
        "max_tokens": 100,
        "model": azure_model,
    }
    
    headers = {
        "api-key": azure_api_key,
        "Content-Type": "application/json",
    }
    
    params = {"api-version": azure_version}
    
    print(f"🔄 Sending request...")
    print(f"   Model: {azure_model}")
    print(f"   Deployment: {azure_deployment}")
    
    response = requests.post(
        url,
        params=params,
        json=payload,
        headers=headers,
        timeout=30,
    )
    
    print(f"\n📊 Response Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    
    response.raise_for_status()
    
    data = response.json()
    print(f"\n✅ Azure API Response (raw):")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:500])
    
    # Parse the response
    choice = ((data.get("choices") or [{}])[0] or {})
    msg = choice.get("message") or {}
    content = msg.get("content", "")
    
    if isinstance(content, list):
        parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
        content = "".join(parts)
    
    draft_text = str(content).strip()
    
    if draft_text:
        print(f"\n🎉 SUCCESS! Azure LLM Response:")
        print(f"─" * 60)
        print(draft_text)
        print(f"─" * 60)
        print(f"\n✓ Azure OpenAI is working correctly!")
        sys.exit(0)
    else:
        print(f"\n⚠️  Response received but content is empty")
        sys.exit(1)

except requests.exceptions.Timeout:
    print(f"\n❌ Request timeout - Azure API took too long to respond")
    sys.exit(1)
except requests.exceptions.ConnectionError as e:
    print(f"\n❌ Connection error: {e}")
    print(f"   Check if AZURE_OPENAI_API_BASE is correct and reachable")
    sys.exit(1)
except requests.exceptions.HTTPError as e:
    print(f"\n❌ HTTP Error: {response.status_code}")
    print(f"   Response: {response.text[:500]}")
    try:
        error_data = response.json()
        if "error" in error_data:
            print(f"   Error details: {error_data['error']}")
    except:
        pass
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Unexpected error: {type(e).__name__}: {e}")
    sys.exit(1)
