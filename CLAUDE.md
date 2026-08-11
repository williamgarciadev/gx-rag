# CLAUDE.md

Guía para trabajar en este repo. **Verificado contra el código el 2026-08-11**; lo fechado así se
midió, no se supone.

## What This Is

RAG de documentación GeneXus/Bantotal para banca. **Retrieval puro — sin LLM propio.** Lo consumen
agentes IA externos (Claude Code y similares) vía MCP, y su objetivo de diseño es **reducir la
alucinación del agente consumidor**.

**El consumidor NO es una persona, y de ahí sale el criterio que ordena las decisiones de interfaz:**

> **La descripción de una tool y el texto de su respuesta son el PROMPT del agente que la consume,
> no documentación.** Una descripción vaga no confunde a un lector: hace que el agente concluya mal
> y afirme algo falso aguas abajo. Por eso toda respuesta declara qué NO se puede concluir de ella
> (ver `_VERDICT_GUIDANCE` y las descripciones de `READ_TOOLS` en `src/server.py` como referencia de
> estilo), y todo parámetro declarado se honra de verdad.

Estado: **Fase 1** (caracterización de corpus + ingesta de wiki). Progreso vivo en
`FASE1_ALISTAMIENTO.md`; orden de lectura de los docs de planeación en `docs/README.md`.

| Necesitás | Leé |
|---|---|
| La arquitectura real, con clasificación de evidencia por afirmación | **`RAG_SYSTEM_MAP.md`** — reconstruido desde el código, es el mapa autoritativo |
| El QUÉ SE QUERÍA (hay requisitos escritos y sin implementar) | `docs/` — ⚠️ `docs/CLAUDE.md` es **spec de planeación, no realidad** |
| Auditar el repo contra sus propios docs | `docs/PROMPT-auditoria-agente.md` |
| **Trabajo de optimización en curso** (hallazgos medidos, fases, coordinación entre agentes) | **`docs/PLAN-optimizacion.md`** — empezá por su sección "Cómo arrancar" |

## Commands

```bash
# Full stack (requiere red externa: docker network create infra_gateway)
docker compose up
# Servicios: gx-rag-api (FastAPI, puerto host 8000), gx-rag-ui (nginx :80 interno, sin puerto host)

# Backend local — SIEMPRE en venv (ver nota abajo)
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn src.api:app --reload --port 8000   # HTTP API (puerto canónico 8000, igual que el Dockerfile)
.venv/bin/python -m src.server                        # MCP server (stdio) — entrypoint aparte, NO lo levanta compose

# Tests — 82 passing al 2026-08-11 16:30. Corré esto antes de creerle al número:
.venv/bin/python -m pytest tests/ -q
# Marcadores (pytest.ini, --strict-markers): unit | integration

# Ingesta
.venv/bin/python scripts/scrape_wiki.py <url> [--output-dir <dir>]
.venv/bin/python -m scripts.ingest_dump <file> --format genexus|workwithplus [--dry-run]

# Frontend (frontend/) — Vite, puerto 5173
npm install && npm run dev
# Fuera de Docker, apuntá el proxy a la API local (el default gx-rag-api:8000 no resuelve):
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev
```

**Puertos**: API `8000`, frontend dev `5173` (Vite incrementa a 5174+ si está tomado). El frontend
llama `/api/*` y Vite lo proxea a `VITE_API_TARGET`.

**`./dev.sh`** levanta backend + frontend juntos en **8050** / **3050** para no colisionar con otros
proyectos de esta máquina (`gx-docs-v1` usa `:8000`). Override con `BACKEND_PORT` / `FRONTEND_PORT`.

**Corré en el venv, no en el intérprete global.** Un paquete global (`notebooklm_skill`) trae un
package `scripts` de nivel superior que tapa el `scripts/` de este repo (que no tiene `__init__.py`,
así que es namespace package implícito) y rompe `from scripts import scrape_wiki` en `src/api.py`.
Docker no se ve afectado (imagen limpia).

