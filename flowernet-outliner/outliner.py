"""
FlowerNet Outliner - 文档大纲生成与内容提示词管理
根据用户需求生成文档结构，并为每个段落生成专用的 Content Prompt
支持多种 LLM: Azure OpenAI, Ollama (本地), Google Gemini, OpenRouter
"""

import os
import json
import time
import random
import re
import requests
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def _is_render_runtime() -> bool:
    return any(os.getenv(key) for key in ("RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_HOSTNAME"))


def _is_local_ollama_url(url: str) -> bool:
    normalized = (url or "").strip().lower()
    return (
        normalized.startswith("http://localhost")
        or normalized.startswith("https://localhost")
        or normalized.startswith("http://127.0.0.1")
        or normalized.startswith("https://127.0.0.1")
        or normalized.startswith("http://0.0.0.0")
        or normalized.startswith("https://0.0.0.0")
    )

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class FlowerNetOutliner:
    """
    文档大纲生成器
    - 第一阶段：使用 Document Structure Prompt 生成层级大纲
    - 第二阶段：为每个 subsection 生成 Content Prompt
    - 支持 Azure OpenAI、Ollama (本地)、Google Gemini、OpenRouter
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini", provider: str = "azure"):
        """
        初始化 Outliner
        
        Args:
            api_key: API Key（兼容参数，优先从环境变量读取）
            model: 使用的模型名称
                - Ollama: "qwen2.5:7b", "llama3.1:8b" 等
                - Azure OpenAI: "gpt-4o-mini"
                - Gemini: "models/gemini-2.5-flash"
            provider: LLM 提供商，支持单个或链式（如 "azure,ollama"）
        """
        requested_provider = provider or os.getenv("OUTLINER_PROVIDER_CHAIN", "azure")
        parsed_chain = [p.strip().lower() for p in requested_provider.split(",") if p.strip()]
        self.provider_chain = parsed_chain or ["azure", "ollama"]

        self.model = model
        self.azure_model = os.getenv("OUTLINER_AZURE_MODEL", os.getenv("AZURE_OPENAI_MODEL", model or "gpt-4o-mini"))
        self.azure_api_key = os.getenv("OUTLINER_AZURE_API_KEY", os.getenv("AZURE_OPENAI_API_KEY", "")).strip()
        self.azure_api_base = os.getenv("OUTLINER_AZURE_API_BASE", os.getenv("AZURE_OPENAI_API_BASE", "")).strip()
        self.azure_api_version = os.getenv("OUTLINER_AZURE_API_VERSION", os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")).strip()
        self.azure_deployment_name = os.getenv("OUTLINER_AZURE_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")).strip()
        self.gemini_model = os.getenv("OUTLINER_GEMINI_MODEL", "models/gemini-2.5-flash-lite")
        self.openrouter_model = os.getenv("OUTLINER_OPENROUTER_MODEL", os.getenv("OPENROUTER_MODEL", "qwen/qwen3-coder:free"))
        self.ollama_model = os.getenv("OUTLINER_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openrouter_api_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions").rstrip("/")
        self.openrouter_referrer = os.getenv("OPENROUTER_HTTP_REFERER", os.getenv("PUBLIC_BASE_URL", "https://flowernet-web.onrender.com"))
        self.openrouter_app_name = os.getenv("OPENROUTER_APP_NAME", "FlowerNet")

        self.ollama_url = os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/')
        self.ollama_retries = int(os.getenv('OLLAMA_RETRIES', '5'))
        self.ollama_backoff = float(os.getenv('OLLAMA_BACKOFF', '2.0'))
        self.ollama_max_backoff = float(os.getenv('OLLAMA_MAX_BACKOFF', '45.0'))
        self.provider_retries = int(os.getenv('PROVIDER_RETRIES', '4'))
        self.provider_backoff = float(os.getenv('PROVIDER_BACKOFF', '2.0'))
        self.provider_max_backoff = float(os.getenv('PROVIDER_MAX_BACKOFF', '90.0'))
        self.provider_jitter = float(os.getenv('PROVIDER_JITTER', '0.35'))
        self.provider_min_interval = float(os.getenv('PROVIDER_MIN_INTERVAL', '1.2'))
        self._provider_next_allowed: Dict[str, float] = {}

        self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
        self.client = genai.Client(api_key=self.api_key) if (self.api_key and GEMINI_AVAILABLE) else None
        self.last_provider_used = ""

        print("✅ Outliner 初始化成功:")
        print(f"  - Provider chain: {self.provider_chain}")
        print(f"  - Azure model: {self.azure_model}")
        print(f"  - Azure deployment: {self.azure_deployment_name}")
        print(f"  - Gemini model: {self.gemini_model}")
        print(f"  - OpenRouter model: {self.openrouter_model}")
        print(f"  - Ollama model: {self.ollama_model}")
        print(f"  - Ollama URL: {self.ollama_url}")
    
    def generate_document_structure(
        self,
        user_background: str,
        user_requirements: str,
        max_sections: int = 5,
        max_subsections_per_section: int = 4
    ) -> Dict[str, Any]:
        """
        阶段 1: 生成文档结构大纲
        
        Args:
            user_background: 用户提供的背景信息
            user_requirements: 用户需求描述
            max_sections: 最大 section 数量
            max_subsections_per_section: 每个 section 最大 subsection 数量
            
        Returns:
            {
                "title": "文档标题",
                "sections": [
                    {
                        "id": "section_1",
                        "title": "Section 标题",
                        "subsections": [
                            {"id": "subsection_1_1", "title": "子标题", "description": "简述"},
                            ...
                        ]
                    },
                    ...
                ]
            }
        """
        # 构建 Document Structure Prompt
        structure_prompt = f"""
