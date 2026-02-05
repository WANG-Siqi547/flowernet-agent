import os
from typing import List, Dict, Any, Optional
from algo_toolbox import FlowerNetAlgos


class FlowerNetController:
    """
    FlowerNet 控制层：根据 Verifier 的反馈优化 Prompt
    """
    
    def __init__(self):
        # Controller 自己的公网 URL（可选，用于返回给客户端）
        self.public_url = os.getenv('CONTROLLER_PUBLIC_URL', 'http://localhost:8001')
        self.iteration_count = 0
        
        print(f"✅ Controller 初始化:")
        print(f"  - Public URL: {self.public_url}")

    def refine_prompt(
        self,
        old_prompt: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str,
        history: Optional[List[str]] = None,
        iteration: int = 1
    ) -> str:
        """
        根据 Verifier 反馈修改 Prompt
        
        输入：
          - old_prompt: 之前使用的 prompt
          - failed_draft: 未通过验证的 draft
          - feedback: Verifier 返回的反馈信息
            包含：
            - relevancy_index: 相关性分数（0-1，越高越好）
            - redundancy_index: 冗余度分数（0-1，越低越好）
            - feedback: 文本反馈信息
            - raw_data: 原始诊断数据
          - outline: 原始大纲
          - history: 历史内容列表（可选）
          - iteration: 当前迭代次数
          
        输出：修改后的新 prompt
        """
        if history is None:
            history = []
        
        # 解析反馈
        redundancy_index = feedback.get('redundancy_index', 0.0)
        relevancy_index = feedback.get('relevancy_index', 0.0)
        feedback_msg = feedback.get('feedback', '')
        
        print(f"\n🔧 [Controller 迭代 {iteration}]")
        print(f"  - 相关性分数: {relevancy_index:.4f}")
        print(f"  - 冗余度分数: {redundancy_index:.4f}")
        print(f"  - 反馈: {feedback_msg}")
        
        # 基础约束
        entity_instr = FlowerNetAlgos.entity_recall(outline)
        logic_instr = FlowerNetAlgos.layred_structure(outline)
        hallucination_instr = FlowerNetAlgos.anti_hallucination()
        context = FlowerNetAlgos.pacsum_template(history)
        
        # 构建改进的 prompt
        new_prompt = f"""
【任务】根据大纲编写内容

【大纲】
{outline}

【背景上下文】
{context if context else "无前置内容"}

【基础约束】
1. {entity_instr}
2. {logic_instr}
3. {hallucination_instr}

【优化要求】（第 {iteration} 次修改）
"""
        
        # 根据反馈的具体问题添加针对性指令
        issues = []
        
        # 问题 1: 冗余度过高
        if redundancy_index > 0.6:
            issues.append("冗余度过高")
            dedup_instr = FlowerNetAlgos.sem_dedup(failed_draft, history)
            new_prompt += f"\n❌ 【冗余问题】\n{dedup_instr}\n"
            new_prompt += f"\n✅ 【改进方案】\n"
            new_prompt += f"- 避免重复已经说过的内容\n"
            new_prompt += f"- 用全新的角度和例子来阐述主题\n"
            new_prompt += f"- 不要使用与前文相同的关键词或短语\n"
            new_prompt += f"- 如果要引用概念，请用不同的表述方式\n"
        
        # 问题 2: 相关性不足
        if relevancy_index < 0.6:
            issues.append("相关性不足")
            new_prompt += f"\n❌ 【相关性问题】\n"
            new_prompt += f"内容偏离了大纲要求。上次生成的内容没有足够关注主题「{outline}」\n"
            new_prompt += f"\n✅ 【改进方案】\n"
            new_prompt += f"- 严格围绕「{outline}」这个核心主题展开\n"
            new_prompt += f"- 每个句子都应该与主题直接相关\n"
            new_prompt += f"- 不要偏离到无关的话题\n"
            new_prompt += f"- 确保内容的主要焦点始终在于{outline}\n"
        
        # 问题 3：同时存在两个问题
        if len(issues) == 0:
            new_prompt += f"\n⚠️ 【小幅调整】\n"
            new_prompt += f"- 保持当前内容的主题和质量\n"
            new_prompt += f"- 略微增加新的细节或角度以通过验证\n"
        
        # 添加用户反馈
        if feedback_msg:
            new_prompt += f"\n💬 【验证器反馈】\n{feedback_msg}\n"
        
        # 添加前次失败内容作为反面教材
        new_prompt += f"\n\n⚠️ 【前次生成的内容（需要改进）】\n"
        new_prompt += f"---\n"
        new_prompt += f"{failed_draft[:500]}...\n" if len(failed_draft) > 500 else failed_draft
        new_prompt += f"---\n"
        
        new_prompt += f"\n\n请基于以上指导重新生成内容。内容应该：\n"
        new_prompt += f"1. 长度适中（200-500字）\n"
        new_prompt += f"2. 逻辑清晰、表述准确\n"
        new_prompt += f"3. 完全不同于前次尝试\n"
        new_prompt += f"4. 相关性和新意达到平衡\n"
        
        return new_prompt

    def analyze_failure_patterns(
        self,
        failed_drafts: List[str],
        feedback_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        分析多次失败的模式，提供优化建议
        
        Args:
            failed_drafts: 所有失败的 draft 列表
            feedback_list: 所有对应的反馈列表
            
        Returns:
            分析结果字典
        """
        analysis = {
            "total_failures": len(failed_drafts),
            "relevancy_trend": [],
            "redundancy_trend": [],
            "main_issues": []
        }
        
        if not feedback_list:
            return analysis
        
        # 分析趋势
        for feedback in feedback_list:
            analysis["relevancy_trend"].append(feedback.get('relevancy_index', 0))
            analysis["redundancy_trend"].append(feedback.get('redundancy_index', 0))
        
        # 确定主要问题
        avg_relevancy = sum(analysis["relevancy_trend"]) / len(analysis["relevancy_trend"])
        avg_redundancy = sum(analysis["redundancy_trend"]) / len(analysis["redundancy_trend"])
        
        if avg_relevancy < 0.5:
            analysis["main_issues"].append("相关性持续不足 - 需要更强调主题相关性")
        if avg_redundancy > 0.6:
            analysis["main_issues"].append("冗余度持续过高 - 需要更新颖的角度")
        
        return analysis