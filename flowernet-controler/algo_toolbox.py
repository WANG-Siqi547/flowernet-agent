import spacy
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# 加载轻量级 NLP 模型
nlp = spacy.load("en_core_web_sm")

class FlowerNetAlgos:
    @staticmethod
    def entity_recall(outline):
        """【提高相关性】通过实体提取，强制 100% 覆盖"""
        doc = nlp(outline)
        # 提取专有名词和名词短语
        entities = [ent.text for ent in doc.ents]
        noun_chunks = [chunk.text for chunk in doc.noun_chunks]
        all_terms = list(set(entities + noun_chunks))
        return f"你必须在段落中包含以下所有术语，确保事实相关性：{', '.join(all_terms)}"

    @staticmethod
    def layred_structure(outline):
        """【提高相关性】层级化结构约束，提升逻辑链条"""
        doc = nlp(outline)
        # 提取逻辑主线 (主-谓-宾)
        relations = []
        for token in doc:
            if token.pos_ == "VERB":
                subj = [w.text for w in token.lefts if w.dep_ in ("nsubj", "nsubjpass")]
                obj = [w.text for w in token.rights if w.dep_ in ("dobj", "pobj")]
                if subj and obj:
                    relations.append(f"{subj[0]} -> {token.text} -> {obj[0]}")
        
        return f"请遵循以下层级逻辑结构展开，严禁偏离：{'; '.join(relations)}"

    @staticmethod
    def sem_dedup(failed_draft, history):
        """【降低冗余】检测草稿中的语义重复句，生成禁止指令"""
        if not history: return ""
        # 简单逻辑：提取失败草稿的关键短语作为“负面约束”
        doc = nlp(failed_draft)
        redundant_candidates = [sent.text for sent in doc.sents if len(sent.text) > 10]
        # 告诉模型不要重复这些已经表达过的意思
        return f"严禁重复以下语义点或内容：{'; '.join(redundant_candidates[:2])}"

    @staticmethod
    def pacsum_template(history, top_k=3):
        """【降低冗余】基于重要性/中心度生成低冗余上下文模板"""
        if not history or len(history) <= top_k:
            return " ".join(history)
        
        # 模拟 PacSum 中心度计算：保留最近的且具有代表性的信息
        # 越靠后的历史往往对当前生成越重要（Position-Augmented）
        selected = history[-top_k:]
        return " ".join(selected)

    @staticmethod
    def anti_hallucination():
        """【减少幻觉】通用的 Grounding 指令"""
        return "请仅依据提供的大纲和背景信息生成内容。如果信息不足，请保持客观，严禁捏造事实或产生幻觉。"