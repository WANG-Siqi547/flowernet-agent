import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from outliner import FlowerNetOutliner


REMOTE_ENV = {
    "RENDER": "true",
    "OUTLINER_PROVIDER_CHAIN": "azure,sensenova,dashscope,openrouter,deepseek",
    "OUTLINER_PROVIDER": "dashscope",
    "OUTLINER_FORCE_DEEPSEEK_ON_RENDER": "true",
    "OUTLINER_REMOTE_SINGLE_LLM_CALL": "true",
    "OUTLINER_DETAIL_LLM_ENABLED": "false",
    "OUTLINER_PROVIDER_RETRIES": "1",
    "OUTLINER_JSON_RETRIES": "1",
    "OUTLINER_DEEPSEEK_API_KEY": "test-key",
    "OUTLINER_DEEPSEEK_MODEL": "deepseek-v4-flash",
    "OUTLINER_DASHSCOPE_API_KEY": "legacy-dashscope-key",
    "OUTLINER_DASHSCOPE_MODEL": "glm-5",
}


class StubOutliner(FlowerNetOutliner):
    def __init__(self, *args, response_text: str, **kwargs):
        self.response_text = response_text
        self.calls = 0
        super().__init__(*args, **kwargs)

    def _generate_text_with_fallback(self, prompt: str, max_tokens: int, expect_json: bool = False):
        self.calls += 1
        return self.response_text, {
            "provider": "deepseek",
            "model": self.deepseek_model,
            "prompt_tokens": 12,
            "output_tokens": 34,
        }


class RemoteOutlinerHardeningTests(unittest.TestCase):
    def test_render_forces_deepseek_and_single_call_mode(self):
        with patch.dict(os.environ, REMOTE_ENV, clear=True):
            outliner = FlowerNetOutliner(provider="dashscope", model="glm-5")

        self.assertEqual(outliner.provider_chain, ["deepseek"])
        self.assertEqual(outliner.deepseek_model, "deepseek-v4-flash")
        self.assertTrue(outliner.remote_single_llm_call)
        self.assertFalse(outliner.detail_llm_enabled)

    def test_full_outline_uses_one_llm_call_when_detail_llm_disabled(self):
        response = json.dumps({
            "title": "深度学习图像识别综述",
            "sections": [
                {
                    "id": "wrong_section_id",
                    "title": "理论基础与发展脉络",
                    "description": "梳理卷积神经网络的基本概念和历史演进。",
                    "subsections": [
                        {
                            "id": "wrong_subsection_id",
                            "title": "核心概念",
                            "description": "说明卷积、感受野和特征提取的基本逻辑。",
                        },
                        {
                            "id": "another_wrong_id",
                            "title": "发展历程",
                            "description": "概述从早期网络到现代架构的演进。",
                        },
                    ],
                }
            ],
        }, ensure_ascii=False)

        with patch.dict(os.environ, REMOTE_ENV, clear=True):
            outliner = StubOutliner(response_text=response, provider="dashscope", model="glm-5")
            result = outliner.generate_full_outline(
                user_background="计算机视觉方向硕士生",
                user_requirements="文档主题：深度学习图像识别综述",
                max_sections=1,
                max_subsections_per_section=2,
            )

        self.assertTrue(result["success"])
        self.assertEqual(outliner.calls, 1)
        self.assertEqual(result["metadata"]["structure_generation"]["active_provider"], "deepseek")
        self.assertEqual(result["metadata"]["detail_generation"]["provider"], "structure_only")
        self.assertEqual(result["structure"]["sections"][0]["id"], "section_1")
        self.assertEqual(result["structure"]["sections"][0]["subsections"][0]["id"], "subsection_1_1")

    def test_remote_json_failure_does_not_call_llm_repair(self):
        with patch.dict(os.environ, REMOTE_ENV, clear=True):
            outliner = StubOutliner(response_text="not json", provider="deepseek", model="deepseek-v4-flash")
            result = outliner.generate_full_outline(
                user_background="研究生",
                user_requirements="文档主题：测试主题",
                max_sections=1,
                max_subsections_per_section=1,
            )

        self.assertFalse(result["success"])
        self.assertEqual(outliner.calls, 1)
        self.assertIn("json_generation_failed", result["error"])


if __name__ == "__main__":
    unittest.main()