你是一个专业的文档结构设计专家。根据用户提供的背景和需求，生成一个详细的文档大纲。

**用户背景**:
{user_background}

**用户需求**:
{user_requirements}

**任务要求**:
1. 生成一个清晰的文档标题
2. 将文档分为 {max_sections} 个主要章节（Section）- 必须恰好是 {max_sections} 个！
3. 每个章节包含 {max_subsections_per_section} 个子章节（Subsection）- 每个章节都必须恰好是 {max_subsections_per_section} 个！
4. 每个子章节需要有标题和简短描述（1-2句话说明该段应该写什么）
5. 总共应该生成：{max_sections * max_subsections_per_section} 个小节

**输出格式**（严格按照 JSON 格式）:
{{
  "title": "文档总标题",
  "sections": [
    {{
      "id": "section_1",
      "title": "第一章标题",
      "subsections": [
        {{
          "id": "subsection_1_1",
          "title": "第一节标题",
          "description": "该节应该介绍...（1-2句话）"
        }},
        {{
          "id": "subsection_1_2",
          "title": "第二节标题",
          "description": "该节应该讨论...（1-2句话）"
        }}
      ]
    }},
    {{
      "id": "section_2",
      "title": "第二章标题",
      "subsections": [...]
    }}
  ]
}}

