# Auditoría del repo — 2026-10-05

Revisión de todo el proyecto en cuatro franjas (retrieval, MCP/API/seguridad, ingesta,
infraestructura/tests/docs), hecha por cuatro agentes de solo lectura y cruzada después.
**Lo marcado ✅ lo verifiqué yo contra el código o la base viva**; el resto viene con
archivo:línea del revisor y no lo contradije, pero no lo re-medí.

Estado medido hoy: 52.636 chunks (`bt_docs` 2.580 / 66 artículos), catálogo `gx_syntax`
**765 entradas, 706 con `version_since`, 96 `object_type`**, suite 417 tests recolectados.

## Cómo leer esto

El criterio que ordena las prioridades es el del propio repo: **la descripción de una tool y
su respuesta son el prompt del agente consumidor**. Un bug que hace que una tool afirme algo
falso pesa más que uno que solo cuesta CPU. Y una "oportunidad" solo entra si nombra el
problema medido que resuelve: nada por ser tendencia.

---

## 1. Errores — los que hacen mentir a una tool (arreglar primero)

| # | Hallazgo | Dónde | Evidencia |
|---|---|---|---|
| 1 ✅ | **`verify_syntax` y `list_methods` describen `version` como ignorado, y el código sí discrimina.** Un agente con `version=gx16` recibe INVÁLIDO/PISTA que la descripción le dijo que no podía existir | `src/server.py:298-307`, `:337-343` vs `src/syntax_db/gx_repository.py:349-389` | 706 de 765 entradas tienen `version_since`. `tests/test_interface_contract.py:93-103` fija el texto viejo |
| 2 ✅ | **`list_methods` declara 5 `object_type` (Method, Event, Command, Rule, Property); hay 96** (`DateTime` 33, `BusinessComponent` 24, `Date` 18…). El agente nunca intenta `DateTime` porque la tool le dice que no existe | `src/server.py:321-335` | `tests/test_interface_contract.py:106-122` protege la lista obsoleta |
| 3 ✅ | **`verify_syntax` da VÁLIDO por match débil**: `_lookup_entry` acepta `LIKE '% n %'`, así que `Component` matchea `Refresh Web Component command` y sale VÁLIDO con "Afírmalo" | `src/syntax_db/gx_repository.py:125-139` | Demostrado sobre `_lookup_entry`, la rama que `verify_syntax:338` consulta antes de la similitud |
| 4 | **Reranker cableado con modelo English-only y fallback silencioso.** `rerank=True` corre `ms-marco-MiniLM-L-6-v2` sobre corpus en español; si falla la carga devuelve el orden RRF sin avisar; `min_score` filtra sobre RRF pero el `score` devuelto es el del cross-encoder | `src/vectorstore/reranker.py:64-80`, `src/server.py:1096-1110`, `src/config.py:17` | `RAG_SYSTEM_MAP.md:337-346` y CLAUDE.md dicen "no usado": stale desde `6819547` |
| 5 | **`get_article` "no encontrado" no distingue** id equivocado, colección equivocada ni colección vacía (a diferencia de `get_document`) | `src/server.py:1179` vs `:1145-1148` | — |
| 6 | **Veredicto en repr de Python**, no JSON (`{'verdict': 'VÁLIDO', …}`) | `src/server.py:1203` | — |

## 2. Errores — rompen un flujo o la integridad del store

