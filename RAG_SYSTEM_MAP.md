# RAG System Map — gx-rag

Documento de arquitectura reconstruido desde el código (no desde los READMEs).
Fecha de la reconstrucción: 2026-08-11. Branch: `vps-produccion`.

> **Actualización 2026-08-19** (branch `fix/hnsw-filtered-search`). Los cambios de esa
> sesión están marcados en línea. En resumen: se resolvió el ítem del `tsv` que §17
> tenía pendiente; se encontraron **dos defectos que este mapa no había detectado**
> (búsqueda vectorial filtrada que devuelve cero filas densas, §5.1; y el gate público
> ciego a la dimensión *colección*, §14); y se cerraron dos *unknowns* de §19 con
> medición. La tabla `chunks` ganó una columna `meta` (§9). Suite: 109 → **136 tests**.

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
  El `tsv` usa la config **`es_simple`** ~~(`sql/002`), no la `'simple'` que todavía
  declara el `001`~~ → **2026-08-19: el `001` también declara `es_simple`**, así que un
  install fresco nace coherente. El `002` queda como migración histórica y sus guards
  lo dejan no-op. La tabla tiene además una columna **`meta jsonb`** (`sql/003`, §9).
- **Retrieval híbrido real**: denso (e5-large, 1024 dims) + léxico (tsvector,
  `ts_rank_cd`), fusionados con **RRF k=60 en una sola query SQL**
  (`src/vectorstore/pg_retriever.py:30-59`).
  **2026-08-19**: la mitad densa devolvía **cero filas** cuando los vecinos HNSW más
  cercanos pertenecían a otra colección — *filtered vector search*, ver §5.1.
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
   - CTE `lexical`: `tsv @@ to_tsquery('es_simple', …)`, rankeado por
     `ts_rank_cd` (índice GIN), `LIMIT 30`.
   - CTE `fused`: `SUM(1/(60 + rank))` — **RRF con K=60** (línea 24),
     fusiona sin reconciliar escalas (rank-based).
   - Filtro opcional `min_score` post-fusión; `ORDER BY score DESC LIMIT
     top_k` (default 5).
4. Output por hit: `{doc_id (chunk_id), id (article_id), title, content,
   source, url, content_hash, score}` (líneas 114-127).

> ### ⚠️ 2026-08-19 — La búsqueda vectorial FILTRADA devolvía cero filas densas
>
> El HNSW hace **post-filtrado**: entrega del orden de `hnsw.ef_search` (default **40**)
> vecinos **globales** y el ejecutor aplica `WHERE collection = …` **después**. Si esos
> vecinos pertenecen a otra colección, la mitad densa queda en **cero** y el RRF
> degenera a léxico puro. Falla **silenciosa**: sin error, y sin cero resultados
> totales, porque la mitad léxica tapa el hueco.
>
> Medido (51.211 chunks, pgvector 0.8.6, `hnsw.iterative_scan=off`, query
> `'saldo contable de la cuenta'`):
>
> | | densa |
> |---|---|
> | top-40 vecinos sin filtro | 40/40 de `bt_docs` |
> | filtrando `gx_docs`, `ef_search=40` | **0 filas** |
> | filtrando `gx_docs`, `ef_search=1000` | 30 filas |
> | filtrando `gx_docs`, `iterative_scan=relaxed_order` | 30 filas, y 35 ms → 10 ms |
>
> **Fix**: `SET LOCAL hnsw.iterative_scan='relaxed_order'` dentro de una transacción
> **acotada** en `hybrid_search` (una conexión devuelta al pool con el GUC pegado se lo
> llevaría al siguiente que la tome). Test: `tests/test_retriever_tuning.py`.
>
> Afecta **1 de 4** queries de prueba: se dispara cuando la query es semánticamente
> ajena a la colección buscada. `gx_docs` (97,8% del corpus) resolvía por
> `Index Scan using chunks_embedding_hnsw`; `bt_docs` y `gx_syntax` van por
> `chunks_collection` + sort exacto y por eso **no** exhibían el bug.
>
> **Índice HNSW parcial por colección NO es la alternativa**: PostgreSQL solo usa un
> índice parcial si prueba la implicación del predicado en tiempo de **planificación**,
> y aquí la colección viaja como **parámetro**; cuando psycopg3 auto-prepara en la 5ª
> ejecución el plan se vuelve genérico y el índice parcial **se apaga solo**, en
> silencio.
>
> **Límite honesto de este fix**: prueba **correctitud mecánica** (la mitad densa dejó
> de estar vacía), **no** calidad de ranking. Sin el set de relevancia etiquetado de
> §16 no se puede afirmar que las respuestas sean mejores.

Notas documentadas en el propio código:
- El score RRF es **posicional y NO calibrado** — piso de ranking, no umbral
  de confianza (docstring líneas 90-92).
  **2026-08-19**: la descripción de `min_score` además **enseñaba un diagnóstico
  falso** — que un score ≈`0.0164` (=1/61) es *"típico de una consulta cuyo tema no
  está en la colección"*. No lo es: solo dice que **ese chunk** apareció en una sola
  mitad, y dos listas de 30 resultados excelentes que no se solapan dan el mismo
  máximo. El score alto es señal de **consenso entre las dos mitades**, no de calidad.
  Ese texto indujo el error durante el propio diagnóstico del bug de arriba; corregido.
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

