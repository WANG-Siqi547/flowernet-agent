import requests
import json
import os
from algo_toolbox import FlowerNetAlgos

class FlowerNetController:
    def __init__(self, verifier_url=None, generator_func=None):
        # 优先使用传入的 URL，否则从环境变量读取
        self.verifier_url = verifier_url or os.getenv('VERIFIER_URL', 'http://localhost:8000')
        self.generator = generator_func  # 这里的 generator 是调用 LLM 的函数
        self.history = []
        
        # Controller 自己的公网 URL（可选，用于返回给客户端）
        self.public_url = os.getenv('CONTROLLER_PUBLIC_URL', 'http://localhost:8001')
        
        print(f"Controller 初始化:")
        print(f"  - Verifier URL: {self.verifier_url}")
        print(f"  - Public URL: {self.public_url}")

    def build_prompt(self, outline, failed_draft=None, scenario="initial"):
        """构造/修改 Prompt 的核心"""
        # 1. 基础约束 (LayRED & Entity Recall)
        entity_instr = FlowerNetAlgos.entity_recall(outline)
        logic_instr = FlowerNetAlgos.layred_structure(outline)
        hallucination_instr = FlowerNetAlgos.anti_hallucination()
        
        # 2. 动态冗余约束 (PacSum & SemDedup)
        context = FlowerNetAlgos.pacsum_template(self.history)
        
        prompt = f"""
        任务：根据大纲编写内容。
        大纲：{outline}
        背景上下文：{context}
        
        指令约束：
        - {entity_instr}
        - {logic_instr}
        - {hallucination_instr}
        """

        if scenario == "fix_redundancy" and failed_draft:
            dedup_instr = FlowerNetAlgos.sem_dedup(failed_draft, self.history)
            prompt += f"\n- 修正要求：{dedup_instr}\n- 请换一个角度描述，不要与前文重复。"
            
        return prompt

    def run_loop(self, outline, max_retries=3):
        current_prompt = self.build_prompt(outline)
        
        for attempt in range(max_retries):
            # 1. 调用生成层 (LLM)
            draft = self.generator(current_prompt)
            
            # 2. 调用验证层 API (跨服务调用)
            response = requests.post(
                f"{self.verifier_url}/verify",
                json={"draft": draft, "outline": outline, "history": self.history}
            )
            res_data = response.json()
            
            if res_data["is_passed"]:
                self.history.append(draft)
                return draft, True

            # 3. 如果不合格，根据反馈修改 Prompt
            print(f"第 {attempt+1} 次尝试失败: {res_data['feedback']}")
            
            if res_data["redundancy_index"] > 0.6:
                current_prompt = self.build_prompt(outline, draft, "fix_redundancy")
            else:
                current_prompt = self.build_prompt(outline, draft, "fix_relevancy")
                
        return "未能生成合格内容", False