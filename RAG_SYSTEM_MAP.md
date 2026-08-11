# RAG System Map — gx-rag

Documento de arquitectura reconstruido desde el código (no desde los READMEs).
Fecha de la reconstrucción: 2026-08-11. Branch: `vps-produccion`.

**Leyenda de evidencia** — cada afirmación relevante lleva una clasificación:

- **VERIFIED** — confirmado directamente en código, config, SQL o tests (con referencia `archivo:línea`).
- **INFERRED** — fuertemente sugerido por el código pero no confirmado en runtime.
- **UNKNOWN** — evidencia insuficiente.

---

## 1. Resumen ejecutivo

`gx-rag` es un **sistema RAG de retrieval puro, sin etapa de generación propia**
(VERIFIED). Su propósito es dar a un agente de codificación externo (típicamente
un LLM con cliente MCP) acceso verificable a la documentación de GeneXus,
Bantotal, WorkWithPlus y tickets SAC, para reducir alucinaciones de sintaxis y
APIs al generar código GeneXus.

Puntos centrales:

- **No hay LLM, ni prompts de sistema en archivos, ni historial de
  conversación en este repo.** `src/prompts/` y `src/tools/` contienen solo
  `__init__.py` vacíos (VERIFIED). La generación la hace el agente consumidor;
  la estrategia anti-alucinación vive en las **descripciones de las tools MCP y
  en los textos de respuesta** (`src/server.py:7-13` — "las descripciones de
  las tools son el prompt").
- **Vector store: PostgreSQL + pgvector + tsvector** (migrado desde
  ChromaDB + BM25 en commit `c4fb609`). Una sola tabla `chunks` con índice
  HNSW (coseno) y GIN (full-text). Corpus real medido contra la base el
  **2026-08-11**: `gx_docs` **50.063** · `bt_docs` **1.122** · `gx_syntax` **26** ·
  `bt_apis` **0** (VERIFIED, `SELECT collection, count(*) FROM chunks GROUP BY 1`).
  El `tsv` de la base viva usa la config **`es_simple`** (`sql/002-unaccent-tsv.sql`),
  no la `'simple'` que todavía declara el `001` — ver §17.
- **Retrieval híbrido real**: denso (e5-large, 1024 dims) + léxico (tsvector,
  `ts_rank_cd`), fusionados con **RRF k=60 en una sola query SQL**
  (`src/vectorstore/pg_retriever.py:30-59`).
- **Reranking: NO IMPLEMENTADO** (config presente, deliberadamente sin cablear).
- **Evaluación de calidad RAG: NO IMPLEMENTADA** (sin set de relevancia
  etiquetado — declarado en el propio schema SQL).
- Tres entry points: MCP stdio (`src/server.py`), MCP público Streamable HTTP
  (`src/http_server.py`, solo lectura, bearer auth fail-closed) y API admin
  FastAPI (`src/api.py`, sin auth, solo accesible por túnel SSH).

---

## 2. Estructura del repositorio

```text
gx-rag/
├── src/
│   ├── server.py            # MCP server stdio: definición y dispatch de tools (743 líneas)
│   ├── http_server.py       # Transporte público MCP Streamable HTTP (solo read tools)
│   ├── api.py               # FastAPI admin: ingesta (/scrape, /discover) + proxies REST
│   ├── config.py            # pydantic-settings, prefijo GX_
│   ├── ingestion/
│   │   ├── ingestor.py      # Orquesta scrape → fs → indexación
│   │   └── bulk_loader.py   # Parsers de dumps (genexus, workwithplus) → RawDoc
│   ├── vectorstore/
│   │   ├── pg_client.py     # ConnectionPool psycopg3 (GXRAG_DSN)
│   │   ├── pg_indexer.py    # Chunking + embedding + upsert (CHUNK_SIZE=800 hardcodeado)
│   │   ├── pg_retriever.py  # hybrid_search: RRF denso+léxico en una query SQL
│   │   ├── pg_collections.py# get_document / get_article / collection_counts
│   │   └── embeddings.py    # e5-large singleton + lru_cache por lote
│   ├── syntax_db/           # Catálogo curado de sintaxis (SQLAlchemy)
│   │   ├── models.py        # gx_syntax, bt_apis, validation_log, missing_methods, syntax_candidates
│   │   ├── extractor.py     # Detección de candidatos y firma "Syntax:"
│   │   ├── candidate_repository.py  # staging → promoción humana
│   │   └── gx_repository.py # verify_syntax + telemetría
│   ├── prompts/             # SOLO __init__.py vacío (placeholder)
│   └── tools/               # SOLO __init__.py vacío (placeholder)
├── scripts/
│   ├── scrape_wiki.py       # Scraper wiki/SAC/WWP (httpx + BS4 + html2text)
│   ├── ingest_dump.py       # CLI de ingesta de dumps
│   ├── candidates.py        # CLI de curación de sintaxis
│   └── migrate_chroma_to_pg.py  # Migración one-shot ChromaDB → Postgres
├── sql/001-chunks-schema.sql    # DDL de la tabla chunks (fuente de verdad del schema)
├── tests/                   # 6 archivos pytest (unit + integration)
├── frontend/                # Vue 3 + Vite — buscador de solo-lectura
├── docs/                    # Docs de planeación (varios desactualizados, ver §17)
├── Dockerfile, docker-compose.yml, DEPLOY.md, docs/deploy-vps.md
└── data/                    # scraped/, scraped_sac/, syntax.db (SQLite local)
```

---

## 3. Diagrama de arquitectura (real, verificado)

### Query path

```text
                 ┌──────────────────────────────────────────┐
                 │   AGENTE EXTERNO (LLM con cliente MCP)   │  ← la "generación"
                 │   (Claude Desktop / otro — fuera del repo)│     vive AQUÍ
                 └───────┬──────────────────────┬───────────┘
              stdio      │                      │  HTTPS (Traefik, TLS)
                         ▼                      ▼
              ┌──────────────────┐   ┌───────────────────────────┐
              │ src/server.py    │   │ src/http_server.py        │
              │ MCP stdio        │   │ POST /mcp/                │
              │ READ + WRITE     │   │ solo READ_TOOLS           │
              │ tools            │   │ bearer fail-closed, rate  │
              └───────┬──────────┘   │ limit, 256KB cap          │
                      │              └───────────┬───────────────┘
                      │   dispatch_tool()        │ guarded_dispatch
                      ▼    (server.py:503)       ▼
              ┌──────────────────────────────────────────┐
              │  Retrieval                               │
              │  • hybrid_search (pg_retriever.py:78)    │──► PostgreSQL+pgvector
              │  • get_article / get_document            │    (tabla chunks,
              │    (pg_collections.py)                   │     HNSW + GIN)
              │  • verify_syntax (gx_repository.py:216)  │──► Syntax DB
              └───────┬──────────────────────────────────┘    (PG o SQLite)
                      ▼
              Respuesta markdown con procedencia
              (url, article_id, content_hash, score RRF)
```

Además: **frontend** Vue 3 (`gx-rag-ui`, nginx) → `GET /api/search` →
`src/api.py` (proxy REST sobre el mismo retrieval). Buscador de solo-lectura
para humanos (VERIFIED, `frontend/src/components/SearchPanel.vue`).

### Ingestion path

```text
  URL wiki/SAC/WWP              Dump de texto (.txt)            Curación humana
        │                             │                              │
        ▼                             ▼                              ▼
  scrape_wiki.scrape()        bulk_loader.parse_*_dump()      extractor.extract_
  (httpx→BS4→html2text)       → RawDoc                        candidates()
        │                             │                              │
        ▼                             ▼                              ▼
  ingestor.ingest()           load_dump()                     syntax_candidates
  ├─ data/scraped/*.md        (genre="guide" forzado)         (staging, pending)
  └─ *.meta.json                    │                              │ promote_
        │                           │                              ▼ (humano, CLI)
        ▼                           ▼                          gx_syntax
  pg_indexer.reindex_if_changed()  (dedup por content_hash SHA256)
        │
        ▼
  RecursiveCharacterTextSplitter (800/100, separadores Markdown)
        │
        ▼
  embeddings.embed_texts() — e5-large, 1024 dims, L2-normalizado
        │
        ▼
  INSERT … ON CONFLICT en tabla chunks (DELETE+INSERT transaccional si cambió)
```

---

## 4. Pipeline de ingesta

### 4.1 Fuentes y formatos (VERIFIED)

Tres vías de entrada:

**A. Scraping por URL** — `scripts/scrape_wiki.py`
- Hosts soportados: `wiki.genexus.com`, `docs.genexus.com`,
  `docs.workwithplus.com`, `sac.genexus.com` (detección SAC por `td#CONTENIDO`,
  línea ~191).
- `fetch_article()` (~línea 163): httpx, timeout 45 s, `follow_redirects=True`,
  `raise_for_status()` (errores HTTP propagan).
- `extract_main_content()` (~línea 181): cadena de fallbacks SAC →
  `div.gx-content-placeholder` → `div#MAINTABLE` → `body`; elimina
  script/style/noscript/iframe/svg y divs de navegación.
- `html_to_markdown()` (~línea 235): html2text (`body_width=0`,
  `unicode_snob=True`). **No hay PDF ni OCR en el camino de scraping** —
  `pymupdf` está en requirements pero sin uso en este flujo (VERIFIED por
  ausencia de imports en los módulos de ingesta).

**B. Dumps de texto pre-scrapeados** — `src/ingestion/bulk_loader.py`
- Formato `genexus` (`parse_genexus_dump`, línea 88): bloques
  `--- INÍCIO DO CONTEÚDO DE: {id}.html ---`; reconstruye URL canónica.
- Formato `workwithplus` (`parse_wwp_dump`, línea 106): bloques `--- URL: … ---`
  separados por `====`.
- Ambos producen `RawDoc` (dataclass, línea 37) y fuerzan `genre="guide"`
  (líneas 146-148). Bloques vacíos se saltan silenciosamente.

**C. Curación humana de sintaxis** — `scripts/candidates.py` +
`src/syntax_db/extractor.py`
- Candidato solo si título ≤ 5 palabras Y el cuerpo contiene `syntax`
  (extractor.py:72-75). Firma extraída tras "Syntax:" (cap 200 chars).
- Nada entra automáticamente al catálogo: `store_drafts()` → staging
  `syntax_candidates` (status `pending`); `promote_candidate()`
  (candidate_repository.py:81) es la única vía hacia `gx_syntax`, acción
  humana vía CLI.

### 4.2 Metadata (VERIFIED)

`ArticleMetadata` (scrape_wiki.py:41-53): `url, title, page_id, genre
("reference"|"guide"), destination ("gx_syntax"|"gx_docs"), source_key,
content_hash (SHA256), version_hint, word_count, scraped_at`.

- `normalize_wiki_url()` (~línea 66): unifica hosts →
  `https://docs.genexus.com/en/wiki?{id}`. **Excepción conocida**: SAC sin ID
  numérico colapsa a una source_key genérica → dedup débil para SAC
  (VERIFIED en `data/scraped_sac/*.meta.json`).
- `classify_genre()` (~línea 123): scoring heurístico; `reference` →
  `gx_syntax`, `guide` → `gx_docs`.

### 4.3 Chunking (VERIFIED)

`src/vectorstore/pg_indexer.py:24-32`:

- `RecursiveCharacterTextSplitter` (langchain-text-splitters),
  **chunk_size=800, chunk_overlap=100** — constantes del módulo.
- Separadores `["\n## ", "\n### ", "\n\n", "\n", " "]`, `keep_separator=True`:
  chunking **estructural sobre Markdown** (respeta encabezados), **no
  semántico**. Sin manejo especial de código ni tablas.
- IDs de chunk: `{article_id}_c{i}` (línea 88). Contenido vacío → warning y
  `[]` (línea 83-85).
- **Discrepancia**: `config.py:18-19` declara `chunk_size=1000,
  chunk_overlap=200`, pero el indexer no los lee — config muerta para el
  camino actual (VERIFIED).

### 4.4 Embeddings (VERIFIED)

`src/vectorstore/embeddings.py`:

- Modelo `intfloat/multilingual-e5-large` (constante línea 13), sentence-
  transformers, singleton lazy (`get_model()`, línea 16).
- `model.encode(..., normalize_embeddings=True)` → 1024 dims, norma L2 = 1.0
  (confirmado en `sql/001-chunks-schema.sql:7`).
- Caché `lru_cache(maxsize=1024)` sobre la **tupla completa** de textos
  (línea 25): cachea por lote, no por chunk individual.
- **Sin prefijos `query:`/`passage:`** que e5 recomienda — texto crudo en
  ingesta y query (VERIFIED en embeddings.py:32 y pg_retriever.py:98).
  Consistente entre ambos lados; posible mejora de recall (INFERRED).

### 4.5 Persistencia, dedup e incrementalidad (VERIFIED)

`pg_indexer.py`:

- `_write_chunks()` (línea 63): split → embed → `executemany(_UPSERT_SQL)`,
  todo sobre la conexión recibida (evita el deadlock DELETE+INSERT resuelto
  en commit `dfeeb63`, docstring líneas 74-81).
- `index_article()` (línea 114): inserción directa sin chequeo de hash.
- `reindex_if_changed()` (línea 132): `SELECT content_hash` → igual:
  `"skipped"` (no re-embebe); distinto: DELETE + INSERT en la misma
  transacción → `"reindexed"`; ausente → `"indexed"`. **Ingesta incremental
  real** por `content_hash` (SHA256).
- Upsert: `INSERT … ON CONFLICT (chunk_id) DO UPDATE` (línea 34-49; el
  comentario 50-55 documenta un bug real donde `collection` fuera del DO
  UPDATE hacía inalcanzable la colección pedida).
- **No existe tool ni endpoint para borrar artículos** (VERIFIED por
  ausencia en server.py/api.py).

---

## 5. Pipeline de retrieval

### 5.1 Búsqueda híbrida (VERIFIED)

`hybrid_search()` — `src/vectorstore/pg_retriever.py:78`:

1. `_to_tsquery()` (línea 67): tokeniza `re.findall(r"\w+", text.lower())` y
   une con `|` (OR deliberado para no perder recall vs el BM25 anterior,
   docstring 68-73). Query sin tokens → `[]` (línea 95-96).
2. `emb.embed(query)` → vector e5-large de la query.
3. **Una sola query SQL** (`_SEARCH_SQL`, líneas 30-59):
   - CTE `dense`: `ORDER BY embedding <=> qvec` (distancia coseno, índice
     HNSW), `LIMIT 30` (`CANDIDATE_POOL`, línea 25).
   - CTE `lexical`: `tsv @@ to_tsquery('simple', …)`, rankeado por
     `ts_rank_cd` (índice GIN), `LIMIT 30`.
   - CTE `fused`: `SUM(1/(60 + rank))` — **RRF con K=60** (línea 24),
     fusiona sin reconciliar escalas (rank-based).
   - Filtro opcional `min_score` post-fusión; `ORDER BY score DESC LIMIT
     top_k` (default 5).
4. Output por hit: `{doc_id (chunk_id), id (article_id), title, content,
   source, url, content_hash, score}` (líneas 114-127).

Notas documentadas en el propio código:
- El score RRF es **posicional y NO calibrado** — piso de ranking, no umbral
  de confianza (docstring líneas 90-92).
- tsvector usa config `'simple'` (sin stemming) deliberadamente: fiel al
  tokenizador BM25 reemplazado y al corpus bilingüe; dos deltas medidos vs
  BM25 (indexa `title||content`; `ts_rank_cd ≠ BM25Okapi`) están documentados
  en `sql/001-chunks-schema.sql:40-49`.

### 5.2 Reranking

```text
Reranking: NOT IMPLEMENTED
```

`config.py:17` define `reranker_model = "cross-encoder/ms-marco-MiniLM-L-6-v2"`
pero **ningún módulo lo importa** (VERIFIED por ausencia de `CrossEncoder` en
el código). `CLAUDE.md` documenta la decisión: modelo entrenado en inglés,
corpus mayormente en español — cableado diferido.

### 5.3 Construcción de contexto (VERIFIED)

En `server.py` (~líneas 519-547): **sin dedup, sin límite de tokens, sin
reordenamiento ni compresión**. Los hits se toman en orden de score y cada
`content` se trunca a `snippet_chars` (default 600) con "…". Si no hay
resultados, se consulta `collection_counts()` y se devuelve un hint de qué
colecciones tienen contenido.

### 5.4 Citas / procedencia (VERIFIED)

No hay generador de citas: la procedencia viaja en cada resultado (`url`,
`article_id`, `content_hash`) y la instrucción de citación vive en la
descripción de la tool: *"cita la url, no el fragmento suelto"*
(`server.py:55-56`). La responsabilidad de citar bien es del agente
consumidor.

### 5.5 Verificación de sintaxis (segunda capa) (VERIFIED)

`verify_syntax` → `src/syntax_db/gx_repository.py:216`: tokeniza el snippet,
busca en `gx_syntax` (exacto → variantes con sufijos wiki → miembros
extraídos del snippet → similitud por prefijo de 8 chars). Veredictos
`VÁLIDO | INVÁLIDO | NO_VERIFICABLE`, con `_VERDICT_GUIDANCE` adjunta
(`server.py` ~línea 333) que prescribe cómo NO malinterpretar cada veredicto.
Escribe telemetría en `validation_log` y cola de curación en
`missing_methods` (upsert con `hit_count++`). El filtrado por versión **no se
enforcea** (nota explícita en gx_repository.py ~líneas 248-250).

---

## 6. Pipeline de generación

```text
Generación (LLM, prompt de sistema, historial): NO EXISTE EN ESTE REPO
```

VERIFIED por búsqueda exhaustiva: no hay cliente de LLM (sin imports de
openai/anthropic/ollama), `src/prompts/` y `src/tools/` están vacíos, no hay
manejo de historial de conversación, ni streaming, ni temperatura/retries de
LLM que configurar.

El diseño lo declara `server.py:7-13`: el consumidor es un agente; las
descripciones de las tools y los textos de respuesta son la capa de
"prompting". Los mecanismos anti-alucinación implementados ahí (VERIFIED):

- Descripción de `search_docs` (server.py:53-74): filtrar por procedencia
  (wiki GX vs SAC vs WorkWithPlus mezclados en la misma colección), nunca
  concluir "no está documentado" con `top_k` chico, "recuperar ≠ verificar".
- `_VERDICT_GUIDANCE` (server.py ~333): semántica asimétrica — solo VÁLIDO
  afirma; NO_VERIFICABLE = "no sé"; INVÁLIDO = detector de typo, no prueba de
  inexistencia.
- Respuestas vacías de `search_docs`/`list_methods` advierten que una lista
  vacía no prueba ausencia.

Detalle: la descripción de `search_docs` aún dice "dense + **BM25** + RRF"
(server.py:54) aunque el motor léxico actual es tsvector — deriva menor de
documentación (VERIFIED).

---

## 7. Traza end-to-end: `search_docs` vía transporte público

```text
Agente MCP → POST /mcp/ (Traefik TLS) → auth+limits → guarded_dispatch
→ dispatch_tool → hybrid_search → PostgreSQL → formato markdown → TextContent
```

| Paso | Componente | Archivo:función | Input | Output | Modos de fallo |
|---|---|---|---|---|---|
| 1 | Transporte HTTP | `http_server.py:handle_mcp` (~línea 213) | Request ASGI con Bearer | 401/413/429 o pasa | Sin keys → fail-closed 401; body >256 KB → 413; rate limit → 429 |
| 2 | Allowlist | `http_server.py:guarded_dispatch` (~127-140) | `tools/call` | bloquea tool no pública | `add_from_url` estructuralmente inalcanzable |
| 3 | Dispatch | `server.py:dispatch_tool` (~línea 503) | nombre + args (loguea solo claves) | llama al handler | excepción → `GENERIC_ERROR` redactado (línea ~500) |
| 4 | Tokenización | `pg_retriever.py:_to_tsquery` (67) | query libre | tsquery OR | query sin tokens → `[]` |
| 5 | Embedding | `embeddings.py:embed` (36) | query | vector 1024-d | fallo de carga/descarga del modelo rompe la primera query (sin retry) |
| 6 | SQL híbrido | `pg_retriever.py:hybrid_search` (78) + `_SEARCH_SQL` (30) | qvec, qtext, collection, top_k | filas rankeadas RRF | error de DB propaga → error genérico al cliente (sin retry) |
| 7 | Formato | `server.py` (~519-547) | hits | markdown con url/hash, truncado a `snippet_chars` | vacío → hint de colecciones con contenido |
| 8 | Respuesta | MCP `TextContent` | markdown | al agente | — |

Dependencias externas del camino: PostgreSQL+pgvector (obligatoria), modelo
de embeddings (HuggingFace, cacheado localmente tras la primera carga).

---

## 8. Responsabilidades por componente

| Componente | Responsabilidad | No hace |
|---|---|---|
| `src/server.py` | Definición de tools MCP (READ_TOOLS línea 50, WRITE_TOOLS línea 244), dispatch, formato de respuestas, guidance anti-alucinación | No retrieval directo, no auth |
| `src/http_server.py` | Transporte público: auth bearer, rate limit, body cap, allowlist read-only | No expone write tools |
| `src/api.py` | API admin: ingesta (`/scrape`, `/discover`), proxies REST, `/status` | Sin auth (topología la protege) |
| `src/vectorstore/pg_retriever.py` | Búsqueda híbrida RRF en SQL | No rerankea, no filtra por metadata más allá de `collection` |
| `src/vectorstore/pg_indexer.py` | Chunking, embedding, upsert, reindex transaccional | No scrape, no parsea dumps |
| `src/vectorstore/pg_collections.py` | `get_document`, `get_article` (string_agg), counts | Errores de DB → `None` (ver §13) |
| `src/vectorstore/embeddings.py` | e5-large singleton + caché | Sin prefijos e5, sin batching explícito |
| `src/ingestion/ingestor.py` | scrape → fs (`data/scraped/`) → indexación | Siempre escribe a `data/scraped` |
| `src/ingestion/bulk_loader.py` | Parsers de dumps → `RawDoc` → `load_dump` | Fuerza `genre="guide"` |
| `src/syntax_db/*` | Catálogo curado, verificación, staging de candidatos, telemetría | No promueve candidatos automáticamente |
| `scripts/scrape_wiki.py` | Scraper multi-host + metadata | No indexa por sí solo (lo hace ingestor) |
| `frontend/` | Buscador de solo-lectura para humanos | No gestiona corpus (forms huérfanos) |

---

## 9. Contratos de datos

### `chunks` (tabla central, VERIFIED — `sql/001-chunks-schema.sql`)

| Campo | Tipo | Notas |
|---|---|---|
| `chunk_id` | text PK | `"{article_id}_c{i}"` |
| `collection` | text NOT NULL | gx_docs, gx_syntax, bt_docs, bt_apis |
| `article_id` | text NOT NULL | agrupa chunks; suele ser URL/source_key |
| `chunk_index` | int NOT NULL | orden dentro del artículo |
| `title`, `content`, `source`, `url`, `genre` | text | metadata redundada por chunk |
| `content_hash` | text | clave de reindexación idempotente |
| `embedding` | vector(1024) NOT NULL | e5-large, L2-normalizado |
| `tsv` | tsvector GENERATED STORED | `to_tsvector('simple', title \|\| ' ' \|\| content)` |

Invariante: el reindexado por artículo es transaccional (DELETE+INSERT en una
conexión) — el artículo nunca queda sin chunks a medias.

### Resultado de búsqueda (VERIFIED — `pg_retriever.py:114-127`)

```text
{ doc_id: str (chunk_id), id: str (article_id), title: str, content: str,
  source: str, url: str, content_hash: str, score: float (RRF, 6 dec.) }
```

### `RawDoc` (VERIFIED — `bulk_loader.py:37`)

```text
{ source_key, title, content, url, page_id }  # dumps pre-scrapeados
```

### `ArticleMetadata` (VERIFIED — `scrape_wiki.py:41-53`)

```text
{ url, title, page_id, genre, destination, source_key,
  content_hash, version_hint, word_count, scraped_at }
```

### Veredicto de `verify_syntax` (VERIFIED)

`VÁLIDO | INVÁLIDO | NO_VERIFICABLE` + guidance textual + side-effect de
telemetría (`validation_log`, `missing_methods` con `hit_count`).

---

## 10. Configuración

### Aplicación / RAG (VERIFIED — `src/config.py`, prefijo `GX_`)

| Variable | Default | Notas |
|---|---|---|
| `GX_PUBLIC_API_KEYS` | `""` | Bearer keys separadas por coma; vacío = fail-closed; cada key ≥32 chars (config.py:69-80) |
| `GX_RATE_LIMIT_PER_MINUTE` | `60` | ventana fija in-memory por key |
| `GX_MAX_AUTH_FAILURES_PER_MINUTE` | `10` | por IP, anti brute-force |
| `GX_ALLOWED_HOSTS` | `"*"` | TrustedHost; prod: `gxrag.wgsoft.com.co,localhost,127.0.0.1` |
| `GX_SERVER_NAME` / `GX_SERVER_TRANSPORT` | `gx-bt-rag-mcp` / `stdio` | |
| `GX_DEFAULT_VERSION` | `gx18` | sin uso visible en retrieval (INFERRED: legado) |
| `GX_VECTORSTORE_*` (nested) | model e5-large; chunk 1000/200 | **chunk_size/overlap no los lee el indexer**; overrides nested probablemente rotos (CLAUDE.md) |
| `GX_SYNTAX_DB_PATH` | `data/syntax.db` | |
| `GX_GX_DOCS`… (CollectionsConfig) | nombres de colecciones | `cross_ref` declarada, no implementada |

### Infraestructura / secrets

| Variable | Dónde se lee | Notas |
|---|---|---|
| `GXRAG_DSN` | `os.environ` en `pg_client.py` y `syntax_db/models.py` (NO via Config) | crítica; sin ella `get_pool()` lanza RuntimeError al primer uso. `src/__init__.py` hace `load_dotenv(override=False)` |
| `HF_HUB_DISABLE_XET` | huggingface_hub | workaround Windows |

Secrets: solo DSN y API keys. `.env` gitignored y excluido del build context
por `.dockerignore` (lección de un leak real, según DEPLOY.md).

### Hardcodeado (no configurable)

`CHUNK_SIZE=800`, `CHUNK_OVERLAP=100` (pg_indexer.py:24-25); `RRF_K=60`,
`CANDIDATE_POOL=30` (pg_retriever.py:24-25); body cap 256 KB; pool PG
min=1/max=8 (pg_client.py).

---

## 11. Dependencias

VERIFIED — `requirements.txt` (rangos, no pins exactos):

- MCP: `mcp>=2,<3` (pin por breaking change 2.0: `add_request_handler`, sin
  decoradores).
- Web: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `httpx>=0.28`.
- ML/IR: `sentence-transformers>=2.0`, `langchain-text-splitters>=0.2`.
- Datos: `psycopg[binary,pool]>=3.2,<4`, `SQLAlchemy>=2.0`.
- Parsing: `beautifulsoup4>=4.15`, `html2text>=2025`, `pymupdf>=1.28` (sin uso
  en el camino de ingesta actual).
- Config: `pydantic>=2`, `pydantic-settings>=2`, `python-dotenv>=1,<2`.
- `structlog>=26.0` — **declarado, cero imports** (dependencia muerta,
  VERIFIED).
- Dev: `pytest>=8`, `pytest-cov>=5`, `ruff>=0.6`, `black>=24`.

Python 3.12 (Dockerfile `python:3.12-slim`, usuario no-root). Frontend: Vue
3.4 + Vite 5.4, build multi-stage node:22-alpine → nginx:alpine.

---

## 12. Servicios externos

| Servicio | Uso | Criticidad |
|---|---|---|
| PostgreSQL + pgvector (externo, red `infra_gateway`, DSN puerto 5433 en ejemplo) | vector store + syntax DB | **crítica** — sin DSN nada funciona |
| HuggingFace Hub | descarga del modelo e5-large (primera vez) | alta en arranque en frío |
| wiki.genexus.com / docs.genexus.com / docs.workwithplus.com / sac.genexus.com | scraping de corpus | solo ingesta |
| Traefik (VPS, `/opt/infra/traefik/`) | TLS + routing `gxrag.wgsoft.com.co` | producción |
| Let's Encrypt (via Traefik) | certificados | producción |

---

## 13. Modos de fallo

| Fallo | Dónde | Detección | Manejo actual | Impacto |
|---|---|---|---|---|
| Falta `GXRAG_DSN` | pg_client.py | RuntimeError al primer uso (fail-fast, `open=True`) | ninguno (explota) | servicio caído hasta configurar |
| Error HTTP en scraping | scrape_wiki.py `fetch_article` | `raise_for_status` | MCP: `GENERIC_ERROR`; batch: causa específica | artículo no ingerido |
| Contenido sin chunks | pg_indexer.py:83 | warning en log | devuelve `[]` (no error) | artículo no indexado silenciosamente |
| Deadlock DELETE+INSERT en reindex | pg_indexer.py | — | **resuelto** (commit `dfeeb63`, misma conexión) | — |
| Proceso muerto a mitad de reindex | pg_indexer.py:132 | — | transaccional: no deja artículo a medias | — |
| Error de DB en get_document/get_article | pg_collections.py:46-48 | log | captura excepción → `None` | **caída de DB se disfraza de 404** (INFERRED del código) |
| Fallo de descarga del modelo e5 | embeddings.py:16 | excepción en primera query | sin retry ni fallback | retrieval caído hasta resolver |
| Excepción en tool pública | server.py ~500 | log.exception | texto genérico redactado | cliente ve error sin detalle (deliberado) |
| Excepción en API admin | api.py | exception handler global | 500 **con texto de la excepción** | fuga de detalle interno (solo superficie interna) |
| Rate limit in-memory | http_server.py | — | contadores por proceso | burst ~2x en borde de ventana; inválido multi-worker (documentado: exige Redis antes de escalar) |
| Query vacía/sin tokens | pg_retriever.py:95 | — | devuelve `[]` | sin resultados |
| `min_score` mal interpretado | pg_retriever.py:90 | docstring | advertido: score no calibrado | falsos rechazos si se usa como umbral de confianza |
| Errores de telemetría de sintaxis | gx_repository.py | log.error | se tragan, nunca rompen verificación | pérdida silenciosa de auditoría |

---

## 14. Seguridad

**Transporte público (`http_server.py`)** — VERIFIED:

- Allowlist estructural read-only (`build_public_server` + `guarded_dispatch`,
  líneas ~118-142): write tools inalcanzables (testeado).
- Bearer auth con `hmac.compare_digest`, **fail-closed** si no hay keys.
- Throttle por IP de fallos de auth **antes** del auth check (10/min default).
- Rate limit por key (60/min default, ventana fija).
- Body cap 256 KB (413). TrustedHost middleware. Errores redactados
  (`GENERIC_ERROR`). Logging solo de **claves** de argumentos (anti-PII,
  server.py ~506).
- Limitaciones documentadas (http_server.py:17-24): contadores in-memory
  (single worker), `_client_ip` usa peer TCP (sin X-Forwarded-For confiable),
  TLS delegado a Traefik.

**API admin (`api.py`)** — VERIFIED:

- **Sin autenticación** y `CORSMiddleware allow_origins=["*"],
  allow_credentials=True` (api.py:32-38) — combinación permisiva. Mitigada
  solo por topología: sin puerto host ni labels Traefik en docker-compose;
  acceso por túnel SSH.
- `/scrape` y `/discover` son **SSRF por diseño** en esta superficie interna;
  `add_from_url` (mismo vector) está excluido del transporte público por eso.

**Secrets** — VERIFIED: `.env` gitignored + `.dockerignore` (hubo un leak
real horneando `.env` en la imagen, documentado en DEPLOY.md); DSN nunca
hardcodeado; el error de keys débiles reporta solo el count, no el material.

**Prompt injection**: no hay defensas específicas contra contenido malicioso
en documentos ingeridos (el corpus se scrapea de dominios allowlisteados por
proceso, no por código). El sistema no ejecuta LLM propio, así que el riesgo
de indirect prompt injection se traslada al agente consumidor (INFERRED).

**Tests de seguridad** (VERIFIED, `tests/test_http_server.py`, 16 tests):
401 fail-closed, 429 rate limit y brute-force, TrustedHost 400, 413,
no-leak de excepciones, `tools/list` = exactamente READ_TOOLS, `add_from_url`
inalcanzable.

---

## 15. Observabilidad

- **Logging**: stdlib `logging.basicConfig(INFO)` en `api.py:21` y
  `server.py:32`; http_server loguea bloqueos de tools y arranque.
  `structlog` declarado pero sin ningún import (planeado, no implementado —
  VERIFIED).
- **Auditoría de dominio**: tablas `validation_log` (todo veredicto de
  `verify_syntax`) y `missing_methods`/`syntax_candidates` (cola de curación
  con `hit_count`) — `src/syntax_db/models.py`.
- **Métricas / tracing**: ninguno. Sin Prometheus, OpenTelemetry, LangSmith
  ni endpoint de métricas (VERIFIED por ausencia).
- **Salud**: `/health` en ambos servidores, healthchecks Docker, y
  `StatusBar` del frontend (poll 30 s a `/api/health` + `/api/status`).
- **No se trackean**: latencias de retrieval/embedding, scores por query,
  tamaño de prompts, uso de tokens (no aplica — sin LLM), ni calidad de
  respuestas.

---

## 16. Evaluación

```text
RAG Evaluation (calidad de retrieval/respuesta): NOT IMPLEMENTED
```

- **No hay set de relevancia etiquetado** — declarado en el propio schema
  (`sql/001-chunks-schema.sql:49`: "Cual de las dos formulas ordena MEJOR es
  una pregunta abierta: no hay set de relevancia etiquetado").
- Las métricas de `docs/gx-bt-rag-calidad-y-no-alucinacion.md` (precisión,
  recall, tasa de falso INVÁLIDO, mala atribución) son **propuesta**, sin
  infraestructura.
- El gate de muestra de 20-30 docs de `FASE1_ALISTAMIENTO.md` sigue pendiente.

Lo que **sí** existe (VERIFIED — `tests/`, pytest con markers
unit/integration):

- `test_http_server.py` (16): contratos de seguridad del transporte público.
- `test_api.py` (13): contratos de error de la API admin.
- `test_pg_store.py`: integración contra Postgres real en schema descartable
  `gxrag_test` con **guarda anti-producción** (verifica `'chunks'::regclass`
  antes de TRUNCATE — hubo un incidente real); cubre retrieval híbrido
  mecánico (la señal léxica rescata lo que el denso pierde, orden denso,
  scope por colección) y `reindex_if_changed` end-to-end.
- `test_syntax_catalog.py`: flujo candidato→promoción→VÁLIDO, resolución de
  snippets, dedup con hit_count.
- `test_bulk_loader.py`: parsers de dumps y decisión de dedup con pool falso.
- `test_server_write_tools.py`: enum del schema = `WRITABLE_COLLECTIONS`,
  `dry_run` default, write tools fuera del transporte público.

Son tests de **correctitud mecánica y contratos**, no de calidad de ranking.

---

## 17. Deuda técnica

### Critical

*(Ninguna detectada de corrección/pérdida de datos. El reindexado es
transaccional y el transporte público está bien blindado.)*

### Resuelto por la auditoría de interfaz (2026-08-11)

Detalle completo, con la medición de cada uno, en la tabla #21–#32 de
`docs/PLAN-optimizacion.md`. Todos tenían test que falla sin el fix; suite 109 passed.

| Issue | Era | VERIFIED por |
|---|---|---|
| `list_methods(version=…)` devolvía vacío siempre | filtraba `version_since`, NULL en las 59 filas por construcción (`candidate_repository.py:94`) | `list_methods('Method', version='gx18')`: 0 → 34 contra el catálogo real |
| `list_methods` sugería `object_type` inexistentes | "Transaction"/"Procedure": 0 filas cada uno | `SELECT DISTINCT object_type`: Command, Event, Method, Property, Rule |
| `verify_syntax` fabricaba `INVÁLIDO` sin token | `name LIKE '%%'` matchea todo | reproducido con `""`, `"do"`, `"if else"` |
| `verify_syntax(version=…)` no declaraba que no gatea | nota solo si `version_since` difería | test de la nota en el `VÁLIDO` |
| `get_document` prometía el documento completo | devuelve 1 chunk (~800 chars) | contrato de `_DOCUMENT_SQL` |
| Colección mal escrita se leía como ausencia | el SDK no valida el enum del inputSchema | `KNOWN_COLLECTIONS` + rechazo previo al embedding |
| Error de DB disfrazado de 404 | `except → None` en `pg_collections` | ahora propaga; test con `get_pool` que lanza |

### Nueva, sin resolver

| Issue | Evidencia | Impacto | Sugerencia |
|---|---|---|---|
| `sql/001` declara `tsv` con config `'simple'` y el retriever consulta `'es_simple'` | `001:50-52` vs `pg_retriever.py:46` | un install fresco que corra **solo** el 001 revienta o pierde la mitad léxica del RRF | actualizar el 001 para que un install nuevo nazca coherente |
| `/discover?source=wiki` inalcanzable | `api.py:323`: `https://wiki.genexus.com/广场/wiki?{query}` | capacidad declarada que falla siempre, en el camino de crecimiento de corpus | corregir la URL de búsqueda de la wiki |
| `bt_docs` fuera de `WRITABLE_COLLECTIONS` | `server.py:236` vs `bt_docs` con 1.122 chunks cargados por CLI | `ingest_batch` rechaza una colección legítima | **decisión humana**: es política de corpus |

### High

| Issue | Evidencia | Impacto | Sugerencia |
|---|---|---|---|
| Documentación desactualizada describe ChromaDB/BM25 eliminados | `CLAUDE.md` raíz ("no hay tests", `data/chroma/`), `docker-compose.yml` (comentario "rw: ChromaDB"), docstrings en `ingestor.py:3`, `api.py:125`, `bulk_loader.py:6`, descripción de `search_docs` ("BM25", server.py:54) | Induce a error a cualquier agente/humano que trabaje sobre el repo | Actualizar docs al estado Postgres |
| `StatusBar.vue` consume campos inexistentes | `frontend/src/components/StatusBar.vue:25,49` usa `chroma_collections`/`chroma_total_docs`; `/status` devuelve `collections`/`scraped_articles` (api.py:179-185) | Dos tarjetas de la UI muestran siempre "—" | Alinear campos con la API |
| Chips de filtro del frontend no filtran | `SearchPanel.vue`: template itera `results`, no `filteredResults` | Filtros por fuente (wiki/SAC/Bantotal) son decorativos | Usar `filteredResults` en el template |

### Medium

| Issue | Evidencia | Impacto | Sugerencia |
|---|---|---|---|
| Config de chunking muerta | `config.py:18-19` (1000/200) vs `pg_indexer.py:24-25` (800/100) | Cambiar la config no cambia el comportamiento | Cablear o eliminar |
| Errores de DB disfrazados de 404 | `pg_collections.py:46-48` (except → None) | Una caída de Postgres parece "documento no encontrado" | Propagar error o distinguir 503 |
| API admin expone texto de excepción en 500 | exception handler global en api.py | Fuga de detalle interno (mitigado por topología) | Redactar como en el MCP público |
| Embeddings sin prefijos e5 | embeddings.py:32, pg_retriever.py:98 | Recall subóptimo potencial del modelo e5 | Evaluar `query:`/`passage:` con un set etiquetado antes de cambiar |
| Dedup débil para SAC | `normalize_wiki_url` colapsa source_keys sin ID numérico | Re-scrapes de SAC pueden duplicar artículos | Extraer ID estable de SAC |
| Rate limit in-memory | http_server.py | Inválido con >1 worker; burst ~2x en ventana | Redis antes de escalar (ya documentado) |
| Código muerto | `ScrapeForm.vue`/`ArticleList.vue` huérfanos; `structlog` sin imports | Ruido | Eliminar o cablear |

### Low

| Issue | Evidencia | Impacto |
|---|---|---|
| `cross_ref` collection y tools del stack doc no implementadas | docs/gx-bt-rag-mcp-stack.md vs server.py | Spec incompleta |
| `lru_cache` de embeddings por lote | embeddings.py:25 | Memoria acotada solo por cantidad de lotes, no por tamaño |
| `vps-produccion.patch` parcialmente stale | toca `src/vectorstore/retriever.py` (Chroma, ya no existe) | Confusión histórica |
| `GX_DEFAULT_VERSION` sin uso | config.py:50 | Legado |

---

## 18. Assessment de arquitectura

| Área | Estado | Evidencia |
|---|---|---|
| Modularidad | GOOD | Capas claras: server/http/api → vectorstore/syntax_db → pg_client; contratos estables preservados en la migración Chroma→PG (pg_retriever.py:11) |
| Separación de concerns | GOOD | Superficie pública vs admin separadas deliberadamente (http_server.py:9-11); write tools fuera del público por estructura, no por convención |
| Testabilidad | GOOD | Fixtures con schema PG descartable + guarda anti-producción (conftest.py); loaders y catálogo testeables por inyección |
| Calidad de retrieval | ACCEPTABLE | Híbrido real con fusión RRF y decisiones documentadas (config 'simple', OR en tsquery), pero score no calibrado y **sin evaluación** que lo respalde |
| Calidad de ingesta | ACCEPTABLE | Dedup por hash, reindex transaccional, gate humano para sintaxis; pero sin borrado de artículos, dedup SAC débil, chunking sin tests de calidad |
| Observabilidad | NEEDS IMPROVEMENT | Solo logs stdlib + auditoría de dominio; structlog muerto; sin métricas ni tracing |
| Evaluación | CRITICAL | Sin set de relevancia ni harness: toda decisión de ranking (RRF vs alternativas, ts_rank_cd vs BM25, prefijos e5) está sin validar — declarado en el propio SQL |
| Seguridad | GOOD (público) / NEEDS IMPROVEMENT (admin) | Público: fail-closed, testeado. Admin: sin auth + CORS `*` con credentials, protegido solo por topología |
| Configuración | ACCEPTABLE | pydantic-settings limpio, pero con campos muertos (chunking, reranker, default_version) y overrides nested dudosos |
| Escalabilidad | ACCEPTABLE | PG externo e índices en disco escalan bien; bloqueos: rate limit in-memory (single worker), modelo de embeddings por proceso |
| Mantenibilidad | NEEDS IMPROVEMENT | Docstrings excelentes que explican el *porqué* (deadlock, DO UPDATE, RRF), pero docs de alto nivel desactualizadas contradicen el código |
| Manejo de errores | ACCEPTABLE | Bien pensado en el transporte público (redacción, fail-closed); inconsistencias: 404-vs-DB-error, 500 con detalle en admin |

---

## 19. Unknowns

- **Qué agente/LLM consume el MCP en producción** y cómo construye su prompt
  final — vive fuera del repo (`.mcp.json` registra el stdio local).
- Contenido exacto de `data-corpus.tar.gz` (artefacto de deploy, no
  inspeccionado).
- Si `VITE_API_TARGET` tiene efecto real: `vite.config.js` hardcodea el target
  del proxy; la variable solo la inyecta `dev.sh` (INFERRED que el default
  manda).
- Estado real del corpus en el Postgres de producción más allá de los números
  documentados en el SQL (50.056 chunks en gx_docs): cobertura de SAC,
  frescura, duplicados por el dedup débil.
- Licencia/compliance de la documentación Bantotal scrapeada — gate abierto
  documentado en DEPLOY.md.
- Uso previsto de `pymupdf` (en requirements, sin imports): presumiblemente
  para los PDFs Bantotal pendientes de Fase 1 (INFERRED de FASE1_ALISTAMIENTO.md).

---

## 20. Próximos pasos recomendados

Ordenados por relación riesgo/esfuerzo, todos como cambios mínimos:

1. **Actualizar la documentación viva** (`CLAUDE.md` raíz, comentario de
   docker-compose, docstrings que dicen "ChromaDB", descripción "BM25" de
   `search_docs`) — barato, elimina la fuente de error más probable para
   futuros agentes.
2. **Arreglar los dos bugs vivos del frontend** (campos `chroma_*` en
   StatusBar; chips que no filtran en SearchPanel).
3. **Construir el set de relevancia etiquetado** (20-50 queries con chunks
   esperados) antes de tocar cualquier parámetro de retrieval — es la
   precondición declarada por el propio equipo para evaluar `ts_rank_cd`,
   prefijos e5, config 'spanish' y un reranker multilingüe.
4. **Distinguir error de DB de "no encontrado"** en `pg_collections.py`
   (hoy una caída de Postgres se ve como 404).
5. **Cablear o eliminar la config muerta** (chunk_size/overlap, reranker,
   default_version) y el código muerto (`structlog`, componentes Vue
   huérfanos).
6. **Decidir el borrado de artículos**: hoy no hay forma de eliminar un
   documento del corpus salvo SQL manual.
7. Antes de escalar el transporte público a más de un worker: **rate limit
   en Redis** (ya documentado como requisito en http_server.py).