| # | Hallazgo | Dónde | Evidencia |
|---|---|---|---|
| 7 ✅ | **`scripts/ingest_local.py` no arrancaba**: importaba `src.vectorstore.indexer` (no existe). Era el único camino al manifiesto. **Arreglado hoy** con `tests/test_ingest_local.py` | `scripts/ingest_local.py:26` | `ModuleNotFoundError` en `--help` |
| 8 | **Mismo bug en el harness de evaluación**: `scripts/eval_retrieval.py:37` importa `src.vectorstore.retriever`, `scripts/ab_e5_prefix.py:31` importa `chroma_client` y `:140` llama `embed_passages` (es `embed_passage`). `eval/baseline.json` y `post-r01.json` son idénticos y de otra máquina | `scripts/eval_retrieval.py`, `scripts/ab_e5_prefix.py` | El único eval vivo es `scripts/eval_corpus.py` |
| 9 ✅ | **`emb_profile` no viaja en el upsert**: `pg_indexer.py` no lo escribe ni en INSERT ni en DO UPDATE; la columna queda en el DEFAULT `'raw'`. Hoy inofensivo (100 % `raw`), pero **bloquea la migración a `e5v1`**: apenas alguien migre, la columna miente y `reembed.py --check` deja de servir | `src/vectorstore/pg_indexer.py:35-51`, `sql/005-emb-profile.sql:31` | `rg emb_profile src/vectorstore/pg_indexer.py` → vacío |
| 10 | **Tres identidades para un mismo archivo local** (extiende el gotcha dump/URL): manifiesto → `file:///…` · `ingest_pdf --index` → `manual/{stem}` · `ingest_video` → `video/{stem}`. El mismo documento por dos scripts queda duplicado. Visto hoy: el video del piloto es `video/curso-…` y el de hoy `file:///F:/ProyectosClaude/…` (ruta absoluta de esta máquina) | `local_loader.py:149,186`, `ingest_pdf.py:115`, `ingest_video.py:156` | Además `_markdown_title` ignora el frontmatter: titula con el stem (`local_loader.py:169-176`) |
| 11 | **Video sin idempotencia**: `index_article` con `content_hash=""`, sin skip y con huérfanos si el doc se acorta | `ingest_video.py:155-164`, `batch_ingest_videos.py:112-124`, `ingestor.py:49-61` | `pg_indexer.py:160-163` lo advierte |
| 12 | **Metadatos que nunca se corrigen**: `reindex_if_changed` devuelve `skipped` por hash del contenido antes de llegar al `meta = EXCLUDED.meta`; `chunk_prefix` y `extra_meta` no entran al hash. No existe `--force` | `pg_indexer.py:210`, `bulk_loader.py:301` | Solo `kb_fichas` lo resuelve con `FICHA_SCHEMA_VERSION` |
| 13 | **Lock-out de todos los clientes detrás de Traefik**: `_client_ip` ve el peer TCP (una sola IP) y el throttle corre antes del auth: 10 fallos de cualquiera dan 429 a todos por 60 s | `src/http_server.py:91-95`, `:228` | Falta `--proxy-headers --forwarded-allow-ips` o leer `X-Forwarded-For` solo desde Traefik |
| 14 | **Palancas de cómputo con clave válida**: `top_k` y `snippet_chars` sin tope; `rerank=True` carga un CrossEncoder; el rate limit cuenta requests, no costo | `src/server.py:1086-1087,1096` | — |
| 15 | **`ValueError` genérico sale sin redactar al público** (psycopg/embeddings también lanzan ValueError) y la Admin API devuelve `str(exc)` en 500/502 | `src/server.py:1121-1126`, `src/api.py:117,496` | `tests/test_api.py:94,119` lo fijan como contrato |
| 16 | **Auth duplicada**: `http_server.py` reimplementa `_bearer_token/_is_authorized/_client_ip` que viven en `src/auth.py`, cuyo docstring pide "una sola copia" | `src/http_server.py:74-95` vs `src/auth.py:13-38` | — |
| 17 | **La Admin API salta los gates del MCP**: `/search` no valida colección ni exige `kb_name`; `/verify` sin `_VERDICT_GUIDANCE`; `/discover` roto (URL con mojibake, `query` sin encode) | `src/api.py:382-455`, `:484` | — |
| 18 | **`bench_retrieval.py` no mide producción**: corre `_SEARCH_SQL` sin `_TUNING_SQL` y usa `emb.embed` en vez de `embed_query` | `scripts/bench_retrieval.py:107,167-169,226` | Toda re-medición posterior al 2026-08-19 compara planes distintos |
| 19 | **`--transcript` con ruta mal escrita cae a Whisper en silencio** (horas de CPU por un typo) | `src/ingestion/video_pipeline.py:671-677` | — |
| 20 | **Dos extractores de PDF que divergen**: `pdf_pipeline.py` usa `find_tables` (que `bt_pdf_extract.py:26-28` descartó por alucinar grillas) y escribe `genre="manual_tecnico"`, fuera del par `guide|reference` | `src/ingestion/pdf_pipeline.py`, `scripts/ingest_pdf.py:122` | — |
| 21 | **`classify_genre` con falsos positivos**: "Smart Devices Tutorial" y "Release Notes" → `reference`; `"event"` matchea *prevent*, `"tip"` matchea *multiple* | `scripts/scrape_wiki.py:113-124` | Solo afecta `add_from_url` |
| 22 | **`_to_tsquery` deja pasar `_`**: `foo_bar` → `'foo' <-> 'bar'` (frase estricta, no OR) para identificadores con guion bajo | `src/vectorstore/pg_retriever.py:170` | Medido en la base |
| 23 | **Encoding**: `local_loader.py:180` lee con `errors="replace"` (un `.md` cp1252 entra con U+FFFD sin aviso); `ingest_pdf.py:95` e `ingest_video.py:136` imprimen sin `use_utf8_stdout` | — | — |
| 24 | **`batch_ingest_videos.py:44` empareja por subcadena**: `ejercicio1` matchea `ejercicio10` | — | — |

