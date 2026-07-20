# Stack consolidado: `gx-bt-rag-mcp`

MCP server de **retrieval semántico puro** (sin LLM propio) sobre documentación de GeneXus y Bantotal, con una **capa de validación estructurada** que verifica sintaxis y firmas contra un catálogo, no solo contra similitud semántica.

---

## 1. Principio rector

Dos capas con garantías distintas:
- **Retrieval** (semántico + BM25 + reranking): devuelve *texto parecido* con score. **Mitiga** la alucinación.
- **Validación** (`verify_syntax`, `list_methods`, `get_signature` sobre SQLite): devuelve *veredicto* contra catálogo cerrado. **Corta** la alucinación.

---

## 2. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| MCP Framework | SDK oficial `mcp` (v1.x, `mcp[cli]`) |
| Transporte | stdio (local) |
| Vector DB | ChromaDB (local, persistente) |
| Embeddings | sentence-transformers + `intfloat/multilingual-e5-large` |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Syntax DB | SQLite (built-in) + SQLAlchemy |
| Chunking | `langchain-text-splitters` |
| Scraper GX | `httpx` + `beautifulsoup4` + `html2text` |
| PDF Parser BT | PyMuPDF + `pdf2image` + `pytesseract` (OCR opcional) |
| Ingesta XPZ | Skill propia de análisis XPZ |
| Config | `pydantic-settings` + `pyyaml` |
| Testing | `pytest` + `pytest-asyncio` |
| Container | Docker + docker-compose |
| Observabilidad | `structlog` |

---

## 3. Colecciones ChromaDB

| Colección | Contenido |
|---|---|
| `gx_docs` | Documentación wiki GeneXus |
| `bt_docs` | Documentación core Bantotal |
| `bt_apis` | Especificaciones de APIs Bantotal |
| `gx_syntax` | Snippets de código GeneXus validados |
| `cross_ref` | Relaciones GX ↔ BT (objeto GX ↔ tabla Bantotal) |

---

## 4. Tools MCP

### Retrieval
- `search_docs(query, collection, top_k, min_score, version?, snippet_mode)`
- `get_document(doc_id)`
- `search_cross(query_gx, query_bt, top_k)`

### Validación
- `list_methods(object_type, version?)`
- `get_signature(method_name, object_type, version?)`
- `verify_syntax(snippet, language, version?)`

### Ingesta
- `add_from_url(url, collection, metadata)`

---

## 5. Syntax DB (SQLite)

### Tablas de catálogo
- **`gx_syntax`**: `name`, `object_type`, `category`, `signature` (JSON), `description`, `version_since`, `source`, `source_doc_id`, `kb_scope`
- **`bt_apis`**: `endpoint`, `method`, `path`, `params`, `request_body`, `responses`, `auth_type`, `source_doc_id`

### Tablas de gobierno
- **`validation_log`**: `timestamp`, `tool`, `input_snippet`, `veredicto`, `matched_entry_id`, `version_queried`, `source`
- **`missing_methods`**: `token`, `object_type`, `first_seen`, `hit_count`, `status`

---

## 6. Estructura de carpetas

```
gx-bt-rag-mcp/
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── config/
│   ├── config.yaml
│   └── mcp.json
├── src/
│   ├── server.py
│   ├── config.py
│   ├── tools/       # search, retrieve, validate, ingest
│   ├── vectorstore/ # chroma_client, embeddings, retriever, collections
│   ├── syntax_db/   # models, gx_repository, bt_repository, audit_repository
│   ├── ingestion/    # gx_scraper, bt_pdf_parser, xpz_ingestor, chunker, loader
│   └── prompts/
├── data/            # gitignored
│   ├── chroma/
│   ├── syntax.db
│   └── raw/
├── scripts/
│   ├── ingest_gx.py
│   ├── ingest_bt.py
│   └── ingest_xpz.py
└── tests/
```

---

## 7. Decisión abierta

**Arquitectura: monolito vs. dos servidores MCP.** Un servidor (retrieval + validación juntos) es más simple. Dos servidores permiten versionar y certificar cada parte por separado — ventaja en contexto regulado.
