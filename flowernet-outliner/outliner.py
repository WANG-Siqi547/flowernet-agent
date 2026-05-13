"""
FlowerNet Outliner - 文档大纲生成与内容提示词管理
根据用户需求生成文档结构，并为每个段落生成专用的 Content Prompt
支持多种 LLM: SenseNova, Azure OpenAI, Ollama (本地), Google Gemini, OpenRouter
"""

import os
import json
import time
import random
import ast
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

    DEEPSEEK_SYSTEM_PREFIX = (
        "You are FlowerNet Outliner, a strict academic document-structure planner. "
        "When JSON is requested, output only a valid JSON object matching the requested schema. "
        "Use professional section and subsection titles, never copy the user's raw instruction as a title, "
        "and keep the outline topic-specific. This stable prefix is intentionally reused across FlowerNet "
        "outline calls to improve DeepSeek context-cache hits."
    )
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini", provider: str = "sensenova"):
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
        requested_provider = (
            os.getenv("OUTLINER_PROVIDER_CHAIN", "").strip()
            or provider
            or os.getenv("OUTLINER_PROVIDER", "sensenova")
        )
        parsed_chain = [p.strip().lower() for p in requested_provider.split(",") if p.strip()]
        allowed_providers = {"azure", "gemini", "dashscope", "sensenova", "deepseek", "openrouter", "ollama"}
        self.provider_chain = [p for p in parsed_chain if p in allowed_providers] or ["deepseek", "sensenova"]

        self.model = model
        self.azure_model = os.getenv("OUTLINER_AZURE_MODEL", os.getenv("AZURE_OPENAI_MODEL", model or "gpt-4o-mini"))
        self.azure_api_key = os.getenv("OUTLINER_AZURE_API_KEY", os.getenv("AZURE_OPENAI_API_KEY", "")).strip()
        self.azure_api_base = os.getenv("OUTLINER_AZURE_API_BASE", os.getenv("AZURE_OPENAI_API_BASE", "")).strip()
        self.azure_api_version = os.getenv("OUTLINER_AZURE_API_VERSION", os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")).strip()
        self.azure_deployment_name = os.getenv("OUTLINER_AZURE_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")).strip()
        self.gemini_model = os.getenv("OUTLINER_GEMINI_MODEL", "models/gemini-2.5-flash-lite")
        self.dashscope_model = os.getenv("OUTLINER_DASHSCOPE_MODEL", os.getenv("DASHSCOPE_MODEL", "glm-5"))
        self.dashscope_api_key = os.getenv("OUTLINER_DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")).strip()
        self.dashscope_api_url = os.getenv(
            "OUTLINER_DASHSCOPE_API_URL",
            os.getenv("DASHSCOPE_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        ).rstrip("/")
        self.sensenova_model = os.getenv("OUTLINER_SENSENOVA_MODEL", os.getenv("SENSENOVA_MODEL", "SenseNova-V6-5-Turbo"))
        self.sensenova_api_key = os.getenv("OUTLINER_SENSENOVA_API_KEY", os.getenv("SENSENOVA_API_KEY", "")).strip()
        self.sensenova_api_url = os.getenv(
            "OUTLINER_SENSENOVA_API_URL",
            os.getenv("SENSENOVA_API_URL", "https://api.sensenova.cn/v1/llm/chat-completions")
        ).rstrip("/")
        self.deepseek_model = os.getenv("OUTLINER_DEEPSEEK_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
        self.deepseek_api_key = os.getenv("OUTLINER_DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.deepseek_base_url = os.getenv(
            "OUTLINER_DEEPSEEK_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.deepseek_api_url = os.getenv(
            "OUTLINER_DEEPSEEK_API_URL",
            os.getenv("DEEPSEEK_API_URL", f"{self.deepseek_base_url}/chat/completions")
        ).rstrip("/")
        self.deepseek_anthropic_base_url = os.getenv(
            "OUTLINER_DEEPSEEK_ANTHROPIC_BASE_URL",
            os.getenv("DEEPSEEK_ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
        ).rstrip("/")
        self.deepseek_thinking_enabled = os.getenv("OUTLINER_DEEPSEEK_THINKING_ENABLED", os.getenv("DEEPSEEK_THINKING_ENABLED", "false")).lower() == "true"
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
        self.provider_retries = max(1, int(os.getenv('OUTLINER_PROVIDER_RETRIES', os.getenv('PROVIDER_RETRIES', '2'))))
        self.provider_backoff = float(os.getenv('PROVIDER_BACKOFF', '2.0'))
        self.provider_max_backoff = float(os.getenv('PROVIDER_MAX_BACKOFF', '90.0'))
        self.provider_jitter = float(os.getenv('PROVIDER_JITTER', '0.35'))
        self.provider_min_interval = float(os.getenv('PROVIDER_MIN_INTERVAL', '1.2'))
        self.provider_failure_threshold = max(1, int(os.getenv('PROVIDER_FAILURE_THRESHOLD', '2')))
        self.provider_cooldown_seconds = max(5.0, float(os.getenv('PROVIDER_COOLDOWN_SECONDS', '30')))
        self.provider_http_timeout = float(os.getenv('PROVIDER_HTTP_TIMEOUT', '30'))
        self.sensenova_connect_timeout = float(os.getenv('SENSENOVA_CONNECT_TIMEOUT', '8'))
        self.sensenova_read_timeout = float(os.getenv('SENSENOVA_READ_TIMEOUT', str(self.provider_http_timeout)))
        self.sensenova_transport_retries = max(1, int(os.getenv('OUTLINER_SENSENOVA_TRANSPORT_RETRIES', '2')))
        self.sensenova_transport_backoff = max(0.2, float(os.getenv('OUTLINER_SENSENOVA_TRANSPORT_BACKOFF', '1.2')))
        self.structure_max_tokens = max(600, int(os.getenv('OUTLINER_STRUCTURE_MAX_TOKENS', '1800')))
        self.detailed_max_tokens = max(800, int(os.getenv('OUTLINER_DETAILED_MAX_TOKENS', '2200')))
        self.azure_http_timeout = float(os.getenv('AZURE_HTTP_TIMEOUT', str(self.provider_http_timeout)))
        self.dashscope_http_timeout = float(os.getenv('DASHSCOPE_HTTP_TIMEOUT', str(self.provider_http_timeout)))
        self.openrouter_http_timeout = float(os.getenv('OPENROUTER_HTTP_TIMEOUT', str(self.provider_http_timeout)))
        self.deepseek_http_timeout = float(os.getenv('DEEPSEEK_HTTP_TIMEOUT', str(self.provider_http_timeout)))
        self.http_session = requests.Session()
        self.http_session.trust_env = False
        self._provider_next_allowed: Dict[str, float] = {}
        self._provider_failure_streak: Dict[str, int] = {}
        self._provider_cooldown_until: Dict[str, float] = {}

        self.api_key = api_key or os.getenv('GOOGLE_API_KEY', '')
        self.client = genai.Client(api_key=self.api_key) if (self.api_key and GEMINI_AVAILABLE) else None
        self.last_provider_used = ""

        print("✅ Outliner 初始化成功:")
        print(f"  - Provider chain: {self.provider_chain}")
        print(f"  - Azure model: {self.azure_model}")
        print(f"  - Azure deployment: {self.azure_deployment_name}")
        print(f"  - Gemini model: {self.gemini_model}")
        print(f"  - DashScope model: {self.dashscope_model}")
        print(f"  - SenseNova model: {self.sensenova_model}")
        print(f"  - DeepSeek model: {self.deepseek_model}")
        print(f"  - OpenRouter model: {self.openrouter_model}")
        print(f"  - Ollama model: {self.ollama_model}")
        print(f"  - Ollama URL: {self.ollama_url}")

    @staticmethod
    def _sanitize_json_text(raw_text: str) -> str:
        text = (raw_text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        text = re.sub(r"[\x00-\x1F]", "", text)
        return text.strip()

    def _safe_json_loads(self, raw_text: str) -> Dict[str, Any]:
        sanitized = self._sanitize_json_text(raw_text)
        try:
            return json.loads(sanitized)
        except json.JSONDecodeError:
            pass

        try:
            compact = re.sub(r"\s+", " ", sanitized)
            return json.loads(compact)
        except json.JSONDecodeError:
            pass

        try:
            py_like = re.sub(r"\btrue\b", "True", sanitized, flags=re.IGNORECASE)
            py_like = re.sub(r"\bfalse\b", "False", py_like, flags=re.IGNORECASE)
            py_like = re.sub(r"\bnull\b", "None", py_like, flags=re.IGNORECASE)
            parsed = ast.literal_eval(py_like)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        raise json.JSONDecodeError("Unable to parse model JSON output", sanitized, 0)

    @staticmethod
    def _is_bad_outline_title(value: Any) -> bool:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return True
        lowered = text.lower()
        if re.fullmatch(r"第\s*\d+\s*[章节节]", text):
            return True
        if re.fullmatch(r"(section|chapter|subsection)\s*\d*", text, flags=re.I):
            return True
        request_artifacts = [
            "请帮我", "生成一篇", "高质量长文档", "用户背景", "用户需求", "额外要求",
            "document topic", "user background", "extra requirements",
        ]
        if any(token in lowered for token in request_artifacts):
            return True
        if len(text) > 64 and re.search(r"[。！？.!?，,；;]", text):
            return True
        return False

    def _outline_quality_issues(self, structure: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        sections = structure.get("sections", []) if isinstance(structure, dict) else []
        if not isinstance(sections, list) or not sections:
            return ["missing_sections"]
        for section in sections:
            if not isinstance(section, dict):
                issues.append("invalid_section")
                continue
            section_title = str(section.get("title") or section.get("name") or "").strip()
            if self._is_bad_outline_title(section_title):
                issues.append(f"bad_section_title={section_title[:80]}")
            subsections = section.get("subsections", [])
            if not isinstance(subsections, list) or not subsections:
                issues.append(f"section_without_subsections={section_title[:40]}")
                continue
            for subsection in subsections:
                if not isinstance(subsection, dict):
                    issues.append(f"invalid_subsection={section_title[:40]}")
                    continue
                subsection_title = str(subsection.get("title") or subsection.get("name") or "").strip()
                if self._is_bad_outline_title(subsection_title):
                    issues.append(f"bad_subsection_title={subsection_title[:80]}")
        return issues

    def _repair_outline_structure(
        self,
        structure: Dict[str, Any],
        user_background: str,
        user_requirements: str,
        max_sections: int,
        max_subsections_per_section: int,
        reason: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        repair_prompt = f"""
你是严谨的学术大纲编辑器。下面的大纲质量不合格，原因是：{reason}

请基于用户背景与用户需求，重新输出一个合格的 JSON 大纲。

硬性要求：
1. 必须恰好 {max_sections} 个 sections，每个 section 恰好 {max_subsections_per_section} 个 subsections。
2. title/section title/subsection title 必须是专业、简洁、主题相关的学术标题。
3. 禁止把“请帮我生成...”“高质量长文档”“用户背景/用户需求/额外要求”等请求文本复制成标题。
4. 不要输出模板占位标题，如“第1章”“Section 1”“Subsection”。
5. 不要输出解释，不要代码块，只输出 JSON。

用户背景：
{user_background}

用户需求：
{user_requirements}

原始不合格大纲：
{json.dumps(structure, ensure_ascii=False, indent=2)[:5000]}

输出格式：
{{
  "title": "文档标题",
  "sections": [
    {{
      "id": "section_1",
      "title": "章节标题",
      "description": "章节定位",
      "subsections": [
        {{
          "id": "subsection_1_1",
          "title": "小节标题",
          "description": "小节说明"
        }}
      ]
    }}
  ]
}}
"""
        repaired, metadata, _ = self._generate_json_with_repair(
            repair_prompt,
            max_tokens=self.structure_max_tokens,
            stage_name="outline_quality_repair",
        )
        return repaired, metadata

    @staticmethod
    def _extract_requirement_field(user_requirements: str, labels: List[str], default: str = "") -> str:
        text = str(user_requirements or "")
        for label in labels:
            pattern = rf"{re.escape(label)}\s*[:：]\s*(.+)"
            matched = re.search(pattern, text)
            if matched:
                value = matched.group(1).strip()
                if value:
                    return value[:120]
        first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
        return (first_line or default).strip()[:120]

    def _generate_compact_document_structure(
        self,
        user_background: str,
        user_requirements: str,
        max_sections: int,
        max_subsections_per_section: int,
        reason: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        topic = self._extract_requirement_field(user_requirements, ["文档主题", "主题", "Document topic"], "文档主题")
        audience = self._extract_requirement_field(user_requirements, ["目标读者/用户背景", "用户背景", "User background"], user_background)
        compact_prompt = f"""
只输出一个合法 JSON 对象，为专业长文档生成大纲。

主题：{topic}
目标读者：{audience or user_background}
补充需求：{user_requirements[:600]}
上一次失败原因：{reason[:240]}

硬性要求：
- title 是主题相关的专业文档标题。
- sections 必须恰好 {max_sections} 个。
- 每个 section 的 subsections 必须恰好 {max_subsections_per_section} 个。
- 标题必须是专业学术标题，禁止复制用户请求句，禁止“第1章/第1节/Section/Subsection”等占位标题。
- description 用 1 句说明该节写什么。

JSON 结构：
{{
  "title": "文档标题",
  "sections": [
    {{
      "id": "section_1",
      "title": "章节标题",
      "description": "章节说明",
      "subsections": [
        {{
          "id": "subsection_1_1",
          "title": "小节标题",
          "description": "小节说明"
        }}
      ]
    }}
  ]
}}
"""
        compact_tokens = max(
            900,
            min(self.structure_max_tokens, 900 + max_sections * max_subsections_per_section * 160),
        )
        return self._generate_json_with_repair(
            compact_prompt,
            max_tokens=compact_tokens,
            stage_name="document_structure_compact",
        )
    
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
6. 标题必须是专业学术标题；禁止把用户请求句、写作要求、用户背景、"请帮我生成..."、"高质量长文档" 复制成章节或小节标题
7. 禁止使用“第1章”“第1节”“Section 1”“Subsection”等占位标题

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
            try:
                structure, llm_metadata, structure_text = self._generate_json_with_repair(
                    structure_prompt,
                    max_tokens=self.structure_max_tokens,
                    stage_name="document_structure",
                )
            except Exception as exc:
                print(f"⚠️ 标准大纲生成失败，尝试短提示 LLM 大纲: {exc}")
                structure, llm_metadata, structure_text = self._generate_compact_document_structure(
                    user_background=user_background,
                    user_requirements=user_requirements,
                    max_sections=max_sections,
                    max_subsections_per_section=max_subsections_per_section,
                    reason=str(exc),
                )

            issues = self._outline_quality_issues(structure)
            if issues:
                print(f"⚠️ 大纲标题质量异常，尝试LLM修复: {issues[:4]}")
                structure, repair_metadata = self._repair_outline_structure(
                    structure=structure,
                    user_background=user_background,
                    user_requirements=user_requirements,
                    max_sections=max_sections,
                    max_subsections_per_section=max_subsections_per_section,
                    reason="; ".join(issues[:6]),
                )
                llm_metadata = {**llm_metadata, "repair": repair_metadata}
                issues = self._outline_quality_issues(structure)
                if issues:
                    return {
                        "success": False,
                        "error": "outline_quality_failed_after_repair",
                        "issues": issues[:8],
                        "metadata": {
                            "provider_chain": self.provider_chain,
                            "active_provider": llm_metadata.get("provider", self.last_provider_used),
                            "model": llm_metadata.get("model", self.model),
                        },
                    }
            
            # 验证结构是否符合要求
            sections = structure.get('sections', [])
            actual_section_count = len(sections)
            
            # 检查章节数量
            if actual_section_count != max_sections:
                print(f"⚠️  警告: 大纲的章节数 ({actual_section_count}) 与要求不符 ({max_sections})")
                if actual_section_count > max_sections:
                    sections = sections[:max_sections]
                    actual_section_count = len(sections)
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
                    if actual_subsection_count > max_subsections_per_section:
                        subsections = subsections[:max_subsections_per_section]
                        actual_subsection_count = len(subsections)
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
            final_issues = self._outline_quality_issues(structure)
            if final_issues:
                return {
                    "success": False,
                    "error": "outline_quality_failed",
                    "issues": final_issues[:8],
                    "metadata": {
                        "provider_chain": self.provider_chain,
                        "active_provider": llm_metadata.get("provider", self.last_provider_used),
                        "model": llm_metadata.get("model", self.model),
                    },
                }
            
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
                "error": f"outline_json_parse_failed: {e}",
                "metadata": {
                    "provider_chain": self.provider_chain,
                    "active_provider": self.last_provider_used,
                    "model": self.model,
                }
            }
        except Exception as e:
            print(f"❌ 大纲生成失败: {e}")
            return {
                "success": False,
                "error": f"outline_generation_failed: {e}",
                "metadata": {
                    "provider_chain": self.provider_chain,
                    "active_provider": self.last_provider_used,
                    "model": self.model,
                }
            }

    @staticmethod
    def _is_transient_provider_error(message: str) -> bool:
        text = (message or "").lower()
        transient_tokens = [
            "429", "rate", "resource_exhausted", "quota", "too many requests",
            "timeout", "timed out", "temporarily", "500", "502", "503", "504", "408", "connection",
            "connection reset", "remote disconnected", "temporarily unavailable", "service unavailable",
            "read timed out", "ssl", "tls", "econnreset", "broken pipe", "network is unreachable",
            "name or service not known", "temporary failure in name resolution",
            "empty response", "empty content", "空响应", "空内容", "返回空",
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

    def _generate_text_with_fallback(self, prompt: str, max_tokens: int, expect_json: bool = False) -> Tuple[str, Dict[str, Any]]:
        errors: List[str] = []
        has_fallback_provider = len(self.provider_chain) > 1
        for provider in self.provider_chain:
            provider_errors: List[str] = []
            cooldown_until = self._provider_cooldown_until.get(provider, 0.0)
            if has_fallback_provider and cooldown_until > time.time():
                remain = max(0.0, cooldown_until - time.time())
                errors.append(f"{provider}: skipped (cooldown {remain:.1f}s)")
                continue

            attempt_limit = self.provider_retries if has_fallback_provider else max(3, self.provider_retries)
            for attempt in range(1, attempt_limit + 1):
                try:
                    self._wait_for_provider_slot(provider)
                    text, metadata = self._generate_text_with_provider(
                        provider=provider,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        expect_json=expect_json,
                    )
                    self._mark_provider_slot(provider)
                    self._provider_failure_streak[provider] = 0
                    self._provider_cooldown_until[provider] = 0.0
                    self.last_provider_used = provider
                    return text, metadata
                except Exception as exc:
                    error_message = str(exc)
                    provider_errors.append(error_message)
                    retry_after = self._extract_retry_after_from_message(error_message)

                    transient_error = self._is_transient_provider_error(error_message)
                    if transient_error:
                        streak = self._provider_failure_streak.get(provider, 0) + 1
                        self._provider_failure_streak[provider] = streak
                        if streak >= self.provider_failure_threshold:
                            self._provider_cooldown_until[provider] = time.time() + self.provider_cooldown_seconds
                    else:
                        self._provider_failure_streak[provider] = 0

                    should_retry = transient_error and attempt < attempt_limit
                    if should_retry:
                        retry_delay = self._compute_retry_delay(attempt, retry_after=retry_after)
                        self._mark_provider_slot(provider, extra_delay=retry_delay)
                        time.sleep(retry_delay)
                        continue
                    break

            errors.append(f"{provider}: {' | '.join(provider_errors)}")

        raise Exception("所有 LLM 提供商都失败: " + " | ".join(errors))

    def _generate_text_with_provider(self, provider: str, prompt: str, max_tokens: int, expect_json: bool = False) -> Tuple[str, Dict[str, Any]]:
        temperature = 0.1 if expect_json else 0.7
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
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if expect_json:
                payload["response_format"] = {"type": "json_object"}
            headers = {
                "api-key": self.azure_api_key,
                "Content-Type": "application/json",
            }
            params = {"api-version": self.azure_api_version}

            try:
                response = self.http_session.post(url, params=params, json=payload, headers=headers, timeout=self.azure_http_timeout)
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
                    temperature=temperature,
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

        if provider == "dashscope":
            if not self.dashscope_api_key:
                raise Exception("DASHSCOPE_API_KEY 未配置")
            try:
                payload = {
                    "model": self.dashscope_model,
                    "messages": [
                        {"role": "system", "content": "你是严格的JSON输出助手。仅输出合法JSON对象，不要输出任何解释、代码块或额外文本。"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if expect_json:
                    payload["response_format"] = {"type": "json_object"}
                headers = {
                    "Authorization": f"Bearer {self.dashscope_api_key}",
                    "Content-Type": "application/json",
                }
                response = self.http_session.post(self.dashscope_api_url, json=payload, headers=headers, timeout=self.dashscope_http_timeout)
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
                    raise Exception("DashScope 返回空响应")
                usage = data.get("usage") or {}
                return text, {
                    "provider": "dashscope",
                    "model": self.dashscope_model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                }
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "unknown")
                response_text = (getattr(getattr(exc, "response", None), "text", "") or "").strip()
                retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                retry_after = self._parse_retry_after_seconds(retry_after_raw)
                if retry_after is not None:
                    suffix = f" | response={response_text[:500]}" if response_text else ""
                    raise Exception(f"DashScope HTTP {status}, retry_after={retry_after}{suffix}")
                suffix = f" | response={response_text[:500]}" if response_text else ""
                raise Exception(f"DashScope HTTP {status}: {str(exc)}{suffix}")
            except requests.RequestException as exc:
                raise Exception(f"DashScope request error: {str(exc)}")

        if provider == "deepseek":
            if not self.deepseek_api_key:
                raise Exception("DEEPSEEK_API_KEY 未配置")
            try:
                payload = {
                    "model": self.deepseek_model,
                    "messages": [
                        {"role": "system", "content": self.DEEPSEEK_SYSTEM_PREFIX},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
                if expect_json:
                    payload["response_format"] = {"type": "json_object"}
                if self.deepseek_thinking_enabled:
                    payload["thinking"] = {"type": "enabled"}
                headers = {
                    "Authorization": f"Bearer {self.deepseek_api_key}",
                    "Content-Type": "application/json",
                }
                response = self.http_session.post(self.deepseek_api_url, json=payload, headers=headers, timeout=self.deepseek_http_timeout)
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
                    raise Exception("DeepSeek 返回空响应")
                usage = data.get("usage") or {}
                return text, {
                    "provider": "deepseek",
                    "model": self.deepseek_model,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0),
                    "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0),
                }
            except requests.HTTPError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "unknown")
                response_text = (getattr(getattr(exc, "response", None), "text", "") or "").strip()
                retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                retry_after = self._parse_retry_after_seconds(retry_after_raw)
                suffix = f" | response={response_text[:500]}" if response_text else ""
                if retry_after is not None:
                    raise Exception(f"DeepSeek HTTP {status}, retry_after={retry_after}{suffix}")
                raise Exception(f"DeepSeek HTTP {status}: {str(exc)}{suffix}")
            except requests.RequestException as exc:
                raise Exception(f"DeepSeek request error: {str(exc)}")

        if provider == "sensenova":
            if not self.sensenova_api_key:
                raise Exception("SENSENOVA_API_KEY 未配置")
            payload_variants = [
                {
                    "model": self.sensenova_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "stream": False,
                    "max_tokens": max_tokens,
                },
                {
                    "model": self.sensenova_model,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                    "temperature": temperature,
                    "stream": False,
                    "max_tokens": max_tokens,
                },
            ]
            headers = {
                "Authorization": f"Bearer {self.sensenova_api_key}",
                "Content-Type": "application/json",
            }
            last_error = "SenseNova unknown error"
            for payload in payload_variants:
                for transport_attempt in range(1, self.sensenova_transport_retries + 1):
                    try:
                        response = self.http_session.post(
                            self.sensenova_api_url,
                            json=payload,
                            headers=headers,
                            timeout=(self.sensenova_connect_timeout, self.sensenova_read_timeout),
                        )
                        response.raise_for_status()
                        data = response.json()
                        container = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
                        choice = ((container.get("choices") or [{}])[0] or {}) if isinstance(container, dict) else {}
                        msg = choice.get("message") if isinstance(choice, dict) else None
                        content = ""
                        if isinstance(msg, str):
                            content = msg
                        elif isinstance(msg, dict):
                            content = msg.get("content", "")
                        if isinstance(content, list):
                            parts = [str(it.get("text", "")) for it in content if isinstance(it, dict)]
                            content = "".join(parts)
                        text = str(content).strip()
                        if not text:
                            last_error = "SenseNova 返回空响应"
                            if transport_attempt < self.sensenova_transport_retries:
                                delay = min(self.sensenova_transport_backoff * transport_attempt, self.provider_max_backoff)
                                time.sleep(delay)
                                continue
                            break
                        usage = container.get("usage") if isinstance(container, dict) else {}
                        usage = usage or {}
                        return text, {
                            "provider": "sensenova",
                            "model": self.sensenova_model,
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                        }
                    except requests.HTTPError as exc:
                        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
                        response_text = (getattr(getattr(exc, "response", None), "text", "") or "").strip()
                        retry_after_raw = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                        retry_after = self._parse_retry_after_seconds(retry_after_raw)
                        suffix = f" | response={response_text[:500]}" if response_text else ""
                        last_error = f"SenseNova HTTP {status}: {str(exc)}{suffix}"

                        is_retryable_http = status in [408, 409, 425, 429, 500, 502, 503, 504]
                        should_retry_transport = is_retryable_http and transport_attempt < self.sensenova_transport_retries
                        if should_retry_transport:
                            delay = self.sensenova_transport_backoff * transport_attempt
                            if retry_after is not None:
                                delay = max(delay, min(float(retry_after), self.provider_max_backoff))
                            time.sleep(min(delay, self.provider_max_backoff))
                            continue

                        # 对 4xx 的参数兼容失败，切换 payload 变体尝试
                        if isinstance(status, int) and status < 500:
                            break
                    except requests.RequestException as exc:
                        last_error = f"SenseNova request error: {str(exc)}"
                        should_retry_transport = transport_attempt < self.sensenova_transport_retries
                        if should_retry_transport:
                            delay = min(self.sensenova_transport_backoff * transport_attempt, self.provider_max_backoff)
                            time.sleep(delay)
                            continue
                    except Exception as exc:
                        last_error = f"SenseNova API Error: {str(exc)}"
                        if self._is_transient_provider_error(last_error) and transport_attempt < self.sensenova_transport_retries:
                            delay = min(self.sensenova_transport_backoff * transport_attempt, self.provider_max_backoff)
                            time.sleep(delay)
                            continue
                        break

            raise Exception(last_error)

        if provider == "openrouter":
            if not self.openrouter_api_key:
                raise Exception("OPENROUTER_API_KEY 未配置")
            try:
                payload = {
                    "model": self.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if expect_json:
                    payload["response_format"] = {"type": "json_object"}
                headers = {
                    "Authorization": f"Bearer {self.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self.openrouter_referrer,
                    "X-Title": self.openrouter_app_name,
                }
                response = self.http_session.post(self.openrouter_api_url, json=payload, headers=headers, timeout=self.openrouter_http_timeout)
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

    def _generate_json_with_repair(self, prompt: str, max_tokens: int, stage_name: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        retries = max(1, int(os.getenv("OUTLINER_JSON_RETRIES", "3")))
        strict_prompt = (
            f"{prompt}\n\n"
            "【严格输出要求】\n"
            "- 仅输出一个JSON对象\n"
            "- 不要输出```代码块\n"
            "- 不要输出任何解释文字\n"
            "- 必须是可被 json.loads 直接解析的标准JSON\n"
        )

        last_error = ""
        last_text = ""
        last_metadata: Dict[str, Any] = {}

        for attempt in range(1, retries + 1):
            text, metadata = self._generate_text_with_fallback(strict_prompt, max_tokens=max_tokens, expect_json=True)
            last_text = text
            last_metadata = metadata
            try:
                return self._safe_json_loads(text), metadata, text
            except json.JSONDecodeError as exc:
                last_error = str(exc)
                if attempt >= retries:
                    break

                repair_prompt = f"""
你将收到一段“本应为JSON但不合法”的文本，请修复成合法JSON。

要求：
1. 仅输出修复后的 JSON 对象
2. 保持字段语义不变，缺失字段可在不改变意图前提下补齐
3. 不要输出任何解释文字

待修复文本：
{text}
"""
                repaired_text, repaired_meta = self._generate_text_with_fallback(
                    repair_prompt,
                    max_tokens=max_tokens,
                    expect_json=True,
                )
                last_text = repaired_text
                last_metadata = repaired_meta
                try:
                    return self._safe_json_loads(repaired_text), repaired_meta, repaired_text
                except json.JSONDecodeError as exc2:
                    last_error = str(exc2)
                    continue

        # Fallback: 返回空结构而不是抛出异常
        print(f"⚠️  {stage_name} JSON 生成失败（重试 {retries} 次）: {last_error}")
        print(f"⚠️  使用 fallback 结构继续")
        return {}, last_metadata, last_text
    
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
            try:
                detailed, llm_metadata, detailed_text = self._generate_json_with_repair(
                    detailed_prompt,
                    max_tokens=self.detailed_max_tokens,
                    stage_name="detailed_section_outlines",
                )
            except Exception as exc:
                print(f"⚠️ 详细大纲生成失败，使用基础结构继续: {exc}")
                detailed = {}
                llm_metadata = {
                    "provider": self.last_provider_used,
                    "model": self.model,
                    "error": str(exc),
                    "fallback_to_base_structure": True,
                }
                detailed_text = ""
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
        lock_terms = self._build_domain_lock_terms(document_title, section_title, subsection_title, user_requirements)
        lock_suffix = " / ".join(lock_terms[:4]) if lock_terms else ""
        prompt = f"""
你正在撰写一篇关于"{document_title}"的文档。

**整体背景**:
{user_background}

**整体需求**:
{user_requirements}

**当前章节**: {section_title}
**当前小节**: {subsection_title}

**领域锁定关键词（用于检索）**: {lock_suffix or "请从当前小节标题提取核心术语"}

**该小节详细大纲**:
{subsection_description}

**写作要求**:
1. 严格按照当前小节详细大纲展开，不写大纲之外的内容
2. 详细展开，字数控制在 500～800 字
3. 使用清晰的逻辑结构，可以包含小标题
4. 语言专业、准确，避免空洞内容
5. 如果涉及技术概念，需要提供具体例子或解释
6. 该内容将作为历史记录，下一小节生成时会参考它进行去重检测，请尽量避免冗余表达
7. 若需要检索外部资料：每个查询词都必须追加领域锁定后缀，格式示例：
   - "原始查询 + 领域:{lock_suffix or subsection_title}"
   - 禁止只用通用关键词裸搜（会导致跨领域引用漂移）

请直接输出该小节的正文内容，不要添加"该小节内容如下"等引导语。
"""
        return prompt.strip()

    def _build_domain_lock_terms(self, document_title: str, section_title: str, subsection_title: str, user_requirements: str) -> List[str]:
        text = " ".join([
            str(document_title or ""),
            str(section_title or ""),
            str(subsection_title or ""),
            str(user_requirements or ""),
        ])
        tokens = re.findall(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff\-_]{1,30}", text)
        stopwords = {
            "section", "subsection", "outline", "content", "prompt", "chapter", "part",
            "写作", "内容", "小节", "章节", "大纲", "文档", "生成", "要求",
            "the", "and", "for", "with", "from", "that", "this",
        }
        terms: List[str] = []
        for token in tokens:
            t = token.strip().lower()
            if not t or t in stopwords or t.isdigit() or len(t) <= 1:
                continue
            if t not in terms:
                terms.append(t)
            if len(terms) >= 6:
                break
        return terms
    
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
                    response = self.http_session.post(
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