请直接输出 JSON，不要添加任何额外说明或代码块标记。
"""
        
        try:
            # 调用 LLM 生成大纲（自动降级）
            structure_text, llm_metadata = self._generate_text_with_fallback(structure_prompt, max_tokens=4000)
            
            # 解析 JSON
            
            # 移除可能的代码块标记
            if structure_text.startswith("```json"):
                structure_text = structure_text[7:]
            if structure_text.startswith("```"):
                structure_text = structure_text[3:]
            if structure_text.endswith("```"):
                structure_text = structure_text[:-3]
            
            structure = json.loads(structure_text.strip())
            
            # 验证结构是否符合要求
            sections = structure.get('sections', [])
            actual_section_count = len(sections)
            
            # 检查章节数量
            if actual_section_count != max_sections:
                print(f"⚠️  警告: 大纲的章节数 ({actual_section_count}) 与要求不符 ({max_sections})")
                # 如果太少，补充空章节
                while actual_section_count < max_sections:
                    actual_section_count += 1
                    sections.append({
                        "id": f"section_{actual_section_count}",
                        "title": f"第{actual_section_count}章 (自动生成)",
                        "subsections": [
                            {
                                "id": f"subsection_{actual_section_count}_{j}",
                                "title": f"第{j}小节",
                                "description": "补充内容"
                            }
                            for j in range(1, max_subsections_per_section + 1)
                        ]
                    })
                structure['sections'] = sections
            
            # 检查每个章节的小节数量
            for section in sections:
                subsections = section.get('subsections', [])
                actual_subsection_count = len(subsections)
                if actual_subsection_count != max_subsections_per_section:
                    print(f"⚠️  警告: 章节 '{section.get('title')}' 的小节数 ({actual_subsection_count}) 与要求不符 ({max_subsections_per_section})")
                    # 补充缺少的小节
                    while actual_subsection_count < max_subsections_per_section:
                        actual_subsection_count += 1
                        subsections.append({
                            "id": f"{section.get('id')}_{actual_subsection_count}",
                            "title": f"第{actual_subsection_count}小节 (自动生成)",
                            "description": "补充内容"
                        })
                    section['subsections'] = subsections
            
            total_subsections = sum(len(s.get('subsections', [])) for s in sections)
            
            print(f"✅ 文档大纲生成成功:")
            print(f"  - 标题: {structure.get('title', 'N/A')}")
            print(f"  - Sections: {len(structure.get('sections', []))}")
            print(f"  - 总小节数: {total_subsections} (预期: {max_sections * max_subsections_per_section})")
            
            metadata = {
                "provider_chain": self.provider_chain,
                "active_provider": llm_metadata.get("provider", self.last_provider_used),
                "model": llm_metadata.get("model", self.model),
                "prompt_tokens": llm_metadata.get("prompt_tokens", 0),
                "output_tokens": llm_metadata.get("output_tokens", 0),
            }
            
            return {
                "success": True,
                "structure": structure,
                "metadata": metadata
            }
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败: {e}")
            print(f"原始输出: {structure_text[:500]}")
            return {
                "success": False,
                "error": f"JSON 解析失败: {str(e)}",
                "raw_output": structure_text
            }
        except Exception as e:
            print(f"❌ 大纲生成失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @staticmethod
    def _is_transient_provider_error(message: str) -> bool:
        text = (message or "").lower()
        transient_tokens = [
            "429", "rate", "resource_exhausted", "quota", "too many requests",
            "timeout", "timed out", "temporarily", "503", "502", "504", "connection"
        ]
        return any(token in text for token in transient_tokens)

    @staticmethod
    def _parse_retry_after_seconds(retry_after: Any) -> Optional[float]:
        if retry_after is None:
            return None
        value = str(retry_after).strip()
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (dt - now).total_seconds())
        except Exception:
            return None

    def _compute_retry_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        base = self.provider_backoff * (2 ** max(0, attempt - 1))
        jitter = random.uniform(0, self.provider_jitter)
        delay = min(base + jitter, self.provider_max_backoff)
        if retry_after is not None:
            delay = max(delay, min(float(retry_after), self.provider_max_backoff))
        return delay

    @staticmethod
    def _extract_retry_after_from_message(message: str) -> Optional[float]:
        text = str(message or "")
        patterns = [
            r"retry_after\s*=\s*([0-9]+(?:\.[0-9]+)?)",
            r"retry in\s*([0-9]+(?:\.[0-9]+)?)\s*s",
            r"retrydelay[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*s",
        ]
        for pattern in patterns:
            matched = re.search(pattern, text, flags=re.IGNORECASE)
            if matched:
                try:
                    return max(0.0, float(matched.group(1)))
                except (TypeError, ValueError):
                    continue
        return None

    def _wait_for_provider_slot(self, provider: str):
        next_allowed_at = self._provider_next_allowed.get(provider, 0.0)
        now = time.time()
        if next_allowed_at > now:
            time.sleep(next_allowed_at - now)

    def _mark_provider_slot(self, provider: str, extra_delay: float = 0.0):
        self._provider_next_allowed[provider] = time.time() + max(self.provider_min_interval, extra_delay)

    def _generate_text_with_fallback(self, prompt: str, max_tokens: int) -> Tuple[str, Dict[str, Any]]:
        errors: List[str] = []
        for provider in self.provider_chain:
            provider_errors: List[str] = []
            for attempt in range(1, self.provider_retries + 1):
                try:
                    self._wait_for_provider_slot(provider)
                    text, metadata = self._generate_text_with_provider(provider=provider, prompt=prompt, max_tokens=max_tokens)
                    self._mark_provider_slot(provider)
                    self.last_provider_used = provider
                    return text, metadata
                except Exception as exc:
                    error_message = str(exc)
                    provider_errors.append(error_message)
                    retry_after = self._extract_retry_after_from_message(error_message)

                    should_retry = self._is_transient_provider_error(error_message) and attempt < self.provider_retries
                    if should_retry:
                        retry_delay = self._compute_retry_delay(attempt, retry_after=retry_after)
                        self._mark_provider_slot(provider, extra_delay=retry_delay)
                        time.sleep(retry_delay)
                        continue
                    break

            errors.append(f"{provider}: {' | '.join(provider_errors)}")

        raise Exception("所有 LLM 提供商都失败: " + " | ".join(errors))

    def _generate_text_with_provider(self, provider: str, prompt: str, max_tokens: int) -> Tuple[str, Dict[str, Any]]:
        if provider == "azure":
            if not self.azure_api_key:
                raise Exception("AZURE_OPENAI_API_KEY 未配置")
            if not self.azure_api_base:
                raise Exception("AZURE_OPENAI_API_BASE 未配置")
            if not self.azure_deployment_name:
                raise Exception("AZURE_OPENAI_DEPLOYMENT_NAME 未配置")

            base = self.azure_api_base.rstrip("/")
            if not base.endswith("/openai"):
                base = f"{base}/openai"
            url = f"{base}/deployments/{self.azure_deployment_name}/chat/completions"

            payload = {
                "model": self.azure_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": max_tokens,
            }
            headers = {
                "api-key": self.azure_api_key,
                "Content-Type": "application/json",
            }
            params = {"api-version": self.azure_api_version}

            try:
                response = requests.post(url, params=params, json=payload, headers=headers, timeout=120)
                response.raise_for_status()
                data = response.json()
                choice = ((data.get("choices") or [{}])[0] or {})
                msg = choice.get("message") or {}
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = [str(it.get("text", "")) for it in content if isinstance(it, dict)]
                    content = "".join(parts)
                text = str(content).strip()
                if not text:
                    raise Exception("Azure OpenAI 返回空响应")
                usage = data.get("usage") or {}
                return text, {
                    "provider": "azure",
                    "model": self.azure_model,
                    "deployment": self.azure_deployment_name,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                }
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "unknown")
                retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                retry_after = self._parse_retry_after_seconds(retry_after_raw)
                if retry_after is not None:
                    raise Exception(f"Azure OpenAI HTTP {status}, retry_after={retry_after}")
                raise Exception(f"Azure OpenAI HTTP {status}: {str(exc)}")
            except requests.RequestException as exc:
                raise Exception(f"Azure OpenAI request error: {str(exc)}")

        if provider == "gemini":
            if not GEMINI_AVAILABLE:
                raise Exception("google-genai 未安装")
            if not self.api_key:
                raise Exception("GOOGLE_API_KEY 未配置")
            if self.client is None:
                self.client = genai.Client(api_key=self.api_key)

            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=max_tokens
                )
            )
            text = (response.text or "").strip()
            if not text:
                raise Exception("Gemini 返回空响应")
            return text, {
                "provider": "gemini",
                "model": self.gemini_model,
                "prompt_tokens": response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
                "output_tokens": response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
            }

        if provider == "openrouter":
            if not self.openrouter_api_key:
                raise Exception("OPENROUTER_API_KEY 未配置")
            try:
                payload = {
                    "model": self.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                }
                headers = {
                    "Authorization": f"Bearer {self.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self.openrouter_referrer,
                    "X-Title": self.openrouter_app_name,
                }
                response = requests.post(self.openrouter_api_url, json=payload, headers=headers, timeout=120)
                response.raise_for_status()
                data = response.json()
                choice = ((data.get("choices") or [{}])[0] or {})
                msg = choice.get("message") or {}
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = [str(it.get("text", "")) for it in content if isinstance(it, dict)]
                    content = "".join(parts)
                text = str(content).strip()
                if not text:
                    raise Exception("OpenRouter 返回空响应")
                usage = data.get("usage") or {}
                return text, {
                    "provider": "openrouter",
                    "model": self.openrouter_model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                }
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "unknown")
                retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                retry_after = self._parse_retry_after_seconds(retry_after_raw)
                if retry_after is not None:
                    raise Exception(f"OpenRouter HTTP {status}, retry_after={retry_after}")
                raise Exception(f"OpenRouter HTTP {status}: {str(exc)}")
            except requests.RequestException as exc:
                raise Exception(f"OpenRouter request error: {str(exc)}")

        if provider == "ollama":
            if _is_render_runtime() and _is_local_ollama_url(self.ollama_url):
                raise Exception("Render 环境 OLLAMA_URL 不能是 localhost/127.0.0.1")
            text = self._call_ollama(prompt, max_tokens=max_tokens)
            return text, {
                "provider": "ollama",
                "model": self.ollama_model,
            }

        raise Exception(f"不支持的 provider: {provider}")
    
    def generate_detailed_section_outlines(
            self,
            structure: Dict[str, Any],
            user_background: str,
            user_requirements: str,
        ) -> Dict[str, Any]:
            """
            阶段 2: 基于整篇结构，再调用一次 LLM 生成每个 section/subsection 的详细大纲。
            """
            structure_json = json.dumps(structure, ensure_ascii=False, indent=2)
            detailed_prompt = f"""
    你是一个专业的学术写作大纲设计专家。
    
    现在已经有一份整篇文章的总体结构，请你基于这个总体结构，再进一步生成每个 section 和 subsection 的详细写作大纲。
    
    **用户背景**:
    {user_background}
    
    **用户需求**:
    {user_requirements}
    
    **已有总体结构**:
    {structure_json}
    
    **你的任务**:
    1. 保持原有 title、section id、subsection id 不变
    2. 为每个 section 生成更详细的 `section_outline`
    3. 为每个 subsection 生成更详细的 `outline`
    4. `description` 可以优化，但必须与总体结构一致
    5. 每个 `outline` 要写清楚该小节应覆盖的核心点、逻辑顺序、避免重复的方向
    
    **输出格式**（严格 JSON）:
    {{
      "title": "保持原文档标题",
      "sections": [
        {{
          "id": "section_1",
          "title": "章节标题",
          "description": "该章节的定位",
          "section_outline": "该章节整体应该如何展开、章节内部逻辑如何组织、与前后章节如何衔接",
          "subsections": [
            {{
              "id": "subsection_1_1",
              "title": "小节标题",
              "description": "该小节的简要说明",
              "outline": "该小节的详细写作大纲，包含要点、顺序、约束、避免重复的要求"
            }}
          ]
        }}
      ]
    }}
    
    请直接输出 JSON，不要输出额外解释，不要修改任何 id。
    """
            detailed_text, llm_metadata = self._generate_text_with_fallback(detailed_prompt, max_tokens=5000)
    
            if detailed_text.startswith("```json"):
                detailed_text = detailed_text[7:]
            if detailed_text.startswith("```"):
                detailed_text = detailed_text[3:]
            if detailed_text.endswith("```"):
                detailed_text = detailed_text[:-3]
    
            detailed = json.loads(detailed_text.strip())
            detailed.setdefault("title", structure.get("title", ""))
    
            base_sections = structure.get("sections", []) if isinstance(structure.get("sections", []), list) else []
            detailed_sections = detailed.get("sections", []) if isinstance(detailed.get("sections", []), list) else []
            detailed_by_section_id = {
                str(section.get("id", "")).strip(): section
                for section in detailed_sections
                if isinstance(section, dict) and str(section.get("id", "")).strip()
            }
    
            normalized_sections: List[Dict[str, Any]] = []
            for base_section in base_sections:
                if not isinstance(base_section, dict):
                    continue
    
                section_id = str(base_section.get("id", "")).strip()
                detailed_section = detailed_by_section_id.get(section_id, {})
                base_subsections = base_section.get("subsections", []) if isinstance(base_section.get("subsections", []), list) else []
                detailed_subsections = detailed_section.get("subsections", []) if isinstance(detailed_section.get("subsections", []), list) else []
                detailed_by_subsection_id = {
                    str(sub.get("id", "")).strip(): sub
                    for sub in detailed_subsections
                    if isinstance(sub, dict) and str(sub.get("id", "")).strip()
                }
    
                normalized_subsections: List[Dict[str, Any]] = []
                for base_subsection in base_subsections:
                    if not isinstance(base_subsection, dict):
                        continue
                    subsection_id = str(base_subsection.get("id", "")).strip()
                    detailed_subsection = detailed_by_subsection_id.get(subsection_id, {})
                    subsection_description = str(
                        detailed_subsection.get("description")
                        or base_subsection.get("description")
                        or f"围绕“{base_subsection.get('title', subsection_id)}”展开"
                    ).strip()
                    subsection_outline = str(
                        detailed_subsection.get("outline")
                        or subsection_description
                    ).strip()
    
                    normalized_subsections.append({
                        "id": subsection_id,
                        "title": str(base_subsection.get("title") or detailed_subsection.get("title") or subsection_id).strip(),
                        "description": subsection_description,
                        "outline": subsection_outline,
                    })
    
                section_description = str(
                    detailed_section.get("description")
                    or base_section.get("description")
                    or ""
                ).strip()
                section_outline = str(
                    detailed_section.get("section_outline")
                    or section_description
                    or f"本章围绕“{base_section.get('title', section_id)}”展开。"
                ).strip()
    
                normalized_sections.append({
                    "id": section_id,
                    "title": str(base_section.get("title") or detailed_section.get("title") or section_id).strip(),
                    "description": section_description,
                    "section_outline": section_outline,
                    "subsections": normalized_subsections,
                })
    
            return {
                "success": True,
                "structure": {
                    "title": detailed.get("title") or structure.get("title", ""),
                    "sections": normalized_sections,
                },
                "metadata": {
                    "provider": llm_metadata.get("provider", self.last_provider_used),
                    "model": llm_metadata.get("model", self.model),
                    "prompt_tokens": llm_metadata.get("prompt_tokens", 0),
                    "output_tokens": llm_metadata.get("output_tokens", 0),
                },
            }
    
    def generate_content_prompts(
        self,
        structure: Dict[str, Any],
        user_background: str,
        user_requirements: str
    ) -> List[Dict[str, Any]]:
        """阶段 3: 基于详细 subsection outline 生成 Content Prompt。"""
        content_prompts = []
        order = 1
        
        for section in structure.get("sections", []):
            section_id = section.get("id", "")
            section_title = section.get("title", "")
            section_outline = section.get("section_outline", "")
            
            for subsection in section.get("subsections", []):
                subsection_id = subsection.get("id", "")
                subsection_title = subsection.get("title", "")
                subsection_desc = subsection.get("description", "")
                subsection_outline = subsection.get("outline", subsection_desc)
                
                content_prompt = self._build_content_prompt(
                    document_title=structure.get("title", ""),
                    section_title=section_title,
                    subsection_title=subsection_title,
                    subsection_description=subsection_outline,
                    user_background=user_background,
                    user_requirements=user_requirements
                )
                
                content_prompts.append({
                    "section_id": section_id,
                    "subsection_id": subsection_id,
                    "section_title": section_title,
                    "subsection_title": subsection_title,
                    "section_outline": section_outline,
                    "subsection_description": subsection_desc,
                    "subsection_outline": subsection_outline,
                    "content_prompt": content_prompt,
                    "order": order
                })
                
                order += 1
        
        print(f"✅ 生成了 {len(content_prompts)} 个 Content Prompts")
        return content_prompts

    def _build_content_prompt(
        self,
        document_title: str,
        section_title: str,
        subsection_title: str,
        subsection_description: str,
        user_background: str,
        user_requirements: str
    ) -> str:
        """构建单个 subsection 的 Content Prompt"""
        prompt = f"""
