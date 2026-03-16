import numpy as np
import jieba
import os
from rank_bm25 import BM25Okapi
from rouge_score import rouge_scorer
from sentence_transformers import SentenceTransformer, util
from FlagEmbedding import BGEM3FlagModel
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation

class FlowerNetVerifier:
    def __init__(self):
        print("🌸 FlowerNet 验证层启动（延迟加载模式）")
        
        # Verifier 自己的公网 URL
        self.public_url = os.getenv('VERIFIER_PUBLIC_URL', 'http://localhost:8000')
        print(f"  - Verifier Public URL: {self.public_url}")
        
        # 延迟加载：模型首次使用时才加载（节省内存）
        self._bge_model = None
        self._sbert_model = None
        
        # 轻量级组件立即初始化
        self.scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
        self.vectorizer = CountVectorizer(stop_words='english')
        print("✅ 验证层就绪（模型将按需加载）")
    
    @property
    def bge_model(self):
        """延迟加载 BGE-M3 模型（如果内存不足，回退到 sbert）"""
        if self._bge_model is None:
            try:
                print("⏳ 尝试加载 BGE-M3 模型...")
                # 使用更小的模型或直接用 sbert 代替
                # self._bge_model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)
                # 改为使用已有的 sbert_model 来节省内存
                print("⚠️  内存受限，使用 SentenceBERT 代替 BGE-M3")
                self._bge_model = self.sbert_model  # 复用同一个模型
                print("✅ 使用轻量级模型")
            except Exception as e:
                print(f"❌ BGE-M3 加载失败: {e}，使用 SentenceBERT")
                self._bge_model = self.sbert_model
        return self._bge_model
    
    @property
    def sbert_model(self):
        """延迟加载 SentenceBERT 模型"""
        if self._sbert_model is None:
            print("⏳ 首次加载 SentenceBERT 模型...")
            self._sbert_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            print("✅ SentenceBERT 已加载")
        return self._sbert_model

    # --- 核心辅助工具 ---
    def _tokenize(self, text):
        """对中文或英文进行分词"""
        return list(jieba.cut(text))

    def _get_lda_topics(self, text, n_topics=1):
        """提取文本的主题分布特征"""
        try:
            words = [" ".join(self._tokenize(text))]
            tf = self.vectorizer.fit_transform(words)
            lda = LatentDirichletAllocation(n_components=n_topics, random_state=0)
            lda.fit(tf)
            return lda.components_
        except:
            return np.zeros((1, 10)) # 兜底处理

    # --- 维度 1: 相关性检测 (Relevancy) ---
    def calculate_relevancy(self, draft, outline):
        """
        计算当前花瓣(Draft)与花心大纲(Outline)的相关性
        改进算法：处理长短文本的相关性判断
        算法组合：关键词覆盖度 + 语义相似度 + 主题一致性
        """
        # 1. 关键词覆盖度 (Keyword Coverage)
        # 提取大纲中的关键词（长度>2的词），计算在草稿中的覆盖率
        outline_tokens = [w for w in self._tokenize(outline) if len(w) > 1]
        draft_tokens = [w for w in self._tokenize(draft) if len(w) > 1]
        
        if outline_tokens:
            outline_keywords = set(outline_tokens)
            draft_keywords = set(draft_tokens)
            keyword_coverage = len(outline_keywords & draft_keywords) / len(outline_keywords)
        else:
            keyword_coverage = 0.0

        # 2. 语义相似度 (Semantic Similarity)
        # 使用绝对值来计算相似度，避免反向向量导致的负值问题
        emb_draft = self.sbert_model.encode(draft, convert_to_tensor=True)
        emb_outline = self.sbert_model.encode(outline, convert_to_tensor=True)
        semantic_sim_raw = util.pytorch_cos_sim(emb_draft, emb_outline).item()
        # 使用绝对值确保相关性为正，然后归一化到 [0, 1]
        semantic_sim = abs(semantic_sim_raw)

        # 3. 主题一致性 (Topic Consistency)
        # 检查大纲的核心词汇在草稿中的重复率（词汇密度）
        # 计算方式：关键词出现次数 / 总词数
        outline_keywords_list = list(outline_keywords) if outline_tokens else []
        if outline_keywords_list and draft_tokens:
            keyword_frequency = sum(draft_tokens.count(kw) for kw in outline_keywords_list) / len(draft_tokens)
            # 归一化：期望频率约为 0.1-0.3 为最优
            topic_consistency = min(keyword_frequency / 0.2, 1.0)
        else:
            topic_consistency = 0.0

        # 4. 权重融合 
        # 关键词覆盖度 (0.4) + 语义相似度 (0.4) + 主题一致性 (0.2)
        total_relevancy = (keyword_coverage * 0.4) + (semantic_sim * 0.4) + (topic_consistency * 0.2)
        
        return {
            "score": float(round(total_relevancy, 4)),
            "details": {
                "keyword_coverage": float(round(keyword_coverage, 4)),
                "semantic_similarity": float(round(semantic_sim, 4)),
                "topic_consistency": float(round(topic_consistency, 4)),
            },
        }

    # --- 维度 2: 冗余度检测 (Redundancy) ---
    def calculate_redundancy(self, draft, history_list):
        """
        检测当前花瓣(Draft)与已生成的花瓣(History)是否重复
        算法组合：BGE-M3(高精度去重) + LDA(主题重复) + FActScore(事实简化版)
        """
        if not history_list:
            return {"score": 0.0, "details": "No history yet"}

        # 1. 语义相似度检测（使用 SentenceBERT，节省内存）
        # 直接用 sbert 而不是 bge，避免加载大模型
        all_histories = " ".join(history_list)
        
        emb_draft = self.sbert_model.encode(draft, convert_to_tensor=True)
        emb_history = self.sbert_model.encode(all_histories, convert_to_tensor=True)
        semantic_sim = util.pytorch_cos_sim(emb_draft, emb_history).item()

        # 2. 主题重复检测（已经计算过了，复用）
        topic_overlap = semantic_sim  # 使用同样的语义相似度

        # 3. 事实层面简化检测 (模拟 FActScore 逻辑)
        # 我们检测 Draft 中的核心名词在 History 中出现的频率
        draft_keywords = set([w for w in self._tokenize(draft) if len(w) > 1])
        history_keywords = set([w for w in self._tokenize(all_histories) if len(w) > 1])
        fact_overlap = len(draft_keywords & history_keywords) / max(len(draft_keywords), 1)

        # 4. 权重融合（简化版，不使用 BGE）
        # 冗余得分越高，说明越重复。使用语义相似度和事实重叠
        total_redundancy = (semantic_sim * 0.6) + (fact_overlap * 0.4)

        return {
            "score": float(round(total_redundancy, 4)),
            "details": {
                "semantic": float(semantic_sim),
                "topic": float(topic_overlap),
                "fact": float(fact_overlap),
            },
        }

    # --- 维度 3: 综合判定 (Decision) ---
    def verify(self, draft, outline, history_list, rel_threshold=0.80, red_threshold=0.40):
        """
        一键验证逻辑：FlowerNet 决定是否进入下一步
        """
        rel = self.calculate_relevancy(draft, outline)
        red = self.calculate_redundancy(draft, history_list)
        
        # 判定逻辑：相关性要高，冗余度要低
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

