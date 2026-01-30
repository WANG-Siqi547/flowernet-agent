import re
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

class FlowerNetAlgos:
    @staticmethod
    def entity_recall(outline):
        """【提高相关性】提取关键术语，强制 100% 覆盖"""
        # 简化版：使用正则提取大写开头的词和重要名词（不依赖 spaCy）
        words = outline.split()
        # 提取可能的关键词（长度>3的词）
        key_terms = [w.strip('.,;:!?') for w in words if len(w) > 3]
        
        if key_terms:
            return f"你必须在段落中包含以下所有术语，确保事实相关性：{', '.join(key_terms[:5])}"
        return "请严格围绕大纲主题展开。"

    @staticmethod
    def layred_structure(outline):
        """【提高相关性】层级化结构约束"""
        # 简化版：提取动词相关的关键短语
        return f"请遵循大纲的逻辑结构展开：「{outline}」，严禁偏离主题。"

    @staticmethod
    def sem_dedup(failed_draft, history):
        """【降低冗余】检测草稿中的重复内容，生成禁止指令"""
        if not history: 
            return ""
        
        # 简化版：提取失败草稿的前几个句子作为"负面约束"
        sentences = re.split(r'[.!?]', failed_draft)
        redundant_parts = [s.strip() for s in sentences if len(s.strip()) > 10][:2]
        
        if redundant_parts:
            return f"严禁重复以下语义点或内容：{'; '.join(redundant_parts)}"
        return "避免与历史内容重复。"

    @staticmethod
    def pacsum_template(history, top_k=3):
        """【降低冗余】基于重要性/中心度生成低冗余上下文模板"""
        if not history or len(history) <= top_k:
            return " ".join(history)
        
        # 保留最近的且具有代表性的信息
        # 越靠后的历史往往对当前生成越重要（Position-Augmented）
        selected = history[-top_k:]
        return " ".join(selected)

    @staticmethod
    def anti_hallucination():
        """【减少幻觉】通用的 Grounding 指令"""
        return "请仅依据提供的大纲和背景信息生成内容。如果信息不足，请保持客观，严禁捏造事实或产生幻觉。"