你正在撰写一篇关于"{document_title}"的文档。

**整体背景**:
{user_background}

**整体需求**:
{user_requirements}

**当前章节**: {section_title}
**当前小节**: {subsection_title}

**该小节详细大纲**:
{subsection_description}

**写作要求**:
1. 严格按照当前小节详细大纲展开，不写大纲之外的内容
2. 详细展开，字数控制在 500～800 字
3. 使用清晰的逻辑结构，可以包含小标题
4. 语言专业、准确，避免空洞内容
5. 如果涉及技术概念，需要提供具体例子或解释
6. 该内容将作为历史记录，下一小节生成时会参考它进行去重检测，请尽量避免冗余表达

请直接输出该小节的正文内容，不要添加"该小节内容如下"等引导语。
"""
        return prompt.strip()
    
    def generate_full_outline(
        self,
        user_background: str,
        user_requirements: str,
        max_sections: int = 5,
        max_subsections_per_section: int = 4
    ) -> Dict[str, Any]:
        """
        完整流程: 第一次 LLM 生成总体结构，第二次 LLM 生成 section/subsection 详细大纲，再生成 prompt。
        """
        structure_result = self.generate_document_structure(
            user_background=user_background,
            user_requirements=user_requirements,
            max_sections=max_sections,
            max_subsections_per_section=max_subsections_per_section
        )
        
        if not structure_result.get("success"):
            return structure_result
        
        base_structure = structure_result["structure"]
        detailed_outline_result = self.generate_detailed_section_outlines(
            structure=base_structure,
            user_background=user_background,
            user_requirements=user_requirements,
        )

        if not detailed_outline_result.get("success"):
            return detailed_outline_result

        structure = detailed_outline_result["structure"]
        content_prompts = self.generate_content_prompts(
            structure=structure,
            user_background=user_background,
            user_requirements=user_requirements
        )
        
        return {
            "success": True,
            "document_title": structure.get("title", ""),
            "structure": structure,
            "content_prompts": content_prompts,
            "total_subsections": len(content_prompts),
            "metadata": {
                "structure_generation": structure_result.get("metadata", {}),
                "detail_generation": detailed_outline_result.get("metadata", {}),
            }
        }
    
    def _call_ollama(self, prompt: str, max_tokens: int = 2000) -> str:
        """调用 Ollama API 生成内容"""
        try:
            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": 0.7
                }
            }
            headers = {
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "FlowerNet-Outliner/1.0"
            }
            response = None
            last_error = ""
            for attempt in range(1, self.ollama_retries + 1):
                try:
                    response = requests.post(
                        f"{self.ollama_url}/api/generate",
                        json=payload,
                        headers=headers,
                        timeout=300
                    )
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    last_error = str(exc)
                    if attempt >= self.ollama_retries:
                        raise Exception(last_error)
                    retry_delay = min(self.ollama_backoff * attempt, self.ollama_max_backoff)
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    if status_code == 429:
                        retry_after = getattr(exc.response, "headers", {}).get("Retry-After", "")
                        try:
                            retry_delay = max(retry_delay, float(retry_after))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(retry_delay)

            if not response.text.strip():
                raise Exception("Ollama 返回空响应")

            result = response.json()
            return result.get('response', '')
            
        except Exception as e:
            raise Exception(f"Ollama API Error: {str(e)}")


# ============ 测试代码 ============

if __name__ == "__main__":
    # 测试用例
    outliner = FlowerNetOutliner()
    
    result = outliner.generate_full_outline(
        user_background="我是一名计算机科学学生，需要撰写关于人工智能的学术论文。",
        user_requirements="需要一篇全面介绍人工智能的文章，包括历史、核心技术、应用场景和未来展望。要求内容专业、逻辑清晰。",
        max_sections=4,
        max_subsections_per_section=3
    )
    
    if result["success"]:
        print(f"\n📄 文档标题: {result['document_title']}")
        print(f"📊 总共 {result['total_subsections']} 个段落需要生成\n")
        
        # 显示前 3 个 Content Prompt
        for prompt_info in result["content_prompts"][:3]:
            print(f"--- {prompt_info['section_title']} > {prompt_info['subsection_title']} ---")
            print(f"Prompt 长度: {len(prompt_info['content_prompt'])} 字符\n")
    else:
        print(f"❌ 失败: {result.get('error')}")
