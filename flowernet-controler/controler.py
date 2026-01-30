import requests
import json
import os
from algo_toolbox import FlowerNetAlgos

class FlowerNetController:
    def __init__(self):
        # Controller 自己的公网 URL（可选，用于返回给客户端）
        self.public_url = os.getenv('CONTROLLER_PUBLIC_URL', 'http://localhost:8001')
        
        print(f"Controller 初始化:")
        print(f"  - Public URL: {self.public_url}")

    def build_initial_prompt(self, outline, history=None):
        """
        构建初始 Prompt
        输入：outline（大纲），history（历史内容列表，可选）
        输出：初始 prompt
        """
        if history is None:
            history = []
            
        # 1. 基础约束 (LayRED & Entity Recall)
        entity_instr = FlowerNetAlgos.entity_recall(outline)
        logic_instr = FlowerNetAlgos.layred_structure(outline)
        hallucination_instr = FlowerNetAlgos.anti_hallucination()
        
        # 2. 动态冗余约束 (PacSum)
        context = FlowerNetAlgos.pacsum_template(history)
        
        prompt = f"""
任务：根据大纲编写内容。
大纲：{outline}
背景上下文：{context}

指令约束：
- {entity_instr}
- {logic_instr}
- {hallucination_instr}
"""
        return prompt

    def refine_prompt(self, old_prompt, failed_draft, feedback, outline, history=None):
        """
        根据 Verifier 反馈修改 Prompt
        输入：
          - old_prompt: 之前使用的 prompt
          - failed_draft: 未通过验证的 draft
          - feedback: Verifier 返回的反馈信息（包含 relevancy_index, redundancy_index 等）
          - outline: 原始大纲
          - history: 历史内容列表（可选）
        输出：修改后的新 prompt
        """
        if history is None:
            history = []
            
        # 解析反馈
        redundancy_index = feedback.get('redundancy_index', 0)
        relevancy_index = feedback.get('relevancy_index', 0)
        feedback_msg = feedback.get('feedback', '')
        
        # 基础约束保持不变
        entity_instr = FlowerNetAlgos.entity_recall(outline)
        logic_instr = FlowerNetAlgos.layred_structure(outline)
        hallucination_instr = FlowerNetAlgos.anti_hallucination()
        context = FlowerNetAlgos.pacsum_template(history)
        
        new_prompt = f"""
任务：根据大纲编写内容。
大纲：{outline}
背景上下文：{context}

指令约束：
- {entity_instr}
- {logic_instr}
- {hallucination_instr}
"""
        
        # 根据具体问题添加修正指令
        if redundancy_index > 0.6:
            # 冗余度过高
            dedup_instr = FlowerNetAlgos.sem_dedup(failed_draft, history)
            new_prompt += f"\n\n⚠️ 修正要求（冗余问题）：\n{dedup_instr}\n请换一个角度描述，不要与前文重复。避免使用与历史内容相同的词汇和表达。"
        
        if relevancy_index < 0.6:
            # 相关性不足
            new_prompt += f"\n\n⚠️ 修正要求（相关性问题）：\n内容偏离了大纲要求。请严格按照大纲的核心主题展开，确保每句话都与「{outline}」直接相关。"
        
        new_prompt += f"\n\n💡 上次生成的问题：{feedback_msg}"
        
        return new_prompt