**No existe `pyproject.toml`**: dependencias en `requirements.txt`, config de tests en `pytest.ini`.
Sin ruff ni mypy configurados.

## Architecture

Dos entrypoints sobre un mismo core de ingesta/retrieval:

- **`src/server.py`** — MCP server (SDK oficial `mcp`, stdio). **7 tools**, separadas por permiso:
  - `READ_TOOLS` (5): `search_docs`, `get_article`, `get_document`, `verify_syntax`, `list_methods`.
  - `WRITE_TOOLS` (2): `add_from_url`, `ingest_batch`. **Mutan el corpus ⇒ quedan FUERA del
    transporte público** (`add_from_url` es un vector de SSRF y la ingesta exige gate humano).
    `PUBLIC_TOOL_NAMES` se deriva de `READ_TOOLS`, así que esa frontera no puede desincronizarse.
- **`src/api.py`** — FastAPI "Ingestor API" para la UI admin: `/`, `/health`, `/status`, `/scrape`,
  `/discover`, `/articles`, `/article`, `/documents`, `/search`, `/verify`, `/methods`.
  `src/http_server.py` expone el transporte público (solo las read tools).

Flujo de datos:

1. **Scrape** (`scripts/scrape_wiki.py`): baja el artículo, lo pasa a Markdown, calcula el SHA256
   `content_hash` para idempotencia, extrae `version_hint`. Sirve para `wiki.genexus.com` **y
   `docs.workwithplus.com`**.
2. **Clasifica** (`classify_genre()`, mismo archivo): scoring por keywords decide el `genre` y con él
   la colección destino. **El eje es el GÉNERO, no el producto**: `reference → gx_syntax`,
   `guide → gx_docs`. Un artículo de WorkWithPlus clasificado `reference` cae en `gx_syntax` — es el
   diseño, no un error.
3. **Ingesta** (`src/ingestion/ingestor.py`): escribe `.md` + `.meta.json` en `data/scraped/` e
   indexa. `src/ingestion/bulk_loader.py` hace lo mismo en lote sobre **dumps delimitados** (formatos
   `genexus` y `workwithplus`), siempre como `genre="guide"`: texto plano es demasiado ruidoso para el
   catálogo de veredicto binario.
4. **Index** (`src/vectorstore/pg_indexer.py`): chunkea con `langchain-text-splitters`
   (`CHUNK_SIZE=800`, overlap 100), embebe con `intfloat/multilingual-e5-large`, escribe en
   **Postgres**. `reindex_if_changed()` compara `content_hash` y saltea sin re-embeddear.