## 3. Deriva de documentación (CLAUDE.md es prompt: lo stale se repite como verdad)

| Afirmación en `CLAUDE.md` | Realidad hoy |
|---|---|
| "7 tools" | 8 (`ingest_kb_objects`) |
| "82 passing" | 417 recolectados |
| "reranker declarado y no usado" | cableado (`pg_retriever.py:190`, `server.py:1096`) |
| "catálogo con 59 entradas, `version_since` NULL en las 59" | 765 / 706 con versión. `candidate_repository.py:94` sigue escribiendo `None`: coexisten dos vías |
| "`object_type`: Method, Event, Command, Rule, Property" | 96 tipos |
| "`sql/001` declara `'simple'`" | ya crea `es_simple` |
| `bt_docs` 1.122 chunks | 2.580 |
| Commands con `.venv/bin/…`, `./dev.sh` (usa `lsof`), `docker compose up` primero | Windows: `.venv\Scripts\`, existe `dev.ps1`, el compose no se usa local |

También: `docs/windows-native-setup.md` describe ChromaDB y Python 3.12 (el venv es 3.13.2 sobre
Postgres); `docs/windows-paso-a-paso.md:11` manda clonar una rama ya integrada; `PENDIENTES.md`
lista `gx_sac` (colección que no existe) y "reranker no cableado"; `RAG_SYSTEM_MAP.md:888` manda
arreglar un dedup SAC que `scrape_sac.py:107` ya resolvió; comentarios en `ingestor.py` y
`scrape_sac.py` hablan de ChromaDB.

## 4. Tests e infraestructura

- **13 tests de `test_http_server.py` fallan solo con `.env`** ✅: `GX_ALLOWED_HOSTS` del `.env`
  no incluye `testserver` y `TrustedHostMiddleware` responde 400. Aislamiento más barato: helper
  `_app(**kw)` → `create_app(allowed_hosts=["*"], …)` en las 13 llamadas. `monkeypatch.setenv`
  no sirve: `config = Config()` es singleton al importar (`src/config.py:102`).
- Marcadores: `unit` 226, `integration` 73, **sin marcador 118**: `-m unit` no sirve de semáforo.
- Sin test: `src/sac_jobs.py`, `src/ingestion/ingestor.py`, `src/vectorstore/embeddings.py`,
  `src/vectorstore/pg_client.py`; de `scripts/` solo `scrape_wiki` e `ingest_local`.
- **Sin CI** (`.github/` no existe). Sin lock de Python: `requirements.txt` abierto en `>=`;
  un install limpio cruza major en `sentence-transformers` (5.6 → 6.1) y minor en `SQLAlchemy`
  (2.0 → 2.1) sin probar. `ruff` (defaults): 129 hallazgos, 66 autofixables.
- Docker: `python:3.12-slim` vs 3.13 local; torch baja la rueda CUDA (parte de los ~15 min de
  build); `ruff/black/pytest` entran a la imagen; `gx-rag-mcp` y `gx-rag-api` sin
  `depends_on: gx-rag-pg`. Secretos y healthchecks: bien.
- Instalados en el venv y no declarados: `chromadb` 1.5.9 (arrastra kubernetes, grpcio,
  opentelemetry), `rank-bm25`, `python-multipart`.

## 5. Oportunidades — solo las que resuelven algo medido

Ordenadas por relación beneficio/costo. Cada una dice qué problema de arriba cierra.

| # | Cambio | Resuelve | Costo | Riesgo |
|---|---|---|---|---|
| A | **`ToolAnnotations`** del SDK `mcp` 2.3.0 (= última en PyPI): `read_only_hint` en las read tools, `open_world_hint` en `add_from_url`, y derivar `PUBLIC_TOOL_NAMES` de la annotation | La frontera lectura/escritura hoy solo la conoce `PUBLIC_TOOL_NAMES`; el cliente no la ve | 1 línea por tool + 1 test | nulo |
| B | **`output_schema` + `structuredContent` en `verify_syntax`**, e **`isError=True`** en errores y rechazos | #6; hoy un error y un resultado son indistinguibles por protocolo | bajo | duplicar fuente de verdad si el schema no se deriva de `Verdict` |
| C | **Dedup por artículo en el SQL de fusión** (`ROW_NUMBER() OVER (PARTITION BY article_id)` o `max_per_article`) | `top_k` cuenta chunks: un ticket SAC de 37 chunks copa los 5. El eval dedupea (`runner.py:14-32`) y el agente no lo recibe: el eval mide algo distinto de lo servido | horas | nulo de re-embed; medir con `eval_corpus` |
| D | **Manifiesto como única puerta de lo local**: `ingest_pdf` e `ingest_video` solo producen el `.md`; todo entra por `ingest_local --manifest`, y `_markdown_title` lee el frontmatter | #10, #11 | ~60 líneas + purgar `manual/*` y `video/*` ya indexados | bajo |
| E | **Hash de identidad con versión del loader** (`sha256(contenido + LOADER_VERSION + meta)`), como `FICHA_SCHEMA_VERSION` | #12 | 10 líneas + test; un bump re-embebe `bt_docs` (minutos) | un bump accidental dispara ese re-embed |
| F | **Reranker multilingüe drop-in**: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (misma API, solo `config.py:17`) por latencia en CPU; `BAAI/bge-reranker-v2-m3` si la calidad justifica 568M parámetros. Y aviso explícito cuando actúe el fallback | #4 | descarga + eval | latencia sobre 30 pares (el embedding ya cuesta 325 ms en el VPS) |
| G | **Título dentro del embedding** (`f"{title}\n{chunk}"`, sin tocar `content`) y `chunk_prefix` también en `parse_genexus_dump`/`parse_wwp_dump` | La mitad densa no sabe de qué artículo es el fragmento; hoy el título solo entra al `tsv` | re-embed completo (5,5 h a 1,1 chunk/s; otra cifra del mapa dice 28 h: conviven) | hacerlo en la MISMA corrida que `e5v1` para pagar un solo re-embed, midiendo antes en subconjunto; **prerrequisito: #9** |
| H | **IDF sin BM25 real**: tabla `term_df` por colección desde `ts_stat` y podar del OR los términos con df > N % | Hallazgo #40 del plan (OR sin IDF: ordenar 14.844 matches de `for` cuesta 193 ms) en latencia y calidad a la vez. `pg_search`/ParadeDB no corre en Windows con PG nativo | bajo | recall en consultas de una sola palabra frecuente; medir |
| I | **Multi-query sin LLM propio**: aceptar `queries: list[str]` y fusionar por RRF en el mismo SQL. El consumidor ES un LLM: manda él las paráfrasis | eje PARÁFRASIS de `eval_corpus` y #38 (con/sin tilde) | bajo | — |
| J | **Chunking por estructura en `local_loader._pdf_docs`**: partir por heading (`bt_pdf_extract` ya entrega `##/###` y tablas) y pasar `chunks=[(kind, texto)]`, que `_write_chunks` ya acepta | el splitter a 800 chars deja tablas sin su fila de encabezado | ~40 líneas + test | sección > ~512 tokens: e5 trunca en silencio; sub-partir con tope 1500 como `kb_fichas` |
| K | **`pyproject.toml` + lock (`uv`)** con extras `video`/`dev`; Docker instala del lock; torch CPU con `--extra-index-url …/whl/cpu` | build no reproducible; GB de imagen y minutos de build | 1-2 h | bajo |
| L | **`[tool.ruff]` + `ruff format`** (sacar black), **CI mínimo** (`ruff check` + `pytest -m "not integration"`), y marcar los 118 tests sin marcador | nada corre los 344 tests no-integración fuera de esta máquina | 1-2 h | tests que cargan `e5-large`: marcador `model` o mock |
| M | **Mover la lógica inline de `server.py`** (1357 líneas) a `src/tools/` (un módulo por tool: `(Tool, handler)`) y `src/prompts/` (los textos de guía) | dispatcher `if/elif` y textos son dos razones de cambio | refactor mecánico | cubierto por los tests de contrato |
| N | **Deuda OCR/ASR**: `--webcam-box` como argumento (hoy fijo a 1920x1080: otro layout de Teams deja pasar cara y nombre al RAG), carga perezosa de Paddle (hoy también en dry-run), contar líneas descartadas por `MIN_CONFIDENCE` | #19 y PII silenciosa | bajo | ninguno sobre lo indexado |

**Lo que NO se recomienda ahora, y por qué**: cambiar de modelo de embeddings (bge-m3,
e5-instruct) hasta medir `e5v1` (mismo costo de re-embed y el set de eval no tiene resolución
para distinguirlos); late chunking (exige modelo de contexto largo); MMR (la dedup por artículo
cubre el caso real); dedup por hash semántico (el duplicado real es por identidad, no por
contenido parecido); elicitation para el gate de ingesta (ya es fail-safe y las write tools son
solo stdio); OAuth en el transporte (clientes propios, bearer de 32+ chars, TLS en Traefik: lo
urgente es la IP real, #13); docling/pymupdf4llm (pymupdf4llm usa el mismo `find_tables`
descartado; docling habría que medirlo sobre MDI-10109 antes); mypy y pre-commit (sin evidencia
de un bug que hubieran evitado, y cada proceso extra en hooks lo penaliza el antivirus).

## 6. Orden sugerido

1. **Descripciones que mienten** (#1, #2, #5, #6) + sus tests de contrato: horas, impacto máximo.
2. **#3** (match débil en `verify_syntax`): decidir si `% n %` baja a PISTA.
3. **#9** (`emb_profile`), porque bloquea cualquier trabajo de embeddings.
4. **Harness de eval** (#8) y **bench** (#18): sin ellos no se puede medir nada de la sección 5.
5. **Aislar `test_http_server.py` del `.env`** y marcar los 118 tests.
6. `CLAUDE.md`, `PENDIENTES.md` y `docs/windows-*.md` al estado real.
7. Recién después, las oportunidades, en el orden de la tabla.

## Límites de esta auditoría

No se midió el VPS ni se corrió la suite completa durante la revisión (había un OCR en segundo
plano); 417 es recolección, no verdes. El #3 se demostró sobre `_lookup_entry`, no end-to-end
(escribe en `validation_log`). CVEs no verificados (`pip-audit` no está instalado).