> ### 2026-08-19 — La mezcla de `gx_docs`, contada y resuelta con un filtro
>
> Esa instrucción pedía al agente **mirar la url de cada hit a ojo**. Ahora hay un eje
> mecánico: `meta.origin` (`sql/004-backfill-origin.sql`) + `filters={"origin": [...]}`.
>
> Composición real, que nadie había contado:
>
> | origen | chunks | % | artículos |
> |---|---|---|---|
> | `sac` (tickets de soporte) | 28.155 | **56%** | 12.756 |
> | `gx_wiki` | 19.783 | 40% | 5.320 |
> | `wwp` (WorkWithPlus) | 2.125 | **4%** | 777 |
>
> **Más de la mitad del corpus de "documentación" son tickets de incidentes**, y la wiki
> del producto que la KB usa de verdad (WorkWithPlus) es el 4%.
>
> Efecto medido, `top_k=10` sobre `gx_docs`:
>
> ```
> query: 'como configurar el pattern en una transaccion'
>   SIN filtro      -> 10 hits, sac=10          ← CERO documentación
>   origin=gx_wiki  -> "Patterns" | "Applying Patterns"
> ```
>
> La documentación que responde la pregunta **existe y quedaba fuera del top-10**. La
> lección generaliza: **el ranking solo no alcanza cuando una fuente es la mitad del
> corpus.** Ni RRF, ni un reranker, ni los prefijos e5 arreglan eso — hace falta el eje
> de procedencia, y es más barato que cualquiera de los tres.
>
> El backfill son 50.063 + 1.122 filas en 2m40s, **sin re-embeddear** (`UPDATE` de jsonb:
> no toca `content`, `embedding` ni `content_hash`, así que la idempotencia de la ingesta
> queda intacta) e idempotente (re-aplicar da `UPDATE 0`). **Ninguna fila queda sin la
> clave** —`gx_syntax` va como `gx_catalog`— para que un filtro que no matchea signifique
> siempre "ese origen no lo tiene" y nunca "esa colección no se clasificó": dos ausencias
> distintas que desde afuera se leen igual.
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
| `tsv` | tsvector GENERATED STORED | `to_tsvector('es_simple', title \|\| ' ' \|\| content)` |
| `meta` | jsonb NOT NULL DEFAULT `'{}'` | **2026-08-19, `sql/003`.** Bolsa abierta, shape POR COLECCIÓN |

**`meta` (2026-08-19, `sql/003-chunk-meta.sql`)** — una columna jsonb y no 8 columnas
nuevas, y el motivo decisivo no es el almacenamiento: `_UPSERT_SQL` tiene placeholders
**posicionales** y `_write_chunks` arma las tuplas por posición (`bulk_loader` pasa
`doc.url` dos veces, en `source` y en `url`), así que sumar posiciones a esa cadena es
la forma más barata de escribir `module` donde va `source` sin que nada falle.

- Claves en `gx_kb_objects`: `obj_name`, `obj_type`, `chunk_kind`, `kb_name`
  (las cuatro **exigidas por un CHECK**), más `module`, `kb_hash`, `related[]`.
  `module` queda opcional a propósito: en el índice del MCP GeneXus es
  `observed:48.6` — la mitad de la KB sin placement resuelto.
- Índices: **btree de expresión** sobre `(collection, meta->>'k')` por cada clave
  filtrable, más un GIN sobre `(meta->'related')`. Medido sobre 3.140 filas: con el
  btree el filtro resuelve por `Bitmap Index Scan`; **reemplazándolo por un GIN de
  `jsonb_ops` sobre `meta` cae a `Seq Scan`** — un GIN sirve `@>`, `?`, `?|`, no la
  igualdad sobre `->>`.
- `meta` **no** entra en `tsv`: el nombre y el tipo del objeto ya van en `title`, que la
  columna generada indexa. Meterlo obligaría a reescribir las ~51k filas del corpus.
- `meta` **sí** va en el `DO UPDATE` del upsert, por el mismo motivo que `collection`:
  un conflicto de `chunk_id` significa "el mismo objeto otra vez", y reindexar con
  metadatos corregidos tiene que actualizarlos.

Invariante: el reindexado por artículo es transaccional (DELETE+INSERT en una
conexión) — el artículo nunca queda sin chunks a medias.

Segunda invariante, **no escrita hasta ahora y de la que cuelga toda la idempotencia**:
`reindex_if_changed` lee el hash con `LIMIT 1` y **sin `ORDER BY`**, o sea que trae *un
chunk cualquiera*. Es correcto solo porque `_write_chunks` escribe el **mismo**
`content_hash` en todas las filas del artículo. Si alguna vez se hasheara por chunk, la
decisión `skipped`/`reindexed` pasaría a ser **no determinista** entre corridas.

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
| PostgreSQL + pgvector (externo, DSN puerto 5433) | vector store + syntax DB | **crítica** — sin DSN nada funciona |
| HuggingFace Hub | descarga del modelo e5-large (primera vez) | alta en arranque en frío |
| wiki.genexus.com / docs.genexus.com / docs.workwithplus.com / sac.genexus.com | scraping de corpus | solo ingesta |
| Traefik (VPS, `/opt/infra/traefik/`) | TLS + routing `gxrag.wgsoft.com.co` | producción |
| Let's Encrypt (via Traefik) | certificados | producción |

