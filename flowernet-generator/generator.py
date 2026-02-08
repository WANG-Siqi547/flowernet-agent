"""
FlowerNet Generator - LLM驱动的内容生成模块
根据prompt使用LLM生成draft内容
支持多种 LLM 提供商: Anthropic Claude, Google Gemini
"""

import os
import requests
import json
from typing import Optional, Dict, Any, List

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class FlowerNetGenerator:
    """
    内容生成器：支持多种 LLM 提供商
    - Anthropic Claude (需要 ANTHROPIC_API_KEY)
    - Google Gemini (需要 GOOGLE_API_KEY，完全免费)
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "models/gemini-2.5-flash", provider: str = "gemini"):
        """
        初始化生成器
        
        Args:
            api_key: API key，如果不提供则从环境变量读取
            model: 使用的模型名称
                - Claude: "claude-3-5-sonnet-20241022"
                - Gemini: "models/gemini-2.5-flash" (免费, 最新), "models/gemini-2.5-pro" (免费但有限制)
            provider: LLM 提供商 ("claude" 或 "gemini")
        """
        self.provider = provider.lower()
        self.model = model
        self.public_url = os.getenv('GENERATOR_PUBLIC_URL', 'http://localhost:8002')
        
        # 根据提供商初始化
        if self.provider == "gemini":
            if not GEMINI_AVAILABLE:
                raise ImportError("需要安装 google-genai: pip install google-genai")
            
            self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
            if not self.api_key:
                raise ValueError("请设置 GOOGLE_API_KEY 环境变量或传入 api_key 参数")
            
            self.client = genai.Client(api_key=self.api_key)
            
            print(f"✅ Generator 初始化 (Google Gemini - 免费):")
            print(f"  - Model: {self.model}")
            print(f"  - Provider: Google Gemini")
            print(f"  - Public URL: {self.public_url}")
            
        elif self.provider == "claude":
            if not ANTHROPIC_AVAILABLE:
                raise ImportError("需要安装 anthropic: pip install anthropic")
            
            self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY', '')
            if not self.api_key:
                raise ValueError("请设置 ANTHROPIC_API_KEY 环境变量或传入 api_key 参数")
            
            self.client = anthropic.Anthropic(api_key=self.api_key)
            
            print(f"✅ Generator 初始化 (Anthropic Claude):")
            print(f"  - Model: {self.model}")
            print(f"  - Provider: Anthropic Claude")
            print(f"  - Public URL: {self.public_url}")
        else:
            raise ValueError(f"不支持的提供商: {provider}。请使用 'claude' 或 'gemini'")

    def generate_draft(self, prompt: str, max_tokens: int = 2000) -> Dict[str, Any]:
        """
        使用 LLM 根据 prompt 生成 draft
        
        Args:
            prompt: 生成指令
            max_tokens: 最大生成token数
            
        Returns:
            包含生成文本和元数据的字典
        """
        try:
            if self.provider == "gemini":
                return self._generate_with_gemini(prompt, max_tokens)
            elif self.provider == "claude":
                return self._generate_with_claude(prompt, max_tokens)
            else:
                raise ValueError(f"未知的提供商: {self.provider}")
        except Exception as e:
            return {
                "success": False,
                "error": f"Error: {str(e)}",
                "draft": ""
            }
    
    def _generate_with_gemini(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Google Gemini 生成内容"""
        try:
            # Gemini 的 max_output_tokens 限制会导致输出过短
            # 如果 max_tokens < 4000，不设置限制让模型自由发挥
            # 如果 max_tokens >= 4000，则设置限制
            config_params = {
                "temperature": 0.7,
            }
            
            if max_tokens >= 4000:
                config_params["max_output_tokens"] = max_tokens
            
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_params)
            )
            
            draft_text = response.text
            
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.model,
                    "provider": "gemini",
                    "prompt_tokens": response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
                    "output_tokens": response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
                    "finish_reason": str(response.candidates[0].finish_reason) if response.candidates else "UNKNOWN",
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"猫咪报错🐱 ：Gemini API Error: {str(e)}， 错了咪！",
                "draft": ""
            }
    
    def _generate_with_claude(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Anthropic Claude 生成内容"""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            draft_text = message.content[0].text
            
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.model,
                    "provider": "claude",
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                    "stop_reason": message.stop_reason,
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Claude API Error: {str(e)}",
                "draft": ""
            }

    def generate_with_context(
        self,
        prompt: str,
        outline: str,
        history: List[str],
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """
        根据上下文生成内容（带大纲和历史记录）
        
        Args:
            prompt: 生成指令
            outline: 当前大纲/任务
            history: 之前生成的内容列表
            max_tokens: 最大生成token数
            
        Returns:
            包含生成文本和元数据的字典
        """
        context_str = "\n".join(history) if history else "No previous content yet."
        
        full_prompt = f"""
背景信息：
- 大纲/任务: {outline}
- 历史内容: {context_str}

生成指令：
{prompt}

请根据以上信息生成内容。
"""
        
        return self.generate_draft(full_prompt, max_tokens)


class FlowerNetOrchestrator:
    """
    FlowerNet 流程编排器：
    管理整个循环流程（Generator -> Verifier -> Controller -> Generator ...）
    """
    
    def __init__(
        self,
        generator_url: str = "http://localhost:8002",
        verifier_url: str = "http://localhost:8000",
        controller_url: str = "http://localhost:8001",
        max_iterations: int = 5
    ):
        """
        初始化编排器
        
        Args:
            generator_url: Generator 服务的 URL
            verifier_url: Verifier 服务的 URL
            controller_url: Controller 服务的 URL
            max_iterations: 最大迭代次数
        """
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.max_iterations = max_iterations
        self.session = requests.Session()
        
        print(f"🌸 FlowerNet 编排器初始化:")
        print(f"  - Generator URL: {generator_url}")
        print(f"  - Verifier URL: {verifier_url}")
        print(f"  - Controller URL: {controller_url}")
        print(f"  - Max iterations: {max_iterations}")

    def generate_section(
        self,
        outline: str,
        initial_prompt: str,
        history: Optional[List[str]] = None,
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        生成一个段落，并进行验证-修改的循环
        
        流程：
        1. Generator 根据 prompt 生成 draft
        2. Verifier 检验 draft（相关性和冗余度）
        3. 如果验证不通过，Controller 修改 prompt
        4. 回到步骤1，直到验证通过或达到最大迭代次数
        
        Args:
            outline: 段落大纲
            initial_prompt: 初始生成提示
            history: 历史内容列表
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            
        Returns:
            包含最终生成内容和迭代过程的字典
        """
        if history is None:
            history = []
        
        current_prompt = initial_prompt
        iterations = 0
        all_drafts = []
        
        print(f"\n{'='*60}")
        print(f"📝 开始生成段落: {outline}")
        print(f"{'='*60}")
        
        while iterations < self.max_iterations:
            iterations += 1
            print(f"\n--- 迭代 {iterations}/{self.max_iterations} ---")
            
            # 1️⃣ 调用 Generator 生成 draft
            print(f"🎯 [Generator] 生成 draft...")
            gen_response = self._call_generator(current_prompt)
            
            if not gen_response.get("success"):
                print(f"❌ Generator 出错: {gen_response.get('error')}")
                return {
                    "success": False,
                    "error": f"Generator 错误: {gen_response.get('error')}",
                    "iterations": iterations
                }
            
            draft = gen_response.get("draft", "")
            all_drafts.append(draft)
            print(f"✅ 生成了 {len(draft)} 字符的内容")
            
            # 2️⃣ 调用 Verifier 验证 draft
            print(f"🔍 [Verifier] 验证内容...")
            verify_response = self._call_verifier(
                draft=draft,
                outline=outline,
                history=history,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold
            )
            
            if not verify_response.get("success"):
                print(f"❌ Verifier 出错: {verify_response.get('error')}")
                return {
                    "success": False,
                    "error": f"Verifier 错误: {verify_response.get('error')}",
                    "iterations": iterations
                }
            
            is_passed = verify_response.get("is_passed", False)
            rel_score = verify_response.get("relevancy_index", 0)
            red_score = verify_response.get("redundancy_index", 0)
            feedback = verify_response.get("feedback", "")
            
            print(f"📊 相关性: {rel_score:.4f} (阈值: {rel_threshold})")
            print(f"📊 冗余度: {red_score:.4f} (阈值: {red_threshold})")
            print(f"💬 反馈: {feedback}")
            
            # 3️⃣ 如果验证通过，返回结果
            if is_passed:
                print(f"\n✨ 内容验证通过！")
                history.append(draft)
                return {
                    "success": True,
                    "draft": draft,
                    "iterations": iterations,
                    "verification": {
                        "relevancy_index": rel_score,
                        "redundancy_index": red_score,
                        "feedback": feedback
                    },
                    "all_drafts": all_drafts
                }
            
            # 4️⃣ 如果验证不通过，调用 Controller 修改 prompt
            print(f"🔧 [Controller] 修改 prompt...")
            controller_response = self._call_controller(
                old_prompt=current_prompt,
                failed_draft=draft,
                feedback=verify_response,
                outline=outline,
                history=history
            )
            
            if not controller_response.get("success"):
                print(f"❌ Controller 出错: {controller_response.get('error')}")
                return {
                    "success": False,
                    "error": f"Controller 错误: {controller_response.get('error')}",
                    "iterations": iterations
                }
            
            current_prompt = controller_response.get("prompt", "")
            print(f"✅ Prompt 已修改，准备下一轮生成...")
        
        # 如果达到最大迭代次数仍未通过
        print(f"\n⚠️  达到最大迭代次数 ({self.max_iterations})，生成过程结束")
        
        # 返回最后生成的 draft 作为结果
        if all_drafts:
            history.append(all_drafts[-1])
            return {
                "success": True,
                "draft": all_drafts[-1],
                "iterations": iterations,
                "warning": f"达到最大迭代次数，可能内容不完全符合要求",
                "all_drafts": all_drafts
            }
        
        return {
            "success": False,
            "error": "无法生成满足要求的内容",
            "iterations": iterations
        }

    def _call_generator(self, prompt: str) -> Dict[str, Any]:
        """调用 Generator API"""
        try:
            response = self.session.post(
                f"{self.generator_url}/generate",
                json={"prompt": prompt},
                timeout=60
            )
            return response.json()
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _call_verifier(
        self,
        draft: str,
        outline: str,
        history: List[str],
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """调用 Verifier API"""
        try:
            response = self.session.post(
                f"{self.verifier_url}/verify",
                json={
                    "draft": draft,
                    "outline": outline,
                    "history": history,
                    "rel_threshold": rel_threshold,
                    "red_threshold": red_threshold
                },
                timeout=60
            )
            data = response.json()
            return {
                "success": True,
                **data
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _call_controller(
        self,
        old_prompt: str,
        failed_draft: str,
        feedback: Dict[str, Any],
        outline: str,
        history: List[str]
    ) -> Dict[str, Any]:
        """调用 Controller API"""
        try:
            response = self.session.post(
                f"{self.controller_url}/refine_prompt",
                json={
                    "old_prompt": old_prompt,
                    "failed_draft": failed_draft,
                    "feedback": feedback,
                    "outline": outline,
                    "history": history
                },
                timeout=60
            )
            return response.json()
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def generate_document(
        self,
        title: str,
        outline_list: List[str],
        system_prompt: str = "",
        rel_threshold: float = 0.6,
        red_threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        生成完整文档（多个段落）
        
        Args:
            title: 文档标题
            outline_list: 大纲列表
            system_prompt: 系统级提示（对所有段落适用）
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            
        Returns:
            包含完整文档和生成过程的字典
        """
        print(f"\n{'#'*60}")
        print(f"📄 开始生成文档: {title}")
        print(f"{'#'*60}")
        print(f"大纲: {outline_list}")
        
        document = {
            "title": title,
            "sections": [],
            "total_iterations": 0,
            "success_count": 0,
            "failed_sections": []
        }
        
        history = []
        
        for idx, outline in enumerate(outline_list, 1):
            print(f"\n[{idx}/{len(outline_list)}] 生成段落...")
            
            # 为每个段落生成初始 prompt
            initial_prompt = self._generate_initial_prompt(
                system_prompt=system_prompt,
                outline=outline,
                section_number=idx,
                total_sections=len(outline_list)
            )
            
            # 调用生成-验证循环
            result = self.generate_section(
                outline=outline,
                initial_prompt=initial_prompt,
                history=history,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold
            )
            
            document["total_iterations"] += result.get("iterations", 0)
            
            if result.get("success"):
                document["sections"].append({
                    "outline": outline,
                    "content": result.get("draft", ""),
                    "iterations": result.get("iterations", 0),
                    "verification": result.get("verification", {})
                })
                document["success_count"] += 1
                history.append(result.get("draft", ""))
            else:
                document["failed_sections"].append({
                    "outline": outline,
                    "error": result.get("error", "Unknown error")
                })
        
        # 生成最终报告
        print(f"\n{'#'*60}")
        print(f"📊 文档生成完成")
        print(f"{'#'*60}")
        print(f"✅ 成功段落: {document['success_count']}/{len(outline_list)}")
        print(f"❌ 失败段落: {len(document['failed_sections'])}/{len(outline_list)}")
        print(f"总迭代次数: {document['total_iterations']}")
        
        return document

    def _generate_initial_prompt(
        self,
        system_prompt: str,
        outline: str,
        section_number: int = 1,
        total_sections: int = 1
    ) -> str:
        """生成初始 prompt"""
        prompt = f"""
任务：编写内容段落

段落编号: {section_number}/{total_sections}
段落主题: {outline}

"""
        if system_prompt:
            prompt += f"系统指示: {system_prompt}\n\n"
        
        prompt += f"""
请根据上述主题编写一段相关内容。要求：
1. 内容应严格围绕主题「{outline}」展开
2. 段落应该逻辑清晰、表述准确
3. 避免与之前的内容重复（如果有的话）
4. 长度适中（200-500 字）
"""
        
        return prompt