5. **Retrieve** (`src/vectorstore/pg_retriever.py::hybrid_search`): **denso (pgvector) + full-text
   (`ts_rank_cd`) fusionados con RRF (`RRF_K=60`), resuelto EN SQL** con un CTE por criterio. El
   reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`) está **declarado en `config.py` y no usado por
   ningún módulo**: es English-trained y el corpus es español, diferido hasta decidir un modelo
   multilingüe.
6. **Valida** (`src/syntax_db/`): catálogo **SQLite** en `data/syntax.db` (SQLAlchemy).
   `gx_repository.verify_syntax()` da veredicto binario — match exacto → `VÁLIDO`, match solo difuso →
   `INVÁLIDO`, nada parecido → `NO_VERIFICABLE`. Todo veredicto se loguea; los near-miss van a
   `missing_methods` con `hit_count` para curación humana.

> **Principio de dos capas contra la alucinación (regla de diseño — no mezclarlas):** el retrieval
> (Postgres, difuso, con score) **mitiga** la alucinación; la validación (`syntax.db`, catálogo
> cerrado, veredicto binario) la **corta**. Son almacenes distintos: **indexar documentos en la
> colección `gx_syntax` NO alimenta el catálogo del validador**, aunque el nombre lo sugiera.

Frontend (`frontend/src/`): Vue 3 + Vite, UI admin para gestión y curación de corpus — **no** es un
chat RAG (las consultas entran por MCP). `src/tools/` y `src/prompts/` están vacíos: la lógica de
tools vive inline en `server.py` y `api.py`.

## Configuration

`src/config.py` usa pydantic-settings (prefijo `GX_`, lee `.env`). **El `.env` existe** y define
`GXRAG_DSN` (el store Postgres, lo leen `pg_client` y `syntax_db` vía `os.environ`) y
`HF_HUB_DISABLE_XET`. `Config` usa `extra="ignore"` justamente porque el `.env` es de toda la app:
con el `forbid` que trae pydantic-settings por defecto, cualquier clave ajena rompe el import entero.

`data/`: `data/syntax.db` (catálogo SQLite) y `data/scraped/` (artículos crudos).
**No hay `data/chroma/`** — `VectorStoreConfig.path` quedó solo para `scripts/migrate_chroma_to_pg.py`.

## Hard Rules

- Nunca inventes métodos, propiedades ni firmas de GeneXus/Bantotal: `syntax.db` es la fuente de
  verdad, y lo no catalogado es `NO_VERIFICABLE`.
- **El crecimiento del corpus pasa por un gate humano**: descubrir candidatos automáticamente está
  bien, ingerir automáticamente no. La rama segura va por defecto (`dry_run=true` en `ingest_batch`).
- Usá el SDK oficial `mcp`, no FastMCP standalone.
- Sin LangChain/LlamaIndex como framework base — solo `langchain-text-splitters` para chunking.
- Consultá PyPI para cualquier número de versión: no los escribas de memoria.
- Una fase está lista cuando el comportamiento se verificó funcionando, no cuando compila.
- **Después de una escritura, verificá el estado por una vía independiente del valor de retorno.**
  El retorno de una tool no describe el store: para probar pertenencia usá una lectura determinista
  (`get_article` por colección), no una búsqueda por ranking.

## Known Gotchas

- **Un `top_k` chico NO prueba una ausencia.** `gx_docs` tiene ~50k chunks y el ranking mezcla
  fuentes: un tema presente puede no entrar en los primeros 3. Para afirmar que algo no está, subí
  `top_k` y reformulá con términos del dominio.
- **`gx_docs` mezcla tres fuentes**: wiki GeneXus, tickets SAC (`sac.genexus.com`) y wiki de
  WorkWithPlus (`docs.workwithplus.com`). GeneXus tiene un pattern nativo *Work With for Web* y WWP
  (DVelop) es otro producto: una query sobre "el pattern" devuelve los dos entremezclados. **Filtrá
  por procedencia (la url del hit), no por presencia.**
- **`bt_docs` y `bt_apis` están vacías** (2026-08-11): Bantotal todavía sin cubrir. `search_docs`
  avisa qué colecciones tienen contenido cuando pegás en una vacía.
- 🔴 **Un mismo artículo entra con DOS identidades según el camino de ingesta**, y eso rompe la
  idempotencia: `add_from_url` normaliza el `source_key` (`normalize_wiki_url`) y el dump usa la URL
  cruda. Como `reindex_if_changed` deduplica por `source_key`, **un artículo ya ingerido por dump se
  DUPLICA si después entra por URL**. Medido con el artículo WWP 1149. Pendiente unificar el esquema.
- **El proceso MCP conserva el código viejo en memoria**: un cambio se verifica por MCP recién
  después de reconectar el server.
- Chunk size: `config.py` declara 1000/200 pero `pg_indexer.py` usa 800/100 — **manda el módulo**, los
  valores del config no se leen.
- El `score` del retrieval es RRF (posicional, rank-based), **no** una similitud calibrada: más alto
  es mejor, pero el valor absoluto no es una probabilidad.
- Los overrides anidados de env-var en `config.py` probablemente no funcionan (las clases anidadas de
  pydantic-settings no heredan el prefijo `GX_` de forma consistente).
- `docs/gx-bt-rag-mcp-stack.md` especifica firmas más ricas y una colección `cross_ref` que no están
  implementadas.
