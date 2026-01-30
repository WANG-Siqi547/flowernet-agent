"""
极简版 Verifier - 无需大模型，仅用传统 NLP 算法
用于 Render 免费版的内存约束环境
"""

import numpy as np
import jieba
import os
from rank_bm25 import BM25Okapi
from rouge_score import rouge_scorer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation


class FlowerNetVerifier:
    def __init__(self):
        print("🌸 FlowerNet 验证层启动（轻量级模式）")
        
        # Verifier 自己的公网 URL
        self.public_url = os.getenv('VERIFIER_PUBLIC_URL', 'http://localhost:8000')
        print(f"  - Verifier Public URL: {self.public_url}")
        
        # 轻量级组件
        self.scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
        self.vectorizer = CountVectorizer(stop_words='english', max_features=1000)
        print("✅ 验证层就绪（仅使用传统 NLP）")

    def _tokenize(self, text):
        """分词"""
        return list(jieba.cut(text))

    # --- 维度 1: 相关性检测 ---
    def calculate_relevancy(self, draft, outline):
        """
        计算草稿与大纲的相关性
        简化算法：关键词覆盖 + BM25 相似度
        """
        # 1. 关键词覆盖度
        outline_tokens = [w for w in self._tokenize(outline) if len(w) > 1]
        draft_tokens = [w for w in self._tokenize(draft) if len(w) > 1]
        
        if outline_tokens:
            outline_keywords = set(outline_tokens)
            draft_keywords = set(draft_tokens)
            keyword_coverage = len(outline_keywords & draft_keywords) / len(outline_keywords)
        else:
            keyword_coverage = 0.0

        # 2. BM25 相似度
        try:
            bm25 = BM25Okapi([outline_tokens, draft_tokens])
            bm25_score = bm25.get_scores(draft_tokens)[0] / (bm25.get_scores(draft_tokens).max() + 1e-9)
        except:
            bm25_score = 0.5

        # 3. 长度相关性（不要太短）
        length_score = min(len(draft) / max(len(outline), 1), 1.0)

        # 综合相关性
        relevancy_score = (keyword_coverage * 0.4) + (bm25_score * 0.3) + (length_score * 0.3)

        return {
            "score": float(round(relevancy_score, 4)),
            "details": {
                "keyword_coverage": float(keyword_coverage),
                "bm25_similarity": float(bm25_score),
                "length_score": float(length_score),
            },
        }

    # --- 维度 2: 冗余检测 ---
    def calculate_redundancy(self, draft, history_list):
        """
        计算冗余度
        简化算法：词语重叠 + N-gram 重复
        """
        if not history_list:
            return {"score": 0.0, "details": "No history yet"}

        all_histories = " ".join(history_list)
        draft_tokens = set(self._tokenize(draft))
        history_tokens = set(self._tokenize(all_histories))

        # 1. 词语重叠度
        if draft_tokens:
            token_overlap = len(draft_tokens & history_tokens) / len(draft_tokens)
        else:
            token_overlap = 0.0

        # 2. 句子级 N-gram 重复（简单版）
        draft_bigrams = set(zip(draft_tokens, list(draft_tokens)[1:]))
        history_bigrams = set(zip(history_tokens, list(history_tokens)[1:]))
        
        if draft_bigrams:
            bigram_overlap = len(draft_bigrams & history_bigrams) / len(draft_bigrams)
        else:
            bigram_overlap = 0.0

        # 综合冗余度
        redundancy_score = (token_overlap * 0.5) + (bigram_overlap * 0.5)

        return {
            "score": float(round(redundancy_score, 4)),
            "details": {
                "token_overlap": float(token_overlap),
                "bigram_overlap": float(bigram_overlap),
            },
        }

    # --- 维度 3: 综合判定 ---
    def verify(self, draft, outline, history_list, rel_threshold=0.4, red_threshold=0.6):
        """
        一键验证逻辑
        """
        rel = self.calculate_relevancy(draft, outline)
        red = self.calculate_redundancy(draft, history_list)

        # 判定逻辑
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
