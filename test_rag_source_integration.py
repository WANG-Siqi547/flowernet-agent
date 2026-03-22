import importlib.util
import unittest


def _load_module(module_name: str, file_path: str):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


RAG_MODULE = _load_module(
    "rag_search",
    "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet-generator/rag_search.py",
)
ORCH_MODULE = _load_module(
    "flowernet_orchestrator_impl",
    "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet-generator/flowernet_orchestrator_impl.py",
)
VERIFIER_MODULE = _load_module(
    "flowernet_verifier_main",
    "/Users/k1ns9sley/Desktop/msc project/flowernet-agent/flowernet-verifier/main.py",
)

RAGSearchEngine = RAG_MODULE.RAGSearchEngine
SourceVerifier = RAG_MODULE.SourceVerifier
DocumentGenerationOrchestrator = ORCH_MODULE.DocumentGenerationOrchestrator
FlowerNetVerifier = VERIFIER_MODULE.FlowerNetVerifier


class TestRagSourceIntegration(unittest.TestCase):
    def test_extract_source_numbers(self):
        engine = RAGSearchEngine()
        text = "结论A [来源1]，结论B [来源3]，重复 [来源1]"
        self.assertEqual(engine.extract_source_numbers(text), [1, 3])

    def test_source_verifier_valid_and_invalid(self):
        verifier = SourceVerifier()
        sources = [{"title": "A"}, {"title": "B"}]

        valid_result = verifier.verify(
            text="内容 [来源1] 和 [来源2]",
            source_results=sources,
            require_citations=True,
            min_citations=1,
        )
        self.assertTrue(valid_result["valid"])

        invalid_result = verifier.verify(
            text="内容 [来源9]",
            source_results=sources,
            require_citations=True,
            min_citations=1,
        )
        self.assertFalse(invalid_result["valid"])
        self.assertEqual(invalid_result["invalid_references"], [9])

    def test_verifier_source_check_required(self):
        verifier = FlowerNetVerifier()
        result = verifier.check_sources(
            draft="没有引用",
            source_results=[{"title": "A"}],
            require_source_citations=True,
            min_source_citations=1,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "insufficient_citations")

    def test_orchestrator_prompt_contains_citation_rule(self):
        orchestrator = DocumentGenerationOrchestrator()
        prompt = orchestrator._build_enhanced_prompt(
            original_prompt="请写作",
            outline="测试大纲",
            history_text="",
            rel_threshold=0.85,
            red_threshold=0.4,
            rag_context="【参考资料】\n[来源1] test",
            require_source_citations=True,
        )
        self.assertIn("[来源N]", prompt)
        self.assertIn("【参考资料】", prompt)

    def test_orchestrator_local_outline_fallback_adds_constraints(self):
        orchestrator = DocumentGenerationOrchestrator()
        fallback = orchestrator._build_local_outline_fallback(
            current_outline="介绍人工智能定义与应用。",
            original_outline="人工智能定义、发展、应用场景",
            feedback={
                "relevancy_index": 0.35,
                "redundancy_index": 0.82,
                "feedback": "内容偏离主题且与前文重复",
            },
            rel_threshold=0.5,
            red_threshold=0.7,
            iteration=2,
        )
        self.assertIn("聚焦要求", fallback)
        self.assertIn("去重要求", fallback)


if __name__ == "__main__":
    unittest.main()
