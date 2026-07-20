# gx-bt-rag-mcp

MCP server de retrieval semántico puro sobre documentación de GeneXus y Bantotal, con capa de validación estructurada.

Especificación completa: @gx-bt-rag-mcp-stack.md
Repo de referencia (solo lectura, NO copiar código): `~/referencia/knowledge-rag`

## Comandos

```bash
# Entorno
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Calidad
ruff check src/ tests/
mypy src/
pytest -q

# Servidor (stdio)
python -m src.server

# Ingesta
python scripts/ingest_gx.py  # wiki GeneXus
python scripts/ingest_bt.py  # PDFs Bantotal
python scripts/ingest_xpz.py # catálogo desde XPZ
```

## Reglas duras

- **Framework MCP: SDK oficial `mcp` (paquete `mcp[cli]`), NO FastMCP standalone.**
- **Versiones: consultar PyPI antes de fijar. NUNCA inventar un número de versión de memoria.**
- **No inventar métodos, propiedades ni firmas de GeneXus/Bantotal.** El catálogo es la fuente de verdad.
- **Dos capas: retrieval (mitiga) y validación (corta). No mezclar.**
- **Todo local. Zero cloud, zero API keys.**
- **El catálogo NO sale solo de la wiki.** Los XPZ de la KB son la fuente más fiable.

## Arquitectura y convenciones

- `src/tools/` una tool por dominio (search, retrieve, validate, ingest)
- `src/syntax_db/models.py` incluye `validation_log` y `missing_methods`
- Embeddings: `intfloat/multilingual-e5-large` — cambiar obliga a reindexar todo
- Logging estructurado con `structlog` en cada tool de validación

## Qué NO hacer

- No usar LangChain/LlamaIndex como base. Solo `langchain-text-splitters` para chunking.
- No copiar código de knowledge-rag.
- No añadir dependencias cloud ni LLM en el servidor.
- No declarar una fase terminada porque el código compila: el gate es funcional.