> **Dónde corre ese Postgres depende del AMBIENTE, y confundirlo cuesta un backup mal
> hecho** (VERIFICADO 2026-08-19):
>
> | | Motor | Backup |
> |---|---|---|
> | **Dev (Windows)** | PostgreSQL **16 NATIVO**, `C:\Program Files\PostgreSQL\16\bin\postgres.exe`, puerto 5433 | `pg_dump` — **no hay volumen que copiar**. Y `pg_dump` no está en el PATH |
> | **VPS** | contenedor `gx-rag-pg`, imagen `pgvector/pgvector:pg16`, volumen `gx-rag-pgdata`, red `infra_gateway` | volumen o `pg_dump` |
>
> El `docker-compose.yml` describe el **VPS**, no la máquina de dev; los dos son
> válidos y el compose no está mal. Lo que faltaba era decir cuál aplica dónde.
> Extensiones medidas en dev: `vector 0.8.6`, `unaccent 1.1`, `hnsw.ef_search=40`.
> Migración obligatoria del lote 2026-08-19 y su reversión: `docs/deploy-vps.md`.

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

> ### 🔴 2026-08-19 — Ese allowlist era CIEGO a la dimensión *colección*
>
> `PUBLIC_TOOL_NAMES` cubre **qué tool** se puede invocar, y del lado *write* está bien
> cubierto. Pero **`search_docs` es pública y recibe `collection` como PARÁMETRO**,
> validado solo contra `KNOWN_COLLECTIONS`. O sea: **agregar un nombre a esa tupla
> alcanzaba para publicar una colección entera en el endpoint de internet**, sin tocar
> `http_server.py` y sin que ningún test se pusiera rojo. No existía
> `PUBLIC_COLLECTIONS`.
>
> Importa porque el caso de uso en curso es indexar los objetos de una Knowledge Base
> GeneXus **de banca** — no documentación pública.
>
> **Fix**: `NON_PUBLIC_COLLECTIONS` / `PUBLIC_COLLECTIONS` en `server.py` +
> `reject_non_public_collection()` llamado desde `guarded_dispatch`. Tres decisiones:
>
> 1. **Declarado por SUSTRACCIÓN**, no por lista blanca: el default seguro tiene que ser
>    "no se publica". Con lista blanca, agregar una colección y olvidarse la deja
>    pública; restando, la deja privada.
> 2. **La política precede a la capacidad**: `gx_kb_objects` figura como no-pública
>    *antes* de existir en `KNOWN_COLLECTIONS`, para que no haya ventana entre el commit
>    que crea la colección y el que la protege.
> 3. El mensaje de rechazo dice explícitamente **"se rechazó ANTES de consultar el
>    store"**: sin eso, un rechazo de *política* se lee como una colección vacía y el
>    agente concluye sobre el corpus a partir de una decisión de acceso.
>
> Test: `tests/test_public_collections.py` (8). Nota de despliegue: un push **no**
> activa esto en el VPS — los servicios usan `build: .`, así que hasta un
> `docker compose up -d --build` el endpoint público sigue sirviendo el código anterior.
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

> ### 2026-08-19 — Línea base medida, y por qué frenó el experimento de los prefijos e5
>
> `scripts/eval_bt_docs.py` sobre `bt_docs` (15 consultas, ground truth = títulos de los
> 33 capítulos del manual funcional):
>
> | | |
> |---|---|
> | ruteo correcto top-1 | **14/15 (93%)** |
> | ruteo correcto top-3 | **15/15 (100%)** |
>
> **El set está saturado, así que NO PUEDE medir la mejora de los prefijos e5.** El
> único margen es el caso que hoy falla en top-1 (`lineas de credito` → devuelve
> *Mesa de Dinero*), y **un caso no distingue señal de ruido**. Contra eso, el costo
> del re-embed es 1.122 chunks en `bt_docs` (~40 min) y **50.063 en `gx_docs` (~28 h
> a 2 s/chunk medidos)**. Decidir con este instrumento sería fe, no medición.
>
> **Decisión: no se re-embeddea.** La precondición no es el cómputo, es un set con
> resolución: más consultas, y sobre todo **consultas que hoy fallen**. Un set que ya
> acierta el 100% en top-3 no puede probar nada sobre ranking.
>
> Hallazgo lateral del mismo eval, que los prefijos **no** arreglarían: el embedding es
> sensible a las tildes. `'prestamos'` vs `'préstamos'` da coseno **0,9355** (y
> `'garantias'` 0,9727), así que aunque el `sql/002` normalizó la mitad **léxica** con
> `unaccent`, el ranking final sigue dependiendo de las tildes por el lado **denso**.
> Eso es un problema real y medido, con su propia causa.
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

Agregados 2026-08-19 (suite total: **136 passed**):

- `test_retriever_tuning.py` (3): que `hybrid_search` aplique
  `hnsw.iterative_scan` **antes** de la búsqueda y **dentro** de una transacción.
  Con un pool falso, así que corre sin Postgres. Declara explícitamente que **no**
  prueba recall — reproducir el under-return exigiría suficientes filas para que el
  planner elija el HNSW sobre un seq scan, y el resultado quedaría colgado de una
  decisión del planner en vez de del código.
- `test_fresh_schema.py` (2): aplica **solo el `sql/001`** en un schema aparte. Es el
  punto ciego que la fixture `pg_schema` no puede cubrir, porque concatena todos los
  `sql/0*.sql` y por construcción no puede detectar que el 001 solo sea incorrecto.
- `test_public_collections.py` (8): el gate de colección, incluido que un rechazo de
  política **no** se lea como corpus vacío.
- `test_chunk_meta.py` (9): el `CHECK` de identidad de `meta` por colección, que
  `module` es opcional, y que los índices de expresión existen.

