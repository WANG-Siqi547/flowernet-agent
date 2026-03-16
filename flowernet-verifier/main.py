from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import jieba
import os
from rank_bm25 import BM25Okapi
from rouge_score import rouge_scorer

from history_store import HistoryManager

# 英文停用词表：过滤高频功能词，只保留实义词参与计算
_EN_STOPWORDS = {
    'the','a','an','is','are','was','were','be','been','being',
    'have','has','had','do','does','did','will','would','could',
    'should','may','might','shall','can','need','dare','ought',
    'used','to','of','in','on','at','by','for','with','about',
    'against','between','into','through','during','before','after',
    'above','below','from','up','down','out','off','over','under',
    'again','further','then','once','and','but','or','nor','so',
    'yet','both','either','neither','not','only','same','than',
    'too','very','just','because','as','until','while','that',
    'this','these','those','it','its','i','you','he','she','we',
    'they','what','which','who','whom','when','where','why','how',
    'all','each','every','more','most','other','some','such','no',
    'any','if','my','your','his','her','our','their','there','also',
    'its','use','also','since','however','therefore','thus','hence',
}


# ============ FlowerNetVerifier 轻量级版本 ============
class FlowerNetVerifier:
    def __init__(self):
        print("🌸 FlowerNet 验证层启动（优化版）")
        self.public_url = os.getenv('VERIFIER_PUBLIC_URL', 'http://localhost:8000')
        print(f"  - Verifier Public URL: {self.public_url}")
        self.scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
        print("✅ 验证层就绪")

    def _tokenize(self, text):
        """全量分词（含停用词）"""
        tokens = [t.strip().lower() for t in jieba.cut(text)]
        return [t for t in tokens if t]

    def _content_tokens(self, text):
        """实义词分词：过滤英文停用词和单字符 token，用于相似度计算"""
        tokens = [t.strip().lower() for t in jieba.cut(text)]
        return [t for t in tokens if len(t) > 1 and t not in _EN_STOPWORDS]

    def calculate_relevancy(self, draft, outline):
        """
        计算草稿与大纲的相关性。
        算法：实义关键词覆盖率(0.4) + ROUGE-L F1(0.4) + BM25(0.2)
        - 关键词覆盖率：大纲中的实义词有多少出现在草稿中
        - ROUGE-L：最长公共子序列匹配，捕捉词序和短语一致性
        - BM25：用大纲自比得分作上限归一化，衡量草稿对大纲的词频相关程度
        """
        outline_tokens = self._content_tokens(outline)
        draft_tokens = self._content_tokens(draft)

        # 1. 关键词覆盖率（实义词）
        if outline_tokens:
            outline_kw = set(outline_tokens)
            draft_kw = set(draft_tokens)
            keyword_coverage = len(outline_kw & draft_kw) / len(outline_kw)
        else:
            keyword_coverage = 0.0

        # 2. ROUGE-L Recall：衡量大纲内容被草稿覆盖了多少。
        # 用 recall 而非 F1：outline 通常远短于 draft，F1 会因 precision 被长文本压缩至趋近0，造成假阴性
        # recall = LCS / len(outline_tokens) 不受 draft 长度影响
        try:
            rouge_l = self.scorer.score(outline, draft)['rougeL'].recall
        except Exception:
            rouge_l = 0.0

        # 3. BM25（以大纲自比得分为上限归一化，修复原来用 draft 自比导致分母过大的 bug）
        try:
            if outline_tokens and draft_tokens:
                bm25 = BM25Okapi([outline_tokens])
                raw_score = bm25.get_scores(draft_tokens)[0]
                # 大纲自比得分作为满分参考上限
                self_ref = bm25.get_scores(outline_tokens)[0]
                bm25_score = float(min(raw_score / (self_ref + 1e-9), 1.0))
                bm25_score = max(bm25_score, 0.0)
            else:
                bm25_score = 0.0
        except Exception:
            bm25_score = 0.0

        relevancy_score = (keyword_coverage * 0.4) + (rouge_l * 0.4) + (bm25_score * 0.2)

        return {
            "score": float(round(relevancy_score, 4)),
            "details": {
                "keyword_coverage": float(round(keyword_coverage, 4)),
                "rouge_l": float(round(rouge_l, 4)),
                "bm25_score": float(round(bm25_score, 4)),
            },
        }

    def calculate_redundancy(self, draft, history_list):
        """
        检测草稿与已生成历史的冗余度。
        算法：对每条历史分别计算，取最大值（修复原来全部拼接导致越来越长、停用词污染的 bug）。
        每条历史得分 = 实义词 token 重叠(0.5) + 实义词 bigram 重叠(0.3) + ROUGE-L(0.2)
        - 使用实义词过滤停用词，避免 the/of/and 等词拉高误报
        - 逐条取最大值而非平均：只要有一条历史高度相似就应报警
        """
        if not history_list:
            return {"score": 0.0, "details": "No history yet"}

        draft_tokens = self._content_tokens(draft)
        draft_tokens_set = set(draft_tokens)
        draft_bigrams = set(zip(draft_tokens, draft_tokens[1:]))

        max_redundancy = 0.0
        per_entry_scores = []

        for hist in history_list:
            hist_tokens = self._content_tokens(hist)
            hist_tokens_set = set(hist_tokens)
            hist_bigrams = set(zip(hist_tokens, hist_tokens[1:]))

            # 实义词 unigram 重叠率
            if draft_tokens_set:
                token_overlap = len(draft_tokens_set & hist_tokens_set) / len(draft_tokens_set)
            else:
                token_overlap = 0.0

            # 实义词 bigram 重叠率（捕捉短语级重复）
            if draft_bigrams:
                bigram_overlap = len(draft_bigrams & hist_bigrams) / len(draft_bigrams)
            else:
                bigram_overlap = 0.0

            # ROUGE-L Recall：衡量历史内容被草稿重复了多少
            # recall = LCS / len(hist_tokens)：hist 是 reference，表示历史中有多少内容被 draft 再现
            try:
                rouge_l = self.scorer.score(hist, draft)['rougeL'].recall
            except Exception:
                rouge_l = 0.0

            entry_score = (token_overlap * 0.5) + (bigram_overlap * 0.3) + (rouge_l * 0.2)
            per_entry_scores.append(float(round(entry_score, 4)))
            if entry_score > max_redundancy:
                max_redundancy = entry_score

        return {
            "score": float(round(max_redundancy, 4)),
            "details": {
                "per_history_scores": per_entry_scores,
                "max_redundancy": float(round(max_redundancy, 4)),
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
        use_db = os.getenv('USE_DATABASE', 'true').lower() == 'true'
        raw_db_path = os.getenv('DATABASE_PATH', 'flowernet_history.db')
        if os.path.isabs(raw_db_path):
            db_path = raw_db_path
        else:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, raw_db_path)
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

