import os
import time
import requests

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

base = "http://localhost:8010"
payload = {
    "topic": "Cat Breed Guide",
    "chapter_count": 1,
    "subsection_count": 1,
    "user_background": "pet owner",
    "extra_requirements": "easy tips and practical feeding advice",
    "timeout_seconds": 1800,
}

print("[1] POST /api/generate")
start = time.time()
resp = requests.post(f"{base}/api/generate", json=payload, timeout=1900)
print("status:", resp.status_code)
if resp.status_code != 200:
    print("body:", resp.text[:1200])
    raise SystemExit(1)

result = resp.json()
print("success:", result.get("success"))
print("document_id:", result.get("document_id"))
print("title:", result.get("title"))
print("stats:", result.get("stats"))
content = result.get("content", "")
print("content_len:", len(content))
print("elapsed_sec:", round(time.time() - start, 2))

if not result.get("success") or not content:
    raise SystemExit("generation failed or empty content")

print("[2] POST /api/download-docx")
docx_resp = requests.post(
    f"{base}/api/download-docx",
    json={"title": result["title"], "content": content},
    timeout=120,
)
print("status:", docx_resp.status_code)
if docx_resp.status_code != 200:
    print("body:", docx_resp.text[:600])
    raise SystemExit(1)

out_path = "/tmp/flowernet_web_e2e.docx"
with open(out_path, "wb") as file_handle:
    file_handle.write(docx_resp.content)

print("docx_saved:", out_path)
print("docx_size_bytes:", os.path.getsize(out_path))
print("content_type:", docx_resp.headers.get("Content-Type"))
print("content_disposition:", docx_resp.headers.get("Content-Disposition"))