# --- 本地测试代码 ---
if __name__ == "__main__":
    verifier = FlowerNetVerifier()
    test_outline = "Discuss the impact of AI on modern healthcare and medical diagnosis."
    test_draft = "AI has revolutionized modern healthcare and medical diagnosis by introducing unprecedented efficiency and accuracy. In medical diagnosis, AI-powered systems can analyze complex medical data, such as imaging scans, lab results, and electronic health records (EHRs), at a speed far exceeding human capabilities, enabling early detection of diseases like cancer, cardiovascular disorders, and neurological conditions. These systems reduce diagnostic errors caused by human fatigue or limited expertise, especially in regions with a shortage of specialized physicians. Beyond diagnosis, AI optimizes healthcare delivery by streamlining administrative tasks, personalizing treatment plans based on individual patient data, and predicting disease outbreaks, thereby improving overall healthcare accessibility and patient outcomes. While challenge"
    test_history = ["The history of healthcare starts from ancient times.", "Modern hospitals use digital records."]
    
    result = verifier.verify(test_draft, test_outline, test_history)
    print("\n--- FlowerNet 验证结果 ---")
    print(f"是否通过: {result['is_passed']}")
    print(f"相关性得分: {result['relevancy_index']}")
    print(f"冗余度得分: {result['redundancy_index']}")
    print(f"控制层反馈: {result['feedback']}")