Son tests de **correctitud mecánica y contratos**, no de calidad de ranking. La
distinción se volvió concreta el 2026-08-19: el fix de §5.1 prueba que la mitad densa
dejó de estar vacía, y **eso no es lo mismo** que probar que las respuestas mejoraron.
Sin el set de relevancia de esta sección, esa segunda afirmación no se puede hacer.

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

### Resuelto 2026-08-19 (branch `fix/hnsw-filtered-search`)

Los tres con test que **falla sin el fix** (verificado por `git stash` + restauración
comparada), como pide `CLAUDE.md`. Suite 109 → **136 passed**.

| Issue | Era | Detalle |
|---|---|---|
| Búsqueda vectorial **filtrada** devolvía cero filas densas | post-filtrado del HNSW con `ef_search=40` y `iterative_scan=off` | §5.1. `SET LOCAL hnsw.iterative_scan='relaxed_order'` en transacción acotada. **No estaba en este mapa** |
| El gate público era ciego a la dimensión **colección** | `search_docs` es pública y `collection` es un parámetro libre | §14. `PUBLIC_COLLECTIONS` por sustracción. **No estaba en este mapa** |
| `sql/001` con `tsv` en `'simple'` | ver la tabla de abajo | ítem que este mapa **sí** tenía anotado, con la sugerencia correcta |

Y un cuarto, de la clase que este repo persigue: la descripción de `min_score`
**enseñaba un diagnóstico falso** sobre el score `0.0164` (§5.1). No era una
imprecisión de redacción — indujo el error durante el diagnóstico del primer ítem de
esta tabla, con el motor delante.

### Nueva, sin resolver

| Issue | Evidencia | Impacto | Sugerencia |
|---|---|---|---|
| ~~`sql/001` declara `tsv` con config `'simple'` y el retriever consulta `'es_simple'`~~ | ~~`001:50-52` vs `pg_retriever.py:46`~~ | ~~un install fresco que corra **solo** el 001 revienta o pierde la mitad léxica del RRF~~ | ✅ **RESUELTO 2026-08-19**: el `001` crea `unaccent` + la config `es_simple` antes del `CREATE TABLE` y declara `tsv` con `es_simple`. El `002` queda no-op por sus guards. Test: `tests/test_fresh_schema.py` (2), que aplica **solo el 001** — el punto ciego que la fixture `pg_schema` no podía cubrir porque concatena todos los `sql/0*.sql` |
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
| Calidad de retrieval | ACCEPTABLE | Híbrido real con fusión RRF y decisiones documentadas (config `es_simple`, OR en tsquery), pero score no calibrado y **sin evaluación** que lo respalde. **2026-08-19**: este assessment no detectó que la búsqueda **filtrada** devolvía cero filas densas (§5.1) — se lo había leído como "calidad sin validar", no como defecto funcional. Lección: `ACCEPTABLE` sin evaluación no distingue "no medido" de "roto" |
| Calidad de ingesta | ACCEPTABLE | Dedup por hash, reindex transaccional, gate humano para sintaxis; pero sin borrado de artículos, dedup SAC débil, chunking sin tests de calidad |
| Observabilidad | NEEDS IMPROVEMENT | Solo logs stdlib + auditoría de dominio; structlog muerto; sin métricas ni tracing |
| Evaluación | CRITICAL | Sin set de relevancia ni harness: toda decisión de ranking (RRF vs alternativas, ts_rank_cd vs BM25, prefijos e5) está sin validar — declarado en el propio SQL |
| Seguridad | GOOD (público) / NEEDS IMPROVEMENT (admin) | Público: fail-closed, testeado. Admin: sin auth + CORS `*` con credentials, protegido solo por topología. **2026-08-19**: el `GOOD` valía para la dimensión *tool* y era **ciego a la dimensión colección** — `search_docs` es pública y recibe `collection` como parámetro, así que un string en `KNOWN_COLLECTIONS` bastaba para publicar contenido privado (§14). Lección: una allowlist sobre el *verbo* no protege el *objeto* |
| Configuración | ACCEPTABLE | pydantic-settings limpio, pero con campos muertos (chunking, reranker, default_version) y overrides nested dudosos |
| Escalabilidad | ACCEPTABLE | PG externo e índices en disco escalan bien; bloqueos: rate limit in-memory (single worker), modelo de embeddings por proceso |
| Mantenibilidad | NEEDS IMPROVEMENT | Docstrings excelentes que explican el *porqué* (deadlock, DO UPDATE, RRF), pero docs de alto nivel desactualizadas contradicen el código |
| Manejo de errores | ACCEPTABLE | Bien pensado en el transporte público (redacción, fail-closed); inconsistencias: 404-vs-DB-error, 500 con detalle en admin |

---

## 19. Unknowns

- **Qué agente/LLM consume el MCP en producción** y cómo construye su prompt
  final — vive fuera del repo (`.mcp.json` registra el stdio local).
- ~~Contenido exacto de `data-corpus.tar.gz` (artefacto de deploy, no
  inspeccionado).~~ → **RESUELTO (VERIFIED, 2026-08-19)**: 289 MB, 1.041 entradas.
  Contiene el store **ChromaDB viejo** (`chroma/`, con su `chroma.sqlite3`),
  `syntax.db` y `scraped_sac/`. **NO contiene los dumps que produjeron los 50.063
  chunks de `gx_docs`.** Consecuencia operativa: las fuentes de `gx_docs` **no están
  en disco** (`data/scraped/` tiene 11 artículos), así que un re-embed del corpus
  —por ejemplo para probar los prefijos e5 de §17— solo puede leer la columna
  `chunks.content`, que es `text NOT NULL` y por lo tanto es su propia fuente. Y no
  puede pasar por el pipeline de ingesta: `reindex_if_changed` compara
  `content_hash`, que no cambia al cambiar el modelo de embeddings, así que
  devolvería `skipped` en los 51.211 chunks.
