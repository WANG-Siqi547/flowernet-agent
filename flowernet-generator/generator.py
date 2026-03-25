"""
FlowerNet Generator - LLM驱动的内容生成模块
根据prompt使用LLM生成draft内容
支持多种 LLM 提供商: Azure OpenAI, Anthropic Claude, Google Gemini, OpenRouter, Ollama
"""

import os
import requests
import json
import subprocess
import time
import random
import re
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
    - Azure OpenAI (需要 AZURE_OPENAI_API_KEY)
    - Anthropic Claude (需要 ANTHROPIC_API_KEY)
    - Google Gemini (需要 GOOGLE_API_KEY)
    - Ollama (本地运行，完全免费无限制)
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini", provider: str = "azure"):
        """
        初始化生成器
        
        Args:
            api_key: API key，如果不提供则从环境变量读取
            model: 使用的模型名称
                - Azure OpenAI: "gpt-4o-mini"（部署名由 AZURE_OPENAI_DEPLOYMENT_NAME 指定）
                - Claude: "claude-3-5-sonnet-20241022"
                - Gemini: "models/gemini-2.5-flash" (免费, 最新), "models/gemini-2.5-pro" (免费但有限制)
                - Ollama: "qwen2.5:7b", "llama3.1:8b", "mistral:7b" 等
            provider: LLM 提供商，支持单个或链式（如 "azure,ollama"）
        """
        requested_provider = provider or os.getenv("GENERATOR_PROVIDER_CHAIN", "azure")
        parsed_chain = [p.strip().lower() for p in requested_provider.split(",") if p.strip()]
        self.provider_chain = parsed_chain or ["azure", "ollama"]

        self.model = model
        self.azure_model = os.getenv("GENERATOR_AZURE_MODEL", os.getenv("AZURE_OPENAI_MODEL", model or "gpt-4o-mini"))
        self.azure_api_key = os.getenv("GENERATOR_AZURE_API_KEY", os.getenv("AZURE_OPENAI_API_KEY", "")).strip()
        self.azure_api_base = os.getenv("GENERATOR_AZURE_API_BASE", os.getenv("AZURE_OPENAI_API_BASE", "")).strip()
        self.azure_api_version = os.getenv("GENERATOR_AZURE_API_VERSION", os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")).strip()
        self.azure_deployment_name = os.getenv("GENERATOR_AZURE_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")).strip()
        self.gemini_model = os.getenv("GENERATOR_GEMINI_MODEL", "models/gemini-2.5-flash-lite")
        self.openrouter_model = os.getenv("GENERATOR_OPENROUTER_MODEL", os.getenv("OPENROUTER_MODEL", "qwen/qwen3-coder:free"))
        self.ollama_model = os.getenv("GENERATOR_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openrouter_api_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions").rstrip("/")
        self.openrouter_referrer = os.getenv("OPENROUTER_HTTP_REFERER", os.getenv("PUBLIC_BASE_URL", "https://flowernet-web.onrender.com"))
        self.openrouter_app_name = os.getenv("OPENROUTER_APP_NAME", "FlowerNet")
        self.public_url = os.getenv('GENERATOR_PUBLIC_URL', 'http://localhost:8002')
        self.ollama_url = os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/')
        self.ollama_retries = int(os.getenv('OLLAMA_RETRIES', '5'))
        self.ollama_backoff = float(os.getenv('OLLAMA_BACKOFF', '2.0'))
        self.ollama_max_backoff = float(os.getenv('OLLAMA_MAX_BACKOFF', '45.0'))
        self.provider_retries = int(os.getenv('PROVIDER_RETRIES', '4'))
        self.provider_backoff = float(os.getenv('PROVIDER_BACKOFF', '2.0'))
        self.provider_max_backoff = float(os.getenv('PROVIDER_MAX_BACKOFF', '90.0'))
        self.provider_jitter = float(os.getenv('PROVIDER_JITTER', '0.35'))
        self.provider_min_interval = float(os.getenv('PROVIDER_MIN_INTERVAL', '1.0'))
        self._provider_next_allowed: Dict[str, float] = {}

        self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
        self.client = genai.Client(api_key=self.api_key) if (self.api_key and GEMINI_AVAILABLE) else None
        self.claude_api_key = os.getenv('ANTHROPIC_API_KEY', '')
        self.claude_client = anthropic.Anthropic(api_key=self.claude_api_key) if (self.claude_api_key and ANTHROPIC_AVAILABLE) else None
        self.last_provider_used = ""

        print("✅ Generator 初始化:")
        print(f"  - Provider chain: {self.provider_chain}")
        print(f"  - Azure model: {self.azure_model}")
        print(f"  - Azure deployment: {self.azure_deployment_name}")
        print(f"  - Gemini model: {self.gemini_model}")
        print(f"  - OpenRouter model: {self.openrouter_model}")
        print(f"  - Ollama model: {self.ollama_model}")
        print(f"  - Ollama URL: {self.ollama_url}")
        print(f"  - Public URL: {self.public_url}")

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
            errors: List[str] = []
            for provider in self.provider_chain:
                provider_errors: List[str] = []
                for attempt in range(1, self.provider_retries + 1):
                    self._wait_for_provider_slot(provider)

                    if provider == "azure":
                        result = self._generate_with_azure(prompt, max_tokens)
                    elif provider == "gemini":
                        result = self._generate_with_gemini(prompt, max_tokens)
                    elif provider == "openrouter":
                        result = self._generate_with_openrouter(prompt, max_tokens)
                    elif provider == "claude":
                        result = self._generate_with_claude(prompt, max_tokens)
                    elif provider == "ollama":
                        result = self._generate_with_ollama(prompt, max_tokens)
                    else:
                        result = {"success": False, "error": f"Unknown provider: {provider}", "draft": ""}

                    if result.get("success"):
                        self._mark_provider_slot(provider)
                        self.last_provider_used = provider
                        meta = result.get("metadata") or {}
                        if "fallback_chain" not in meta:
                            meta["fallback_chain"] = self.provider_chain
                        result["metadata"] = meta
                        return result

                    error_message = result.get("error", "unknown error")
                    provider_errors.append(str(error_message))
                    should_retry = self._is_transient_provider_error(str(error_message)) and attempt < self.provider_retries
                    if should_retry:
                        retry_after = result.get("retry_after")
                        if retry_after is None:
                            retry_after = self._extract_retry_after_from_message(str(error_message))
                        retry_delay = self._compute_retry_delay(attempt, retry_after=retry_after)
                        self._mark_provider_slot(provider, extra_delay=retry_delay)
                        time.sleep(retry_delay)
                        continue
                    break

                errors.append(f"{provider}: {' | '.join(provider_errors)}")

            return {
                "success": False,
                "error": "All providers failed: " + " | ".join(errors),
                "draft": ""
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Error: {str(e)}",
                "draft": ""
            }

    def _generate_with_azure(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Azure OpenAI（OpenAI-compatible）生成内容"""
        try:
            if not self.azure_api_key:
                return {
                    "success": False,
                    "error": "AZURE_OPENAI_API_KEY not set",
                    "draft": ""
                }
            if not self.azure_api_base:
                return {
                    "success": False,
                    "error": "AZURE_OPENAI_API_BASE not set",
                    "draft": ""
                }
            if not self.azure_deployment_name:
                return {
                    "success": False,
                    "error": "AZURE_OPENAI_DEPLOYMENT_NAME not set",
                    "draft": ""
                }

            base = self.azure_api_base.rstrip("/")
            if not base.endswith("/openai"):
                base = f"{base}/openai"
            url = f"{base}/deployments/{self.azure_deployment_name}/chat/completions"

            payload = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": max_tokens,
                "model": self.azure_model,
            }
            headers = {
                "api-key": self.azure_api_key,
                "Content-Type": "application/json",
            }
            params = {"api-version": self.azure_api_version}

            response = requests.post(url, params=params, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            data = response.json()

            choice = ((data.get("choices") or [{}])[0] or {})
            msg = choice.get("message") or {}
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
                content = "".join(parts)
            draft_text = str(content).strip()
            if not draft_text:
                return {
                    "success": False,
                    "error": "Azure OpenAI empty response",
                    "draft": ""
                }

            usage = data.get("usage") or {}
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.azure_model,
                    "provider": "azure",
                    "deployment": self.azure_deployment_name,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            }
        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            retry_after = self._parse_retry_after_seconds(
                getattr(getattr(e, "response", None), "headers", {}).get("Retry-After", "")
            )
            error_message = f"Azure OpenAI API Error: {str(e)}"
            if status_code is not None:
                error_message = f"Azure OpenAI HTTP {status_code}: {str(e)}"
            return {
                "success": False,
                "error": error_message,
                "draft": "",
                "status_code": status_code,
                "retry_after": retry_after,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Azure OpenAI API Error: {str(e)}",
                "draft": ""
            }
    
    def _generate_with_gemini(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Google Gemini 生成内容"""
        try:
            if not GEMINI_AVAILABLE:
                return {
                    "success": False,
                    "error": "google-genai not installed",
                    "draft": ""
                }
            if not self.api_key:
                return {
                    "success": False,
                    "error": "GOOGLE_API_KEY not set",
                    "draft": ""
                }
            if self.client is None:
                self.client = genai.Client(api_key=self.api_key)

            # Gemini 的 max_output_tokens 限制会导致输出过短
            # 如果 max_tokens < 4000，不设置限制让模型自由发挥
            # 如果 max_tokens >= 4000，则设置限制
            config_params = {
                "temperature": 0.7,
            }
            
            if max_tokens >= 4000:
                config_params["max_output_tokens"] = max_tokens
            
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_params)
            )
            
            draft_text = response.text
            
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.model,
                    "actual_model": self.gemini_model,
                    "provider": "gemini",
                    "prompt_tokens": response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
                    "output_tokens": response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
                    "finish_reason": str(response.candidates[0].finish_reason) if response.candidates else "UNKNOWN",
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Gemini API Error: {str(e)}",
                "draft": ""
            }

    def _generate_with_openrouter(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 OpenRouter（OpenAI-compatible）生成内容"""
        try:
            if not self.openrouter_api_key:
                return {
                    "success": False,
                    "error": "OPENROUTER_API_KEY not set",
                    "draft": ""
                }

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
                parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
                content = "".join(parts)
            draft_text = str(content).strip()
            if not draft_text:
                return {
                    "success": False,
                    "error": "OpenRouter empty response",
                    "draft": ""
                }

            usage = data.get("usage") or {}
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.openrouter_model,
                    "provider": "openrouter",
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            }
        except requests.RequestException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            retry_after = self._parse_retry_after_seconds(
                getattr(getattr(e, "response", None), "headers", {}).get("Retry-After", "")
            )
            error_message = f"OpenRouter API Error: {str(e)}"
            if status_code is not None:
                error_message = f"OpenRouter HTTP {status_code}: {str(e)}"
            return {
                "success": False,
                "error": error_message,
                "draft": "",
                "status_code": status_code,
                "retry_after": retry_after,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"OpenRouter API Error: {str(e)}",
                "draft": ""
            }
    
    def _generate_with_claude(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Anthropic Claude 生成内容"""
        try:
            if self.claude_client is None:
                return {
                    "success": False,
                    "error": "ANTHROPIC_API_KEY not set or anthropic sdk not installed",
                    "draft": ""
                }
            message = self.claude_client.messages.create(
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
    
    def _generate_with_ollama(self, prompt: str, max_tokens: int) -> Dict[str, Any]:
        """使用 Ollama 本地生成内容"""
        try:
            if _is_render_runtime() and _is_local_ollama_url(self.ollama_url):
                return {
                    "success": False,
                    "error": "Render 环境 OLLAMA_URL 不能是 localhost/127.0.0.1",
                    "draft": ""
                }
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
                "User-Agent": "FlowerNet-Generator/1.0"
            }
            response = None
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
                    if attempt >= self.ollama_retries:
                        raise
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
                return {
                    "success": False,
                    "error": "Ollama 返回空响应",
                    "draft": ""
                }

            result = response.json()
            draft_text = result.get('response', '')
            
            return {
                "success": True,
                "draft": draft_text,
                "metadata": {
                    "model": self.ollama_model,
                    "provider": "ollama",
                    "prompt_tokens": result.get('prompt_eval_count', 0),
                    "output_tokens": result.get('eval_count', 0),
                    "total_duration_ms": result.get('total_duration', 0) / 1000000,  # 纳秒转毫秒
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Ollama API Error: {str(e)}",
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
    集成 History Database 自动存储验证通过的内容
    """
    
    def __init__(
        self,
        generator_url: str = "http://localhost:8002",
        verifier_url: str = "http://localhost:8000",
        controller_url: str = "http://localhost:8001",
        max_iterations: int = 5,
        history_manager = None
    ):
        """
        初始化编排器
        
        Args:
            generator_url: Generator 服务的 URL
            verifier_url: Verifier 服务的 URL
            controller_url: Controller 服务的 URL
            max_iterations: 最大迭代次数
            history_manager: HistoryManager 实例（用于自动存储验证通过的内容）
        """
        self.generator_url = generator_url
        self.verifier_url = verifier_url
        self.controller_url = controller_url
        self.max_iterations = max_iterations
        self.history_manager = history_manager
        self.session = requests.Session()
        self.generator_retries = int(os.getenv('ORCH_GENERATOR_RETRIES', '4'))
        self.generator_backoff = float(os.getenv('ORCH_GENERATOR_BACKOFF', '2.0'))
        self.generator_max_backoff = float(os.getenv('ORCH_GENERATOR_MAX_BACKOFF', '60.0'))
        
        print(f"🌸 FlowerNet 编排器初始化:")
        print(f"  - Generator URL: {generator_url}")
        print(f"  - Verifier URL: {verifier_url}")
        print(f"  - Controller URL: {controller_url}")
        print(f"  - Max iterations: {max_iterations}")
        print(f"  - History Manager: {'✅ 已启用' if history_manager else '❌ 未启用'}")

    def _compute_effective_thresholds(self, iteration: int, rel_threshold: float, red_threshold: float) -> Tuple[float, float]:
        """前3轮严格校验；从第4轮起每轮放宽 0.02，最多放宽 0.10，减少长时间卡关。"""
        relax_steps = max(0, iteration - 3)
        effective_rel = max(rel_threshold - min(0.10, 0.02 * relax_steps), rel_threshold - 0.10)
        effective_red = min(red_threshold + min(0.10, 0.02 * relax_steps), red_threshold + 0.10)
        return round(effective_rel, 4), round(effective_red, 4)

    def generate_section(
        self,
        outline: str,
        initial_prompt: str,
        document_id: str = None,
        section_id: str = None,
        subsection_id: str = None,
        history: Optional[List[str]] = None,
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40
    ) -> Dict[str, Any]:
        """
        生成一个subsection，并进行验证-修改的循环
        验证通过后自动存入 History Database
        
        流程：
        1. Generator 根据 prompt 生成 draft
        2. Verifier 检验 draft（相关性和冗余度）
        3. 如果验证不通过，Controller 修改 prompt
        4. 回到步骤1，直到验证通过或达到最大迭代次数
        5. 验证通过后自动存入数据库（如果提供了history_manager）
        
        Args:
            outline: 段落大纲（subsection主题）
            initial_prompt: 初始生成提示
            document_id: 文档ID（用于数据库存储）
            section_id: Section ID（如 "section_1"）
            subsection_id: Subsection ID（如 "subsection_1_1"）
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
            effective_rel_threshold, effective_red_threshold = self._compute_effective_thresholds(
                iteration=iterations,
                rel_threshold=rel_threshold,
                red_threshold=red_threshold,
            )
            verify_response = self._call_verifier(
                draft=draft,
                outline=outline,
                history=history,
                rel_threshold=effective_rel_threshold,
                red_threshold=effective_red_threshold
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
            
            print(f"📊 相关性: {rel_score:.4f} (阈值: {effective_rel_threshold})")
            print(f"📊 冗余度: {red_score:.4f} (阈值: {effective_red_threshold})")
            print(f"💬 反馈: {feedback}")
            
            # 3️⃣ 如果验证通过，存入数据库并返回结果
            if is_passed:
                print(f"\n✨ 内容验证通过！")
                
                # 自动存入 History Database（如果提供了history_manager和必要的ID）
                if self.history_manager and document_id and section_id and subsection_id:
                    try:
                        self.history_manager.add_entry(
                            document_id=document_id,
                            section_id=section_id,
                            subsection_id=subsection_id,
                            content=draft,
                            metadata={
                                "relevancy_index": rel_score,
                                "redundancy_index": red_score,
                                "iterations": iterations,
                                "outline": outline
                            }
                        )
                        print(f"💾 已存入数据库: {document_id}/{section_id}/{subsection_id}")
                    except Exception as e:
                        print(f"⚠️  存储到数据库失败: {e}")
                else:
                    print(f"⚠️  未存入数据库（缺少 history_manager 或 ID 信息）")
                
                # 更新内存历史记录
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
                    "all_drafts": all_drafts,
                    "stored_in_db": bool(self.history_manager and document_id)
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
        """
        调用 Generator 进行内容生成
        注意：这里假设编排器在同一进程中有 generator 对象
        如果 generator_url 是本地的，直接使用本地 generator
        """
        def is_transient_error(text: str) -> bool:
            lowered = (text or "").lower()
            return any(token in lowered for token in ["429", "rate", "timeout", "temporarily", "503", "502", "504", "connection"])

        # 如果有本地 generator 对象，直接使用它（避免 HTTP 循环）
        if hasattr(self, '_local_generator') and self._local_generator:
            for attempt in range(1, self.generator_retries + 1):
                result = self._local_generator.generate_draft(prompt)
                if result.get("success"):
                    return result
                error_message = str(result.get("error", "unknown error"))
                if attempt < self.generator_retries and is_transient_error(error_message):
                    retry_delay = min(self.generator_backoff * (2 ** (attempt - 1)), self.generator_max_backoff)
                    time.sleep(retry_delay)
                    continue
                return result
        
        # 否则使用 HTTP 调用
        last_error = ""
        for attempt in range(1, self.generator_retries + 1):
            try:
                print(f"🔗 [Orchestrator] 调用 Generator API: {self.generator_url}/generate (attempt {attempt}/{self.generator_retries})")
                response = self.session.post(
                    f"{self.generator_url}/generate",
                    json={"prompt": prompt},
                    timeout=120
                )
                print(f"   响应状态: {response.status_code}")
                print(f"   响应长度: {len(response.text)}")

                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if attempt < self.generator_retries and is_transient_error(last_error):
                        retry_delay = min(self.generator_backoff * (2 ** (attempt - 1)), self.generator_max_backoff)
                        time.sleep(retry_delay)
                        continue
                    print(f"   ❌ 非 200 状态码!")
                    return {
                        "success": False,
                        "error": last_error
                    }

                result = response.json()
                print(f"   ✅ 获得有效响应")
                return result
            except Exception as e:
                last_error = str(e)
                print(f"   ❌ 异常: {type(e).__name__}: {last_error}")
                if attempt < self.generator_retries and is_transient_error(last_error):
                    retry_delay = min(self.generator_backoff * (2 ** (attempt - 1)), self.generator_max_backoff)
                    time.sleep(retry_delay)
                    continue
                return {
                    "success": False,
                    "error": last_error
                }

        return {
            "success": False,
            "error": last_error or "Generator 调用失败"
        }

    def _call_verifier(
        self,
        draft: str,
        outline: str,
        history: List[str],
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40
    ) -> Dict[str, Any]:
        """调用 Verifier API，内部最多重试3次应对 Render 冷启动。"""
        last_error = "unknown"
        for attempt in range(1, 4):
            try:
                print(f"🔗 [Orchestrator] 调用 Verifier API: {self.verifier_url}/verify (第{attempt}次)")
                payload = {
                    "draft": draft,
                    "outline": outline,
                    "history": history,
                    "rel_threshold": rel_threshold,
                    "red_threshold": red_threshold
                }
                cmd = [
                    "curl", "-s", "-X", "POST", f"{self.verifier_url}/verify",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps(payload, ensure_ascii=False),
                    "--max-time", "90"
                ]
                completed = subprocess.run(cmd, capture_output=True, text=True, timeout=95)
                if completed.returncode != 0:
                    last_error = f"Verifier curl 执行失败: {completed.stderr.strip()[:80]}"
                elif not completed.stdout.strip():
                    last_error = "Verifier 返回空响应"
                else:
                    data = json.loads(completed.stdout)
                    print(f"   响应长度: {len(completed.stdout)}")
                    return {"success": True, **data}
            except Exception as e:
                last_error = f"{type(e).__name__}: {str(e)[:80]}"
            if attempt < 3:
                print(f"   ⚠️ Verifier 第{attempt}次调用失败 ({last_error})，5s 后重试...")
                import time as _t; _t.sleep(5)
        print(f"   ❌ Verifier 全部重试失败: {last_error}")
        return {"success": False, "error": last_error}

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
            print(f"🔗 [Orchestrator] 调用 Controller API: {self.controller_url}/refine_prompt")
            payload = {
                "old_prompt": old_prompt,
                "failed_draft": failed_draft,
                "feedback": feedback,
                "outline": outline,
                "history": history
            }
            cmd = [
                "curl", "-s", "-X", "POST", f"{self.controller_url}/refine_prompt",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(payload, ensure_ascii=False)
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if completed.returncode != 0:
                return {
                    "success": False,
                    "error": f"Controller curl 执行失败: {completed.stderr.strip()}"
                }

            if not completed.stdout.strip():
                return {
                    "success": False,
                    "error": "Controller 返回空响应"
                }

            print(f"   响应长度: {len(completed.stdout)}")
            return json.loads(completed.stdout)
        except Exception as e:
            print(f"   ❌ Controller 异常: {type(e).__name__}: {str(e)[:100]}")
            return {
                "success": False,
                "error": str(e)
            }

    def generate_document(
        self,
        document_id: str,
        title: str,
        outline_list: List[Dict[str, Any]],
        system_prompt: str = "",
        rel_threshold: float = 0.80,
        red_threshold: float = 0.40
    ) -> Dict[str, Any]:
        """
        生成完整文档（多个sections，每个section包含多个subsections）
        文档完成后自动清空 History Database
        
        Args:
            document_id: 文档唯一标识（用于数据库管理）
            title: 文档标题
            outline_list: 大纲列表，格式:
                [
                    {
                        "section_id": "section_1",
                        "section_title": "第一章",
                        "subsections": [
                            {"subsection_id": "subsection_1_1", "title": "...", "outline": "..."},
                            {"subsection_id": "subsection_1_2", "title": "...", "outline": "..."}
                        ]
                    },
                    ...
                ]
            system_prompt: 系统级提示（对所有段落适用）
            rel_threshold: 相关性阈值
            red_threshold: 冗余度阈值
            
        Returns:
            包含完整文档和生成过程的字典
        """
        print(f"\n{'#'*60}")
        print(f"📄 开始生成文档: {title}")
        print(f"📄 文档ID: {document_id}")
        print(f"{'#'*60}")
        
        document = {
            "document_id": document_id,
            "title": title,
            "sections": [],
            "total_iterations": 0,
            "total_subsections": 0,
            "success_count": 0,
            "failed_subsections": []
        }
        
        history = []  # 内存历史记录（用于验证冗余度）
        
        # 遍历所有 sections
        for section_idx, section_data in enumerate(outline_list, 1):
            section_id = section_data.get("section_id", f"section_{section_idx}")
            section_title = section_data.get("section_title", f"Section {section_idx}")
            subsections = section_data.get("subsections", [])
            
            print(f"\n{'='*60}")
            print(f"📖 Section {section_idx}/{len(outline_list)}: {section_title}")
            print(f"📖 Section ID: {section_id}")
            print(f"📖 包含 {len(subsections)} 个 subsections")
            print(f"{'='*60}")
            
            section_result = {
                "section_id": section_id,
                "section_title": section_title,
                "subsections": [],
                "success_count": 0,
                "failed_count": 0
            }
            
            # 遍历该 section 下的所有 subsections
            for subsection_idx, subsection_data in enumerate(subsections, 1):
                subsection_id = subsection_data.get("subsection_id", f"subsection_{section_idx}_{subsection_idx}")
                subsection_title = subsection_data.get("title", f"Subsection {subsection_idx}")
                outline = subsection_data.get("outline", subsection_title)
                
                document["total_subsections"] += 1
                
                print(f"\n  [{section_idx}.{subsection_idx}] 生成 subsection: {subsection_title}")
                print(f"  Subsection ID: {subsection_id}")
                
                # 为该 subsection 生成初始 prompt
                initial_prompt = self._generate_initial_prompt(
                    system_prompt=system_prompt,
                    outline=outline,
                    section_number=subsection_idx,
                    total_sections=len(subsections)
                )
                
                # 调用生成-验证循环（带数据库存储）
                result = self.generate_section(
                    outline=outline,
                    initial_prompt=initial_prompt,
                    document_id=document_id,
                    section_id=section_id,
                    subsection_id=subsection_id,
                    history=history,
                    rel_threshold=rel_threshold,
                    red_threshold=red_threshold
                )
                
                document["total_iterations"] += result.get("iterations", 0)
                
                if result.get("success"):
                    section_result["subsections"].append({
                        "subsection_id": subsection_id,
                        "subsection_title": subsection_title,
                        "outline": outline,
                        "content": result.get("draft", ""),
                        "iterations": result.get("iterations", 0),
                        "verification": result.get("verification", {}),
                        "stored_in_db": result.get("stored_in_db", False)
                    })
                    section_result["success_count"] += 1
                    document["success_count"] += 1
                    history.append(result.get("draft", ""))
                else:
                    section_result["failed_count"] += 1
                    document["failed_subsections"].append({
                        "section_id": section_id,
                        "subsection_id": subsection_id,
                        "subsection_title": subsection_title,
                        "outline": outline,
                        "error": result.get("error", "Unknown error")
                    })
            
            # 将该 section 添加到文档中
            document["sections"].append(section_result)
        
        # 生成最终报告
        print(f"\n{'#'*60}")
        print(f"📊 文档生成完成")
        print(f"{'#'*60}")
        print(f"✅ 成功 subsections: {document['success_count']}/{document['total_subsections']}")
        print(f"❌ 失败 subsections: {len(document['failed_subsections'])}/{document['total_subsections']}")
        print(f"📖 总 sections: {len(document['sections'])}")
        print(f"🔄 总迭代次数: {document['total_iterations']}")
        
        # 文档完成后清空 History Database
        if self.history_manager:
            try:
                self.history_manager.clear_history(document_id)
                print(f"🗑️  已清空文档 {document_id} 的历史记录")
                document["history_cleared"] = True
            except Exception as e:
                print(f"⚠️  清空历史记录失败: {e}")
                document["history_cleared"] = False
        else:
            document["history_cleared"] = False
        
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
