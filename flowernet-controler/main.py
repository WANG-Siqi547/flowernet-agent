from fastapi import FastAPI
from pydantic import BaseModel
from controler import FlowerNetController
import os

app = FastAPI()

# 这里的生成器需要你接入自己的 LLM (OpenAI/DeepSeek 等)
def mock_llm_generator(prompt):
    return "这是模拟 LLM 生成的草稿..."

# VERIFIER_URL 应该指向 Docker Compose 中的服务名
VERIFIER_SERVICE_URL = os.getenv("VERIFIER_URL", "http://verifier-app:8000")
controller = FlowerNetController(VERIFIER_SERVICE_URL, mock_llm_generator)

class GenerateRequest(BaseModel):
    outline: str

@app.post("/process")
async def process_task(req: GenerateRequest):
    final_draft, success = controller.run_loop(req.outline)
    return {"content": final_draft, "success": success}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)