- ~~Si `VITE_API_TARGET` tiene efecto real~~ → **RESUELTO (VERIFIED, 2026-08-11):
  NO tenía ninguno.** `vite.config.js` hardcodeaba `http://gx-rag-api:8000`, un
  hostname que solo existe dentro de la red de Docker — donde este proxy **no se
  usa**, porque ahí el frontend se sirve como estáticos por nginx. Resultado: en
  `npm run dev` (el único escenario donde el proxy actúa) TODAS las llamadas
  devolvían 500, incluida `/api/health`, que ni toca la base. Corregido: el
  default es `127.0.0.1:8000` y la variable ahora se lee de verdad.
- Estado real del corpus en el Postgres de producción: **parcialmente resuelto
  (VERIFIED, 2026-08-19)** — `SELECT collection, count(*) FROM chunks GROUP BY 1`
  da **51.211** chunks: `gx_docs` 50.063 · `bt_docs` 1.122 · `gx_syntax` 26 ·
  `bt_apis` 0. Extensiones: `vector 0.8.6`, `unaccent 1.1`. `hnsw.ef_search=40`,
  `hnsw.iterative_scan=off` (era el default; ver §5.1). Índices: HNSW 383 MB,
  GIN 19 MB, y **0 embeddings NULL**. Sigue **UNKNOWN**: cobertura de SAC, frescura
  y duplicados por el dedup débil — eso requiere inspección por artículo, no conteos.
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



[TASK NOTIFICATION]
ID: annoyed-olive-swallow
Status: complete
Agent: All research complete. One imp...

Result:

All research complete. One important correction up front: **the catalog has 26 tools, not 28** — verified with a direct count of `Name = "gx_` declarations in `ToolSchemas.cs` (rg output: 26 matches, all listed in section 8). Full report below.

---

# Gateway MCP Protocol Implementation — Migration Research Report

## 1. JSON-RPC methods handled & protocol version

All dispatch lives in `McpServer.HandleAsync` (`GxGenie.Gateway\McpServer.cs:73-130`), a hand-rolled line-delimited JSON-RPC 2.0 loop (`RunAsync`, lines 37-71: `ReadLineAsync` → `JsonNode.Parse` → dispatch → `WriteLineAsync`).

| Method | Handling | Reference |
| --- | --- | --- |
| `initialize` | Returns static `BuildInitializeResult()` | McpServer.cs:90-92, 132-144 |
| `initialized` **and** `notifications/initialized` | No-op, no response | McpServer.cs:94-97 |
| `tools/list` | Returns full catalog, ignores `params.cursor` (no pagination) | McpServer.cs:99-101, 146-159 |
| `tools/call` | Dispatches to Worker via `WorkerProxy` | McpServer.cs:103-106, 161-215 |
| `ping` | Returns empty `{}` result | McpServer.cs:108-110 |
| `shutdown` | Returns empty `{}` result (non-MCP, LSP-style extra) | McpServer.cs:112-114 |
| Unknown method **with id** | JSON-RPC error `-32601 Method not found` | McpServer.cs:116-118 |
| Unknown **notification** | Logged to stderr, ignored | McpServer.cs:119-120 |

Protocol version: **`"2024-11-05"`**, hardcoded constant (McpServer.cs:14). **There is no actual negotiation** — `BuildInitializeResult()` is parameterless and always echoes `"2024-11-05"` regardless of what the client sent; `params.protocolVersion` is never read. The doc comment (McpServer.cs:10) explicitly targets spec 2024-11-05.

Other protocol-level behaviors:

- **Notification detection**: `bool isNotification = idNode is null` (McpServer.cs:78) — notifications never get a response; responses echo the request `id` via `DeepClone` (lines 234, 242), so both numeric and string ids work.
- **JSON-RPC errors used**: `-32700` parse error (line 59), `-32600` invalid request / missing method (lines 64, 82), `-32601` method not found (line 118), `-32603` internal error via catch-all (line 128).
- **Not implemented**: `notifications/cancelled` (falls into "unknown notification" ignore path), progress, `logging/*`, resources, prompts, sampling, roots. No `instructions` field in initialize result.
- Edge-case quirk: `req["method"]?.GetValue<string>()` (line 76) runs *outside* the try block — a non-string `method` value crashes the process (FATAL in Program.cs:58-61) instead of returning `-32600`.
- **No input validation** of `tools/call` arguments against `inputSchema` — validation is delegated entirely to the Worker.

## 2. Exact `initialize` response shape

From `BuildInitializeResult()` (McpServer.cs:132-144) with constants at lines 14-16:

```json
{
  "protocolVersion": "2024-11-05",
  "capabilities": {
    "tools": { "listChanged": false }
  },
  "serverInfo": {
    "name": "gxmcp",
    "version": "1.4.0"
  }
}
```

- Server name: **`gxmcp`** (McpServer.cs:15) — note: *not* "GxGenie".
- Server version: **`1.4.0`** (McpServer.cs:16), hardcoded, not from assembly metadata.
- Only capability advertised is `tools.listChanged = false`. No resources/prompts/logging/sampling/roots capabilities.

