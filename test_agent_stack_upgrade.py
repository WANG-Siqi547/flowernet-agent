from flowernet_agent_stack import (
    agent_stack_capabilities,
    get_checkpoint_store,
    get_eval_store,
    get_langgraph_adapter,
    get_tool_registry,
    get_vector_store,
)


def test_vector_rag_tooling_roundtrip():
    vector = get_vector_store()
    count = vector.index_rag_results(
        "game theory equilibrium mechanism design",
        [
            {
                "title": "Nash equilibrium and mechanism design survey",
                "body": "A scholarly overview of strategic interaction, equilibrium, and incentive compatibility.",
                "url": "https://doi.org/10.0000/example",
                "quality_score": 0.9,
            }
        ],
        namespace="test",
    )
    assert count >= 1
    hits = vector.query("equilibrium incentive compatibility", namespace="test", top_k=3)
    assert hits
    assert hits[0]["rerank_score"] > 0


def test_checkpoint_eval_tools_and_graph():
    checkpoint = get_checkpoint_store()
    checkpoint.set("test:key", {"status": "ok"}, ttl_seconds=60)
    assert checkpoint.get("test:key")["status"] == "ok"

    eval_store = get_eval_store()
    record = eval_store.record({"document_id": "test-doc", "success": True, "quality_score_avg": 0.88})
    assert record["document_id"] == "test-doc"
    assert eval_store.summary()["count"] >= 1

    registry = get_tool_registry()
    tools = {tool["name"] for tool in registry.list_tools()}
    assert {"rag_query", "rag_index", "eval_summary", "checkpoint_get", "checkpoint_set"}.issubset(tools)
    assert registry.call("eval_summary", {})["success"] is True

    graph = get_langgraph_adapter().graph_spec()
    assert any(node["id"] == "verifier" for node in graph["nodes"])
    assert "history" in graph["state_keys"]

    caps = agent_stack_capabilities()
    assert "vector_store" in caps
    assert "tools" in caps
