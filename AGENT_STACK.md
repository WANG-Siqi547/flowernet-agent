# FlowerNet Agent Stack

FlowerNet keeps the original microservice workflow intact and adds optional agent infrastructure around it. If optional services are not configured, the system falls back to local memory/file storage so document generation can still run.

## Capabilities

- LangGraph-style orchestration spec for `outliner -> generator -> verifier -> controller -> exporter`.
- Vector DB backed RAG memory with Qdrant, Chroma, or local memory fallback.
- RAG reranking that combines semantic similarity, lexical overlap, authority-domain signals, and stored quality priors.
- Tool-use registry exposed through HTTP and the MCP server.
- Redis-ready task queue and checkpoint store with local file fallback.
- LLM evaluation records and Web metrics dashboard integration.

## Optional Dependencies

Install only when you want the production backends:

```bash
pip install -r requirements-agent-optional.txt
```

## Environment Variables

Vector DB:

```bash
VECTOR_DB_BACKEND=auto          # auto | qdrant | chroma | memory
VECTOR_DB_COLLECTION=flowernet_rag
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
CHROMA_PERSIST_DIR=.flowernet_state/chroma
```

Queue and checkpoint:

```bash
REDIS_URL=redis://localhost:6379/0
FLOWERNET_STATE_DIR=.flowernet_state
```

MCP:

```bash
python flowernet-mcp-server/main.py
```

If the `mcp` package is installed it starts a FastMCP server. Without the SDK it accepts JSON-over-stdio messages, which keeps local testing dependency-light.

## HTTP Endpoints

Generator:

- `GET /agent/capabilities`
- `GET /workflow/graph`
- `GET /tools/list`
- `POST /tools/call`
- `POST /rag/index`
- `POST /rag/query`
- `POST /evaluation/record`
- `GET /evaluation/summary`

Web metrics proxy:

- `GET /api/metrics/agent-stack`
- `GET /api/metrics/evaluation-dashboard`