## 3. Tool schema representation & tools/call dispatch

### Schema representation (ToolSchemas.cs)

**Not raw JSON strings and not attributed C# types.** Schemas are `System.Text.Json.Nodes.JsonObject` trees **built programmatically in code** at static-init time, inside a static `List<ToolDef>` (ToolSchemas.cs:150-678). The catalog entry type (ToolSchemas.cs:13-19):

```csharp
public sealed class ToolDef
{
    public string Name { get; init; } = "";
    public string Description { get; init; } = "";
    public JsonNode InputSchema { get; init; } = new JsonObject();
    public string WorkerTool { get; init; } = "";
}
```

Key points:

- Builder helpers: `Obj(...)` (line 21), `Prop(type, description, extra)` (line 28 — merges `enum`/`default`/`pattern`/`minimum`/`maximum` siblings into the property), `EnumOf(...)` (line 42), `ModuleProp()` (line 55 — rebuilt per call site because a `JsonNode` can't have two parents).
- Descriptions/params are **100% static and hand-written** (Spanish prose), with two dynamic touches: the shared `ObjectTypes` string array (lines 143-148, 20 GX types) feeds several `enum`s, and `gx_create_transaction` uses a dedicated method `CreateTransactionInputSchema()` (lines 66-140) with recursive `$defs`/`$ref` (`#/$defs/attribute`, `#/$defs/level`).
- `Name` → `WorkerTool` is currently **1:1 identical for all 26 tools** (e.g. lines 154-155), but the indirection exists and the SDK migration must preserve it.
- `FindByName` is ordinal exact match (lines 680-681).
- No `outputSchema`, no `annotations`, no `_meta` on any tool (doc comment lines 8-9 says outputSchema is deliberately omitted).
- `tools/list` emits `{ name, description, inputSchema: <DeepClone> }` per tool (McpServer.cs:146-159).

### Exact JSON shape of one full tool (as serialized by `tools/list`)

`gx_list_objects` (ToolSchemas.cs:163-186):

```json
{
  "name": "gx_list_objects",
  "description": "Lista objetos de la KB filtrando por tipo y/o substring del nombre. Use type='All' para listar todos los tipos top-level.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "description": "Tipo de objeto a listar. 'All' devuelve todos los tipos top-level.",
        "enum": ["All","Transaction","Procedure","WebPanel","DataProvider","DataSelector","DataView","BusinessComponent","SDT","Domain","Table","Theme","Query","Report","Menu","WorkPanel","Image","ExternalObject","Group","BPDiagram"],
        "default": "All"
      },
      "filter": {
        "type": "string",
        "description": "Substring case-insensitive a buscar en el nombre del objeto (opcional)."
      },
      "limit": {
        "type": "integer",
        "description": "Máximo de filas a devolver (default 1000).",
        "minimum": 1,
        "maximum": 100000,
        "default": 1000
      }
    },
    "additionalProperties": false
  }
}
```

(The zero-arg tools `gx_kb_info` / `gx_list_kbs` use `"properties": {}` + `"additionalProperties": false`, no `"required"` — ToolSchemas.cs:157-161, 672-676.)

### tools/call dispatch & error propagation (McpServer.cs:161-228)

Flow:

1. `params` must be a JSON object with non-empty `name` → otherwise **tool error** (not JSON-RPC error), lines 163-168.
2. Unknown tool name → tool error `"Unknown tool: {name}"` (lines 170-172).
3. `arguments` (if present) round-tripped `JsonNode → ToJsonString → JsonDocument → JsonElement.Clone()` (lines 174-180). Note: non-object `arguments` are accepted here but silently dropped by WorkerProxy (see below).
4. `await _worker.CallAsync(tool.WorkerTool, arguments, ct)` (line 185). Any exception → tool error `"Worker call failed: {ExType}: {msg}"` (lines 187-190).

**Worker response contract** (consumed at McpServer.cs:192-201): `{ "success": bool, "error"?: string, "data"?: any }`.

- `success != true` → tool error `"Worker error: {error}"` (or `"(no error message)"`).
- Success → `data` re-serialized **pretty-printed** (`WriteIndented = true`, line 200) into a single text content item; missing `data` → literal string `"(no data)"`.

Success envelope (lines 203-214) / error envelope (`ToolError`, lines 217-228):

```json
{ "content": [ { "type": "text", "text": "<pretty JSON of data>" } ], "isError": false }
{ "content": [ { "type": "text", "text": "<message>" } ], "isError": true }
```

**Critical design fact for migration: worker/tool failures NEVER become JSON-RPC errors.** They always come back as a normal `result` with `isError: true` (MCP tool error). JSON-RPC `error` responses are reserved for protocol faults (parse/dispatch/internal, section 1). The SDK migration must preserve this split — e.g. by catching exceptions inside the tool handler and returning `CallToolResult` with `IsError = true` rather than letting them surface as JSON-RPC errors.

## 4. Worker lifecycle (WorkerProxy.cs)

- **Long-lived child process, lazily spawned** on first `CallAsync` via `EnsureStarted()` (WorkerProxy.cs:49, 89-127). Rationale in remarks (lines 12-16): restarting per call would re-attach LocalDB every time.
- `ProcessStartInfo` (lines 97-108): `UseShellExecute=false`, all three stdio streams redirected, `CreateNoWindow=true`, `StandardOutputEncoding`/`StandardErrorEncoding = UTF8`, `WorkingDirectory` = Gateway's own cwd (inherited from Claude Code, for per-KB `.gxw` auto-detect).
- **Env vars passed** (lines 109-115):
  - `GXGENIE_CWD` = user cwd, **always**.
  - `GXGENIE_CONFIG` = config path, **only if explicit** (`--config` arg or `GXGENIE_CONFIG` env — `GatewayConfig.IsExplicitConfig`, GatewayConfig.cs:30, 44-47). Fallback configs (next to exe / repo root) are *not* propagated; the Worker does its own resolution.
- **Concurrency**: `SemaphoreSlim(1,1)` serializes all calls (lines 23, 46, 85) — concurrent `tools/call`s queue, they don't interleave. The SDK migration must keep single-flight semantics or switch the Worker IPC.
- **IPC request shape** (one JSON line to worker stdin, lines 52-62):

  ```json
  { "tool": "<WorkerTool>", "id": "<Guid:N 32 hex>", "params": { ... } }
  ```

  `params` included **only if** arguments are a JSON object (line 57). The `id` is generated per call and is *not* correlated on response — responses are matched positionally (one request → next line).
- **IPC response**: exactly one line from worker stdout, parsed to `JsonElement` (lines 67-81). Shape: `{ success, error?, data? }` (see §3).
- **Timeout**: linked CTS `CancelAfter(WorkerTimeoutSeconds)` (lines 64-65); default **120 s** (GatewayConfig.cs:20, overridable via `config.json` `Worker.TimeoutSeconds`, GatewayConfig.cs:66). Timeout → `TimeoutException` with drained stderr embedded (line 75).
- **Crash handling**: `ReadLine` returning `null` → `InvalidOperationException("Worker exited unexpectedly. Stderr:...")` (lines 78-79). **Restart is implicit/lazy**: `EnsureStarted` checks `_process is { HasExited: false }` (line 91), so the *next* call after a crash spawns a fresh process. The failed call itself is not retried — it surfaces as a tool error. `EnableRaisingEvents = true` is set (line 117) but no `Exited` handler is attached; the dead `Process` object is never disposed until gateway shutdown (minor leak).
- **stderr handling**: async `BeginErrorReadLine` + `ErrorDataReceived` appending to a lock-guarded `StringBuilder` (lines 24, 118-122, 125); only ever surfaced inside timeout/crash exception messages via `DrainStderr()` (lines 129-136). Never forwarded to MCP `logging` notifications or anywhere else.
- **Dispose** (lines 138-159): close worker stdin → `WaitForExit(2000)` → `Kill(entireProcessTree: true)` fallback → dispose.

Worker exe resolution (`GatewayConfig.Load` + `AutoDetectWorkerExe`, GatewayConfig.cs:32-110): `config.json` `Worker.ExecutablePath` → env `GXGENIE_WORKER_EXE` → sibling of gateway dir → repo layout `..\..\..\..\GxGenie.Worker\bin\{Release,Debug}\net8.0\`. **No `ProjectReference`** — the Worker is purely an external process.

## 5. Logging & other protocol-relevant behavior

- **Logging = stderr only**, prefix `[gxmcp.gateway]` (`Program.cs:37`); no file logging anywhere. Stdout is reserved for JSON-RPC lines; `Console.OutputEncoding`/`InputEncoding` forced to UTF-8 (`Program.cs:6-7`).
- Log points: startup worker/config/cwd (`Program.cs:39-41`), `"MCP server ready (protocol 2024-11-05, 26 tools)"` (McpServer.cs:39), `"MCP server exiting (stdin closed)"` (McpServer.cs:70), unknown notifications (McpServer.cs:120), handler errors (McpServer.cs:126), `FATAL` + exit 1 on unhandled (Program.cs:58-61).
- **Framing**: newline-delimited JSON (`ReadLineAsync`/`WriteLineAsync` + `FlushAsync`, McpServer.cs:45, 250-255); compact serialization, nulls omitted (`JsonOpts`, McpServer.cs:23-27).
- **Cancellation**: Ctrl+C wired to a CTS (`Program.cs:46-51`), token flows into `ReadLineAsync` and worker calls. **No MCP `notifications/cancelled` support** — it's ignored as an unknown notification. No in-flight request is cancelled by the client.
- **No progress notifications, no timeouts at the MCP layer** (only the Worker-level 120 s timeout).
- **Shutdown**: stdin EOF ends the loop → exit 0 (McpServer.cs:49, 70; Program.cs:56). `shutdown` method returns `{}` but does not stop the loop.
- Exit codes: 0 normal, 1 fatal, 2 config load failure (Program.cs:34, 56, 61).

## 6. Target framework & NuGet dependencies (GxGenie.Gateway.csproj)

- `net8.0`, `OutputType=Exe`, `Nullable=enable`, `ImplicitUsings=enable`, `LangVersion=latest`, `InvariantGlobalization=true` (csproj lines 3-14).
- **Zero `<PackageReference>` items. Zero `<ProjectReference>` items.** The entire Gateway runs on BCL only (`System.Text.Json`). Everything — JSON-RPC framing, MCP protocol, schema catalog, process management — is hand-rolled. This is a green-field insertion point for `ModelContextProtocol` + `Microsoft.Extensions.Hosting` packages.

## 7. `C:\Proyectos\GxGenie` existence

**`Test-Path 'C:\Proyectos\GxGenie'` → `False`.** The directory does not exist on this machine, confirming the AGENTS.md gotcha: both test scripts are unrunnable here without edits. Hardcoded references:

- `test-mcp.ps1:5` (exe path), `:8` (`GXGENIE_CONFIG = C:\Proyectos\GxGenie\config.json`)
- `test-mcp-write.ps1:5` (exe), `:8` (`config.test.json`), `:76` (XPZ output into `C:\Proyectos\GxGenie\GxGenie.Worker\probes\`)

What the scripts do (relevant to migration regression testing):

- **test-mcp.ps1** (read-only smoke): sends `initialize` (protocolVersion `2024-11-05`, empty capabilities, clientInfo `test/0`), `tools/list` (prints count + names), `tools/call gx_kb_info`, `tools/call gx_list_objects {type=Transaction, filter=BOrden, limit=3}`; parses `result.content[0].text` as JSON and prints fields (`kbName`, `kbVersion`, `totalEntities`, `models`, `objectCounts`, `count`, `items`); closes stdin, waits 5 s, prints exit code + stderr. **No hard assertions** beyond "exe exists".
- **test-mcp-write.ps1** (write roundtrip, needs `config.test.json`): initialize → tools/list → `gx_kb_info` → `gx_create_procedure` (random `MCPGwTest_<n>`; **throws if `result.isError`**, checks `payload.backup_path`) → `gx_list_objects` (**throws if `count < 1`**, reads `items[0].id`) → `gx_read_object` (**throws unless `parts.source` contains "via gateway"**) → `gx_export_xpz` (**throws unless `payload.success`**, reads `bytes`/`output_path`). This script *does* assert, and its assertions depend on the exact MCP result shape (`result.isError`, `result.content[0].text` being parseable JSON) — i.e. the `isError`/text-content contract from §3 is load-bearing.

## 8. The tool list — correction: **26 tools, not 28**

Verified by counting `Name = "gx_` declarations in `ToolSchemas.cs` (rg: 26 matches). There is no other file contributing tools (`ToolSchemas.All` is the single source, and `McpServer` only reads it). If you expected 28, that number doesn't match the code — the complete list, in catalog order:

| # | Tool name | Line | Category (per source comments) |
| --- | --- | --- | --- |
| 1 | `gx_kb_info` | 154 | Read |
| 2 | `gx_list_objects` | 165 | Read |
| 3 | `gx_read_object` | 189 | Read |
| 4 | `gx_search` | 208 | Read |
| 5 | `gx_list_attributes` | 238 | Read |
| 6 | `gx_export_xpz` | 256 | Phase 3 write |
| 7 | `gx_import_xpz` | 278 | Phase 3 write |
| 8 | `gx_build_object` | 300 | Phase 3 write (AllowBuild) |
| 9 | `gx_delete_object` | 316 | Phase 3 write |
| 10 | `gx_create_procedure` | 337 | Phase 3 write |
| 11 | `gx_create_transaction` | 358 | Phase 3 write |
| 12 | `gx_update_object_code` | 366 | Phase 3 write |
| 13 | `gx_get_structure` | 394 | Phase B1 structured read |
| 14 | `gx_get_layout` | 414 | Phase B1 structured read |
| 15 | `gx_get_variables` | 434 | Phase B1 structured read |
| 16 | `gx_get_unused_variables` | 454 | Phase B1 structured read |
| 17 | `gx_add_attribute` | 476 | Phase B2 granular writes |
| 18 | `gx_remove_attribute` | 514 | Phase B2 granular writes |
| 19 | `gx_remove_variable` | 531 | Phase B2 granular writes |
| 20 | `gx_set_attribute_property` | 550 | Phase B2 granular writes |
| 21 | `gx_set_control_property` | 569 | Phase B3 layout writes |
| 22 | `gx_remove_control` | 590 | Phase B3 layout writes |
| 23 | `gx_add_control` | 606 | Phase B3 layout writes |
| 24 | `gx_list_object_parts` | 632 | Phase B3 discovery |
| 25 | `gx_switch_kb` | 654 | Phase 4 multi-KB |
| 26 | `gx_list_kbs` | 669 | Phase 4 multi-KB |

---

### Migration-critical summary

1. **Protocol surface is tiny**: initialize (no real version negotiation), tools/list, tools/call, ping, plus notification tolerance. The official SDK covers all of it; the only behaviors to deliberately preserve are the `serverInfo` identity (`gxmcp` / `1.4.0`), the `isError:true`-not-JSON-RPC-error contract for worker failures, pretty-printed `data` inside a single text content item, and the absence of `outputSchema`.
2. **The Worker IPC is orthogonal to MCP** — `WorkerProxy` can survive the migration almost intact; the SDK's tool handlers just replace `HandleToolCallAsync`. Keep the `SemaphoreSlim` serialization, lazy restart, 120 s timeout, and stderr capture.
3. **Schemas are code-built `JsonNode` trees with rich constraints** (`enum`, `pattern`, `minimum/maximum`, `default`, recursive `$defs/$ref` in `gx_create_transaction`). Whatever SDK mapping you choose must not lose those constraints or the Spanish descriptions.
4. **Zero existing NuGet deps** means no version conflicts, but also note `LangVersion=latest` + net8.0 — the `ModelContextProtocol` SDK targets net8.0, so that's compatible.
5. Test scripts are **not runnable on this machine** (`C:\Proyectos\GxGenie` absent) and hardcode paths at test-mcp.ps1:5,8 and test-mcp-write.ps1:5,8,76 — plan to parameterize them as part of the migration if you want regression coverage.
