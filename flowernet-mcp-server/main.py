"""
FlowerNet MCP tool server.

Run with the official MCP SDK when installed:
    python main.py

If the SDK is not installed, the module still exposes a small JSON-over-stdio
fallback for local smoke tests. The fallback accepts one JSON object per line:
    {"tool":"rag_query","arguments":{"query":"game theory"}}
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flowernet_agent_stack import get_tool_registry, agent_stack_capabilities


registry = get_tool_registry()


def _run_mcp() -> bool:
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        return False

    app = FastMCP("flowernet-tools")

    @app.tool()
    def rag_query(query: str, top_k: int = 8, namespace: str = "") -> str:
        return json.dumps(
            registry.call("rag_query", {"query": query, "top_k": top_k, "namespace": namespace or None}),
            ensure_ascii=False,
        )

    @app.tool()
    def rag_index(query: str, results_json: str, namespace: str = "global") -> str:
        try:
            results = json.loads(results_json)
        except Exception:
            results = []
        return json.dumps(
            registry.call("rag_index", {"query": query, "results": results, "namespace": namespace}),
            ensure_ascii=False,
        )

    @app.tool()
    def eval_summary() -> str:
        return json.dumps(registry.call("eval_summary", {}), ensure_ascii=False)

    @app.tool()
    def checkpoint_get(key: str) -> str:
        return json.dumps(registry.call("checkpoint_get", {"key": key}), ensure_ascii=False)

    @app.tool()
    def capabilities() -> str:
        return json.dumps(agent_stack_capabilities(), ensure_ascii=False)

    app.run()
    return True


def _run_stdio_fallback() -> None:
    sys.stderr.write("FlowerNet MCP SDK not installed; using JSON stdio fallback.\n")
    sys.stderr.flush()
    for line in sys.stdin:
        try:
            req = json.loads(line)
            tool = str(req.get("tool") or req.get("name") or "")
            args = req.get("arguments") if isinstance(req.get("arguments"), dict) else {}
            if tool == "capabilities":
                payload = {"success": True, "capabilities": agent_stack_capabilities()}
            else:
                payload = registry.call(tool, args)
        except Exception as exc:
            payload = {"success": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if not _run_mcp():
        _run_stdio_fallback()
