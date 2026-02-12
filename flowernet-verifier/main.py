from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import numpy as np
import jieba
import os
from rank_bm25 import BM25Okapi
from rouge_score import rouge_scorer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation

from history_store import HistoryManager


# ============ FlowerNetVerifier 轻量级版本 ============
class FlowerNetVerifier:
    def __init__(self):
        print("🌸 FlowerNet 验证层启动（轻量级模式）")
        self.public_url = os.getenv('VERIFIER_PUBLIC_URL', 'http://localhost:8000')
        print(f"  - Verifier Public URL: {self.public_url}")
        self.scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
        self.vectorizer = CountVectorizer(stop_words='english', max_features=1000)
        print("✅ 验证层就绪（仅使用传统 NLP）")

    def _tokenize(self, text):
        """分词"""
        tokens = [t.strip().lower() for t in jieba.cut(text)]
        return [t for t in tokens if t]

    def calculate_relevancy(self, draft, outline):
        """计算草稿与大纲的相关性"""
        outline_tokens = [w for w in self._tokenize(outline) if len(w) > 1]
        draft_tokens = [w for w in self._tokenize(draft) if len(w) > 1]
        
        if outline_tokens:
            outline_keywords = set(outline_tokens)
            draft_keywords = set(draft_tokens)
            keyword_coverage = len(outline_keywords & draft_keywords) / len(outline_keywords)
        else:
            keyword_coverage = 0.0

        try:
            bm25 = BM25Okapi([outline_tokens, draft_tokens])
            bm25_score = bm25.get_scores(draft_tokens)[0] / (bm25.get_scores(draft_tokens).max() + 1e-9)
        except:
            bm25_score = 0.5

        length_score = min(len(draft) / max(len(outline), 1), 1.0)
        # 提高关键词覆盖率权重，降低长度影响
        relevancy_score = (keyword_coverage * 0.6) + (bm25_score * 0.3) + (length_score * 0.1)

        return {
            "score": float(round(relevancy_score, 4)),
            "details": {
                "keyword_coverage": float(keyword_coverage),
                "bm25_similarity": float(bm25_score),
                "length_score": float(length_score),
            },
        }

    def calculate_redundancy(self, draft, history_list):
        """计算冗余度"""
        if not history_list:
            return {"score": 0.0, "details": "No history yet"}

        all_histories = " ".join(history_list)
        draft_tokens_list = [t for t in self._tokenize(draft) if len(t) > 1]
        history_tokens_list = [t for t in self._tokenize(all_histories) if len(t) > 1]
        draft_tokens_set = set(draft_tokens_list)
        history_tokens_set = set(history_tokens_list)

        if draft_tokens_set:
            token_overlap = len(draft_tokens_set & history_tokens_set) / len(draft_tokens_set)
        else:
            token_overlap = 0.0

        draft_bigrams = set(zip(draft_tokens_list, draft_tokens_list[1:]))
        history_bigrams = set(zip(history_tokens_list, history_tokens_list[1:]))
        
        if draft_bigrams:
            bigram_overlap = len(draft_bigrams & history_bigrams) / len(draft_bigrams)
        else:
            bigram_overlap = 0.0

        # 提高单词重叠权重，更严格检测冗余
        redundancy_score = (token_overlap * 0.7) + (bigram_overlap * 0.3)

        return {
            "score": float(round(redundancy_score, 4)),
            "details": {
                "token_overlap": float(token_overlap),
                "bigram_overlap": float(bigram_overlap),
            },
        }

    def verify(self, draft, outline, history_list, rel_threshold=0.4, red_threshold=0.6):
        """一键验证逻辑"""
        rel = self.calculate_relevancy(draft, outline)
        red = self.calculate_redundancy(draft, history_list)

        is_passed = (rel['score'] >= rel_threshold) and (red['score'] <= red_threshold)

        advice = "Content looks good."
        if rel['score'] < rel_threshold:
            advice = "Content is deviating from the outline. Add more focus on the section mission."
        if red['score'] > red_threshold:
            advice = "Content is redundant with previous sections. Provide new information."

        return {
            "is_passed": is_passed,
            "relevancy_index": rel['score'],
            "redundancy_index": red['score'],
            "feedback": advice,
            "raw_data": {"relevancy": rel['details'], "redundancy": red['details']}
        }


# ============ FastAPI 应用 ============

# 1. 定义数据格式 (Pydantic 模型)
# 这样 FastAPI 会自动帮你检查收到的数据对不对
class VerifyRequest(BaseModel):
    draft: str                  # 当前生成的草稿
    outline: str                # 对应的大纲/任务要求
    history: List[str] = []     # 之前已经生成的章节内容列表（用于查重）
    document_id: Optional[str] = None  # 如果不传 history，可用 document_id 从数据库读取
    rel_threshold: Optional[float] = 0.6  # 可选：自定义相关性阈值
    red_threshold: Optional[float] = 0.7  # 可选：自定义冗余度阈值

# 2. 初始化应用
app = FastAPI(title="FlowerNet Verifying Layer API")

# 全局 verifier 对象（延迟初始化）
_verifier = None
_history_manager = None

def get_verifier():
    """延迟初始化 verifier（首次使用时才创建）"""
    global _verifier
    if _verifier is None:
        print("⏳ 首次初始化 Verifier...")
        _verifier = FlowerNetVerifier()
        print("✅ Verifier 已初始化")
    return _verifier

def get_history_manager():
    """延迟初始化 HistoryManager（用于从数据库读取历史内容）"""
    global _history_manager
    if _history_manager is None:
        use_db = os.getenv('USE_DATABASE', 'false').lower() == 'true'
        db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
        _history_manager = HistoryManager(use_database=use_db, db_path=db_path)
    return _history_manager

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
        history_list = request.history
        if (not history_list) and request.document_id:
            history_manager = get_history_manager()
            history_records = history_manager.get_history(request.document_id)
            history_list = [entry["content"] for entry in history_records]
        # 调用 verifier.py 中的 verify 方法
        result = verifier.verify(
            draft=request.draft,
            outline=request.outline,
            history_list=history_list,
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

