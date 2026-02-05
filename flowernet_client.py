"""
FlowerNet Python 客户端库
简化与 FlowerNet 系统的交互
"""

import requests
from typing import List, Dict, Any, Optional
import time


class FlowerNetClient:
    """FlowerNet 客户端 - 简化 API 调用"""
    
    def __init__(
        self,
        generator_url: str = "http://localhost:8002",
        verifier_url: str = "http://localhost:8000",
        controller_url: str = "http://localhost:8001",
        timeout: int = 60,
        verbose: bool = True
    ):
        """
        初始化客户端
        
        Args:
            generator_url: Generator 服务 URL
            verifier_url: Verifier 服务 URL
            controller_url: Controller 服务 URL
            timeout: 请求超时时间（秒）
            verbose: 是否输出详细日志
        """
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.timeout = timeout
        self.verbose = verbose
        self.session = requests.Session()
    
    def _log(self, message: str):
        """输出日志"""
        if self.verbose:
            print(message)
    
    def generate(
        self,
        prompt: str,
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        简单生成
        
        Args:
            prompt: 生成提示
            max_tokens: 最大 token 数
            
        Returns:
            包含生成文本的字典
        """
        self._log(f"🎯 生成内容 (prompt: {len(prompt)} 字符)...")
        
        try:
            response = self.session.post(
                f"{self.generator_url}/generate",
                json={"prompt": prompt, "max_tokens": max_tokens},
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def verify(
        self,
        draft: str,
        outline: str,
        history: Optional[List[str]] = None,
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        验证内容
        
        Args:
            draft: 要验证的文本
            outline: 大纲
            history: 历史内容
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            
        Returns:
            验证结果
        """
        self._log(f"🔍 验证内容...")
        
        try:
            response = self.session.post(
                f"{self.verifier_url}/verify",
                json={
                    "draft": draft,
                    "outline": outline,
                    "history": history or [],
                    "rel_threshold": rel_threshold,
                    "red_threshold": red_threshold
                },
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def refine_prompt(
        self,
        old_prompt: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str,
        history: Optional[List[str]] = None,
        iteration: int = 1
    ) -> Dict[str, Any]:
        """
        优化 Prompt
        
        Args:
            old_prompt: 原始 prompt
            failed_draft: 失败的生成文本
            feedback: 验证反馈
            outline: 大纲
            history: 历史内容
            iteration: 迭代次数
            
        Returns:
            包含新 prompt 的字典
        """
        self._log(f"🔧 优化 prompt (迭代 {iteration})...")
        
        try:
            response = self.session.post(
                f"{self.controller_url}/refine_prompt",
                json={
                    "old_prompt": old_prompt,
                    "failed_draft": failed_draft,
                    "feedback": feedback,
                    "outline": outline,
                    "history": history or [],
                    "iteration": iteration
                },
                timeout=self.timeout
            )
            return response.json()
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def generate_with_loop(
        self,
        outline: str,
        initial_prompt: str,
        history: Optional[List[str]] = None,
        max_iterations: int = 5,
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        完整的生成-验证-修改循环
        
        Args:
            outline: 大纲
            initial_prompt: 初始 prompt
            history: 历史内容
            max_iterations: 最大迭代次数
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            
        Returns:
            包含最终生成结果的字典
        """
        if history is None:
            history = []
        
        self._log(f"\n{'='*50}")
        self._log(f"📝 生成段落: {outline}")
        self._log(f"{'='*50}")
        
        current_prompt = initial_prompt
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            self._log(f"\n[迭代 {iteration}/{max_iterations}]")
            
            # 生成
            gen_result = self.generate(current_prompt)
            if not gen_result.get("success"):
                self._log(f"❌ 生成失败: {gen_result.get('error')}")
                return gen_result
            
            draft = gen_result.get("draft", "")
            self._log(f"✅ 生成了 {len(draft)} 字符")
            
            # 验证
            ver_result = self.verify(
                draft=draft,
                outline=outline,
                history=history,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold
            )
            
            rel_score = ver_result.get("relevancy_index", 0)
            red_score = ver_result.get("redundancy_index", 0)
            is_passed = ver_result.get("is_passed", False)
            
            self._log(f"📊 相关性: {rel_score:.4f} | 冗余度: {red_score:.4f}")
            
            if is_passed:
                self._log(f"✨ 验证通过！")
                history.append(draft)
                return {
                    "success": True,
                    "draft": draft,
                    "iterations": iteration,
                    "verification": {
                        "relevancy": rel_score,
                        "redundancy": red_score
                    }
                }
            
            # 优化 prompt
            ctl_result = self.refine_prompt(
                old_prompt=current_prompt,
                failed_draft=draft,
                feedback=ver_result,
                outline=outline,
                history=history,
                iteration=iteration
            )
            
            if not ctl_result.get("success"):
                self._log(f"❌ 优化失败: {ctl_result.get('error')}")
                return ctl_result
            
            current_prompt = ctl_result.get("prompt", "")
            self._log(f"🔄 准备下一轮...")
        
        # 达到最大迭代次数
        self._log(f"\n⚠️  达到最大迭代次数")
        return {
            "success": True,
            "draft": draft if 'draft' in locals() else "",
            "iterations": max_iterations,
            "warning": "Reached max iterations"
        }
    
    def health_check(self) -> Dict[str, bool]:
        """检查所有服务健康状态"""
        status = {}
        
        for name, url in [
            ("Generator", self.generator_url),
            ("Verifier", self.verifier_url),
            ("Controller", self.controller_url)
        ]:
            try:
                response = self.session.get(f"{url}/", timeout=5)
                status[name] = response.status_code == 200
            except:
                status[name] = False
        
        return status


class FlowerNetDocumentGenerator:
    """文档生成器 - 生成多段落文档"""
    
    def __init__(self, client: FlowerNetClient):
        """
        初始化文档生成器
        
        Args:
            client: FlowerNetClient 实例
        """
        self.client = client
    
    def generate_document(
        self,
        title: str,
        outlines: List[str],
        system_prompt: str = "",
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7,
        max_iterations: int = 5
    ) -> Dict[str, Any]:
        """
        生成完整文档
        
        Args:
            title: 文档标题
            outlines: 段落大纲列表
            system_prompt: 系统级提示
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            max_iterations: 最大迭代次数
            
        Returns:
            包含完整文档的字典
        """
        self.client._log(f"\n{'#'*50}")
        self.client._log(f"📄 生成文档: {title}")
        self.client._log(f"{'#'*50}")
        
        document = {
            "title": title,
            "sections": [],
            "total_iterations": 0,
            "success_count": 0
        }
        
        history = []
        
        for idx, outline in enumerate(outlines, 1):
            self.client._log(f"\n📍 第 {idx}/{len(outlines)} 段落")
            
            # 生成初始 prompt
            initial_prompt = f"""
【任务】编写段落内容
【段落】{idx}/{len(outlines)}
【主题】{outline}

{f'【系统指示】{system_prompt}' if system_prompt else ''}

请编写一段相关内容，长度 200-500 字。
"""
            
            # 生成并验证
            result = self.client.generate_with_loop(
                outline=outline,
                initial_prompt=initial_prompt,
                history=history,
                max_iterations=max_iterations,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold
            )
            
            if result.get("success"):
                document["sections"].append({
                    "outline": outline,
                    "content": result.get("draft", ""),
                    "iterations": result.get("iterations", 0)
                })
                document["success_count"] += 1
                document["total_iterations"] += result.get("iterations", 0)
                history.append(result.get("draft", ""))
        
        # 生成摘要
        self.client._log(f"\n{'#'*50}")
        self.client._log(f"✅ 文档生成完成")
        self.client._log(f"成功段落: {document['success_count']}/{len(outlines)}")
        self.client._log(f"总迭代次数: {document['total_iterations']}")
        self.client._log(f"{'#'*50}\n")
        
        return document


# 使用示例
if __name__ == "__main__":
    # 初始化客户端
    client = FlowerNetClient(verbose=True)
    
    # 检查服务
    print("🔍 检查服务状态...")
    status = client.health_check()
    print(f"  Generator: {'✅' if status['Generator'] else '❌'}")
    print(f"  Verifier: {'✅' if status['Verifier'] else '❌'}")
    print(f"  Controller: {'✅' if status['Controller'] else '❌'}")
    
    if not all(status.values()):
        print("\n❌ 部分服务离线，请先启动所有服务")
        exit(1)
    
    # 测试单段落生成
    print("\n" + "="*50)
    print("测试 1: 单段落生成")
    print("="*50)
    
    result = client.generate_with_loop(
        outline="介绍人工智能的基本概念",
        initial_prompt="请详细介绍人工智能的基本概念、应用领域和发展前景。",
        max_iterations=3
    )
    
    if result.get("success"):
        print(f"\n✅ 成功！迭代次数: {result['iterations']}")
        print(f"内容预览: {result['draft'][:100]}...")
    else:
        print(f"\n❌ 失败: {result.get('error')}")
    
    # 测试完整文档生成
    print("\n" + "="*50)
    print("测试 2: 完整文档生成")
    print("="*50)
    
    doc_gen = FlowerNetDocumentGenerator(client)
    document = doc_gen.generate_document(
        title="人工智能入门指南",
        outlines=[
            "基本概念和定义",
            "发展历史和现状",
            "主要应用领域"
        ],
        system_prompt="使用简洁、易懂的语言",
        max_iterations=2
    )
    
    print(f"\n📊 文档统计:")
    print(f"  标题: {document['title']}")
    print(f"  段落数: {len(document['sections'])}")
    print(f"  成功: {document['success_count']}/{len(document['sections'])}")
    print(f"  总迭代: {document['total_iterations']}")
