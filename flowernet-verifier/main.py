from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
from verifier import FlowerNetVerifier  # 导入你刚刚跑通的算法类

# 1. 定义数据格式 (Pydantic 模型)
# 这样 FastAPI 会自动帮你检查收到的数据对不对
class VerifyRequest(BaseModel):
    draft: str                  # 当前生成的草稿
    outline: str                # 对应的大纲/任务要求
    history: List[str] = []     # 之前已经生成的章节内容列表（用于查重）
    rel_threshold: Optional[float] = 0.6  # 可选：自定义相关性阈值
    red_threshold: Optional[float] = 0.7  # 可选：自定义冗余度阈值

# 2. 初始化应用
app = FastAPI(title="FlowerNet Verifying Layer API")

# 全局 verifier 对象（延迟初始化）
_verifier = None

def get_verifier():
    """延迟初始化 verifier（首次使用时才创建）"""
    global _verifier
    if _verifier is None:
        print("⏳ 首次初始化 Verifier...")
        _verifier = FlowerNetVerifier()
        print("✅ Verifier 已初始化")
    return _verifier

print("🚀 FlowerNet API 启动（Verifier 将按需初始化）...")

# 3. 定义根目录（用于检查服务是否存活）
@app.get("/")
def read_root():
    return {"status": "online", "message": "FlowerNet Verifying Layer is ready."}

# 4. 定义核心验证接口
@app.post("/verify")
async def perform_verification(request: VerifyRequest):
    try:
        # 获取或创建 verifier（延迟初始化）
        verifier = get_verifier()
        # 调用 verifier.py 中的 verify 方法
        result = verifier.verify(
            draft=request.draft,
            outline=request.outline,
            history_list=request.history,
            rel_threshold=request.rel_threshold,
            red_threshold=request.red_threshold
        )
        return result
    except Exception as e:
        # 如果代码出错，返回 500 错误
        raise HTTPException(status_code=500, detail=str(e))

# 5. 本地直接运行脚本的快捷入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

