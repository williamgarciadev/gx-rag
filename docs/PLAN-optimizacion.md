# Plan — optimizar `gx-rag`: medir primero, después cortar peso muerto

> **Documento de handoff.** Está escrito para que un agente que llega sin contexto lo levante y
> arranque. Reconstruido desde el código el **2026-08-11**, rama `vps-produccion`, HEAD `dfeeb63`.
> Todo `archivo:línea` de acá fue verificado, no supuesto. Si al retomarlo el HEAD cambió,
> revalidá la tabla de hallazgos antes de ejecutar.

## Cómo arrancar (leer esto primero)

1. **Entorno.** Trabajá en el venv del repo, nunca en el intérprete global: un paquete global
   (`notebooklm_skill`) trae un package `scripts` de nivel superior que tapa el `scripts/` de este
   repo y rompe `from scripts import scrape_wiki` en `src/api.py`.
   ```bash
   .venv/bin/python -m pytest tests/ -q     # piso declarado: 76 passing al 2026-08-11
   ```
2. **`GXRAG_DSN` es obligatoria.** Sin ella `get_pool()` tira `RuntimeError` al arrancar
   (`pg_client.py:29-33`, `open=True` a propósito: falla temprano, no en la primera query).
   El `.env` de la raíz se carga en `src/__init__.py:20` con `override=False` — el entorno gana.
   Ojo: esa misma variable decide si el **catálogo de sintaxis** vive en Postgres o en el SQLite
   local (`models.py:113-140`).
3. **Leé `RAG_SYSTEM_MAP.md`** (raíz, sin trackear) — es el mapa autoritativo, con clasificación de
   evidencia por afirmación. `CLAUDE.md` ya está sincronizado. **`docs/CLAUDE.md` es spec de
   planeación, no realidad.**
4. **Reiniciar el MCP no es automático.** El proceso conserva el código viejo en memoria: un cambio
   se verifica por MCP recién después de reconectar el server.
5. **Orden de ejecución:** Fase 0 y Fase 1 pueden ir en paralelo (Fase 1 no depende de mediciones).
   Fase 2 **no arranca** hasta que Fase 0 dejó escrito `docs/BASELINE-perf.md`. Fases 3 y 4 son
   independientes de todo lo anterior y se pueden tomar en cualquier momento.

## Contexto

El pedido es **identificar qué se puede mejorar/optimizar y hacerlo más eficiente**, más **una versión nueva del prompt de bootstrap**.

Punto de partida verificado hoy contra el código (rama `vps-produccion`, HEAD `dfeeb63`):

- El bootstrap que se usó para arrancar la sesión **está stale y contradice el repo**: describe ChromaDB + rank-bm25, dice que `tests/` está vacío y que `ingest_batch` no está implementado. Nada de eso es cierto — el store es Postgres+pgvector+tsvector (commit `c4fb609`), hay `pytest.ini` con 6 suites, y `ingest_batch` está implementado con `dry_run=True` por defecto.
- `RAG_SYSTEM_MAP.md` (sin trackear) ya es el mapa autoritativo, con clasificación de evidencia por afirmación. `CLAUDE.md` raíz ya fue sincronizado. **La fase de "entender el sistema" está cerrada** — no se re-hace.
- **Hay ~638 líneas sin commitear** (`src/server.py` +342, `bulk_loader.py` +151, `ingest_dump.py` +79, `pg_indexer.py` +7, tests) que ya arreglan mentiras de interfaz reales.

El problema de fondo para "optimizar": **no hay una sola medición de latencia en todo el camino de servicio.** `rg "perf_counter|time.time|EXPLAIN"` sobre `src/` devuelve únicamente las ventanas del rate-limit. Y `RAG_SYSTEM_MAP.md` §16 declara Evaluación como **CRITICAL**: no hay set de relevancia etiquetado, así que ninguna decisión de ranking está validada.

**Por eso este plan no arranca tocando parámetros.** Arranca instrumentando, sigue por lo que es seguro por construcción, y deja explícitamente afuera lo que exige un set etiquetado. Optimizar sin baseline es exactamente el pecado que los propios docs del repo prohíben.

---

## Hallazgo principal: el `ROW_NUMBER()` dentro de los CTE probablemente anula el índice HNSW

`src/vectorstore/pg_retriever.py:30-46`:

```sql
dense AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
    FROM chunks
    WHERE collection = %(collection)s
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(pool)s
)
```

En Postgres las **window functions se evalúan sobre todas las filas que pasan el `WHERE`, antes del `ORDER BY … LIMIT` externo**. `ROW_NUMBER() OVER (ORDER BY embedding <=> qvec)` necesita el orden completo de la partición ⇒ el planner no puede empujar el `LIMIT 30` dentro del índice. Con ~50.056 chunks en `gx_docs` eso significa **calcular 50k distancias coseno de 1024 dims y ordenarlas en cada query**, con el HNSW degradado o inutilizado. El CTE `lexical` (líneas 39-46) tiene la misma forma: `ts_rank_cd` sobre todo el match set — y como `_to_tsquery()` une los términos con **OR** (línea 75, decisión deliberada de recall), ese match set es enorme.

Fix propuesto — empujar el `LIMIT` a una subquery interna y rankear afuera, preservando el orden:

```sql
dense AS (
    SELECT chunk_id, ROW_NUMBER() OVER () AS rank
    FROM (
        SELECT chunk_id FROM chunks
        WHERE collection = %(collection)s
        ORDER BY embedding <=> %(qvec)s::vector
        LIMIT %(pool)s
    ) d
)
```

**Semánticamente idéntico** (`ROW_NUMBER() OVER ()` conserva el orden de la subquery), pero deja al HNSW servir `ORDER BY … LIMIT` que es justo su caso de uso.

⚠️ **Esto es una hipótesis, no una medición.** El plan la valida con `EXPLAIN (ANALYZE, BUFFERS)` **antes** de tocar nada. Si el plan de ejecución ya usa el índice, se descarta y se documenta por qué.

---

## Tabla de hallazgos (prioridad descendente)

| # | Hallazgo | Evidencia | Costo real | Riesgo del fix |
|---|---|---|---|---|
| 1 | `ROW_NUMBER()` en los CTE puede anular HNSW y GIN | `pg_retriever.py:33,41` | latencia de **toda** query | bajo (equivalencia semántica) |
| 2 | Cero instrumentación de latencia en el camino de servicio | `rg` sobre `src/` | no hay baseline para nada | nulo |
| 3 | `lru_cache(1024)` cachea por **lote completo** de textos | `embeddings.py:25-33` | hit-rate ~0 en ingesta; retiene hasta 1024 lotes de embeddings + sus strings en RAM para siempre | bajo |
| 4 | `hnsw.ef_search` nunca se setea; default 40 vs `CANDIDATE_POOL=30` | ausencia en `pg_retriever.py`; `sql/001-chunks-schema.sql:57` | recall denso al borde del default | bajo (medible) |
| 5 | `CANDIDATE_POOL=30` fijo sin importar `top_k` | `pg_retriever.py:25,104` | `top_k=20` fusiona sobre un pool raquítico | bajo |
| 6 | Embedding serializado a **literal de texto** (~10 KB/fila) | `pg_indexer.py:58-60`, `pg_retriever.py:98` | ancho de banda y parseo en ingesta masiva | medio (adaptador binario) |
| 7 | `executemany` fila a fila para el upsert de chunks | `pg_indexer.py:108` | ingesta de lote lenta (501 artículos SAC pendientes) | medio |
| 8 | Modelo e5-large carga **perezoso** en la 1ª query | `embeddings.py:16-22` | el primer agente que consulta paga el arranque en frío | nulo (pre-warm) |
| 9 | Error de DB en `get_document`/`get_article` se disfraza de 404 | `pg_collections.py:46-48` | **mentira de interfaz**: caída de Postgres se lee como "no existe" | bajo |
| 10 | `search_docs` le dice al agente que el motor léxico es **BM25** | `server.py:54`; motor real tsvector | el prompt del consumidor miente | nulo |
| 11 | `get_document` describe `doc_id` como "ID interno en **ChromaDB**" | `server.py:146` | ídem | nulo |
| 12 | `/verify` HTTP devuelve veredicto **sin** `_VERDICT_GUIDANCE` | `api.py:268` vs `server.py:598-612` | superficie anti-alucinación asimétrica entre transportes | bajo |
| 13 | `scripts/ingest_dump.py` **escribe por defecto** (`--dry-run` opt-in) | `ingest_dump.py:103` vs `server.py:395` | gate humano invertido respecto de la tool MCP | bajo |
| 14 | `data/syntax.db` **trackeado en git**, binario mutable con telemetría real | `git ls-files data/`; 32K→45K en el diff | diffs opacos, conflictos irresolubles, snippets de usuario versionados | bajo |
| 15 | `data/scraped_sac/` (1002 archivos, 2.9 MB) **no está en `.gitignore`** | `.gitignore` solo cubre `data/scraped/`, `chroma/`, `raw/` | un `git add .` infla el repo para siempre | nulo |
| 16 | `pymupdf` y `structlog` declarados con **cero imports** | `requirements.txt`; `rg` sin resultados | peso de imagen e instalación | nulo |
| 17 | Config muerta: `chunk_size/overlap`, `reranker_model`, `default_version`, `SyntaxDBConfig.path` | `config.py:17-23,50` vs `pg_indexer.py:24-25` | cambiar la config no cambia nada | nulo |
| 18 | `.pyc` huérfanos de los módulos Chroma borrados | `src/vectorstore/__pycache__/` | ruido | nulo |
| 19 | `StatusBar.vue` lee `chroma_collections`/`chroma_total_docs` inexistentes | `StatusBar.vue:25,49` vs `api.py:179-185` | dos tarjetas muestran "—" siempre | bajo |
| 20 | Chips de filtro del frontend no filtran (itera `results`, no `filteredResults`) | `SearchPanel.vue` | filtros decorativos | bajo |

---

## Plan de ejecución

### Fase 0 — Baseline (sin esto, nada de lo demás es verificable)

1. **`scripts/bench_retrieval.py`** (nuevo): corre N queries fijas contra `hybrid_search()`, reporta p50/p95/p99 y, con `--explain`, vuelca `EXPLAIN (ANALYZE, BUFFERS)` de `_SEARCH_SQL`. Reusa `pg_client.get_pool()` y `pg_retriever._SEARCH_SQL` — sin duplicar SQL.
2. **Medir el hallazgo #1**: `EXPLAIN ANALYZE` de la query actual contra `gx_docs` y contra `gx_syntax` (26 filas vs 50k: el contraste expone el comportamiento del filtro por colección sobre HNSW).
3. Registrar `SELECT extversion FROM pg_extension WHERE extname='vector'` y `SHOW hnsw.ef_search` — el repo no pinea pgvector en ningún lado, así que hoy **no sabemos** contra qué versión corremos.
4. Guardar el baseline en `docs/BASELINE-perf.md`. Es el número contra el que se compara todo lo que sigue.

**Gate:** sin baseline escrito, no se pasa a Fase 2.

### Fase 1 — Peso muerto (seguro por construcción, sin cambio de comportamiento)

Se puede hacer en paralelo a Fase 0; no depende de mediciones.

- `.gitignore`: agregar `data/scraped_sac/` y `data/*.db` (#15).
- `git rm --cached data/syntax.db` + documentar el bootstrap del catálogo por schema, no por binario (#14). **Decisión humana requerida** — ver "Qué NO se toca".
- Sacar `pymupdf` y `structlog` de `requirements.txt`, con comentario de por qué (#16). Si Fase 1 de PDFs Bantotal los va a necesitar, se reincorporan cuando haya código que los importe.
- Borrar config muerta o cablearla; recomiendo **cablear** `chunk_size/overlap` (`config.py` → `pg_indexer`) y **borrar** `reranker_model` y `default_version` (#17).
- `find src -name '__pycache__' -exec rm -rf` para los `.pyc` huérfanos (#18).
- Sincronizar las 2 descripciones de tools que le mienten al agente consumidor (#10, #11) — cambio de texto, cero riesgo, alto valor: **esas descripciones son el prompt del agente**.

### Fase 2 — Optimizaciones medidas (cada una re-mide contra el baseline)

Orden por relación impacto/riesgo:

1. **Reescribir los CTE con `LIMIT` interno** (#1). Comparar `EXPLAIN ANALYZE` antes/después y p95. Test de equivalencia: mismo top-k, mismo orden, mismos scores contra el corpus de test.
2. **Setear `hnsw.ef_search` explícito por sesión** (#4) en `pg_client` o como `SET LOCAL` en la query. Barrer 40/64/100 y quedarse con el codo de la curva latencia/recall.
3. **`CANDIDATE_POOL = max(30, top_k * 4)`** (#5) — el pool deja de ser un techo escondido cuando el llamador pide más.
4. **Arreglar el `lru_cache`** (#3): sacarlo de `embed_texts` y ponerlo en `embed()` (camino de query, un texto, alta repetición). La ingesta deja de retener RAM sin motivo.
5. **Pre-warm del modelo** (#8): cargar e5-large al arrancar `server.py`/`http_server.py`, con log de cuánto tardó.
6. **Ingesta en lote** (#6, #7): adaptador binario de vector + `COPY` a tabla staging + `MERGE`/`INSERT … ON CONFLICT` desde ahí. Medir contra los 501 artículos de `data/scraped_sac/`, que son el lote real pendiente.

### Fase 3 — Correcciones de interfaz (baratas, tocan no-alucinación)

- #9: distinguir error de DB de "no encontrado" en `pg_collections.py`. Hoy una caída de Postgres le dice al agente "ese documento no existe" — es alucinar en negativo por la vía de infraestructura.
- #12: que `/verify` HTTP devuelva la misma guía de veredicto que MCP.
- #13: invertir el default de `scripts/ingest_dump.py` para que la rama segura sea la que sale por omisión, igual que `ingest_batch`.
- #19, #20: los dos bugs vivos del frontend.

### Fase 4 — Prompt de bootstrap nuevo

Crear **`docs/BOOTSTRAP-agente.md`**, reemplazo del prompt stale. Contenido:

- Arquitectura real: **tres** entrypoints (`server.py` stdio read+write, `http_server.py` público read-only con bearer fail-closed, `api.py` admin sin auth protegida por topología).
- Retrieval real: pgvector + tsvector + RRF `k=60` **en una sola query SQL**; score posicional **no calibrado**.
- Validación real: catálogo que resuelve a **Postgres si hay `GXRAG_DSN`, SQLite si no** (`models.py:113-140`) — el bootstrap viejo decía "SQLite" a secas.
- Estado real de tests (`pytest.ini`, markers `unit`/`integration`).
- Las dos capas y el modelo de confianza (eso del bootstrap viejo **sí** sigue vigente y se conserva).
- Puntero a `RAG_SYSTEM_MAP.md` como mapa autoritativo y a `docs/PROMPT-auditoria-agente.md` para auditar.

Marcar el bootstrap viejo como derogado donde viva.

---

## Archivos críticos

| Archivo | Qué cambia |
|---|---|
| `src/vectorstore/pg_retriever.py` | CTE con `LIMIT` interno, `ef_search`, `CANDIDATE_POOL` dinámico |
| `src/vectorstore/embeddings.py` | reubicar `lru_cache`, pre-warm |
| `src/vectorstore/pg_indexer.py` | `COPY` + staging, adaptador binario de vector |
| `src/vectorstore/pg_collections.py` | distinguir error de DB de 404 |
| `src/server.py` | descripciones "BM25"/"ChromaDB" (líneas 54, 146) |
| `src/api.py` | guía de veredicto en `/verify` |
| `src/config.py` | cablear chunking, borrar config muerta |
| `scripts/bench_retrieval.py` | **nuevo** — harness de latencia + EXPLAIN |
| `docs/BOOTSTRAP-agente.md`, `docs/BASELINE-perf.md` | **nuevos** |
| `.gitignore`, `requirements.txt` | higiene |
| `frontend/src/components/StatusBar.vue`, `SearchPanel.vue` | 2 bugs vivos |

---

## Verificación

1. `.venv/bin/python -m pytest tests/ -q` en verde **antes** de empezar (el piso declarado es 76 passing) y después de cada fase.
2. Cada optimización de Fase 2 lleva su número: `scripts/bench_retrieval.py` antes y después, delta explícito. **Si no mejora, se revierte** — no se queda "porque en teoría es mejor".
3. Equivalencia de resultados en el fix #1: mismos `chunk_id` en el mismo orden con los mismos scores, sobre el schema de test descartable de `tests/conftest.py` (que ya tiene guarda anti-producción).
4. Test que falla sin el fix, para cada corrección de Fase 3 — la regla del repo.
5. Verificar persistencia por vía independiente del valor de retorno: `get_article` por colección, no búsqueda por ranking (un `top_k` chico sobre 50k chunks **no prueba una ausencia**).
6. Recordar que **reiniciar el MCP no es automático**: un cambio se verifica por MCP recién tras reconectar el server.

---

## Qué NO se toca, y por qué

- **Prefijos `query:`/`passage:` de e5** (`embeddings.py:32`): e5 los recomienda y hoy no se usan, pero cambiarlos **invalida los 50k embeddings existentes** y exige reindexar todo. Sin set de relevancia etiquetado no hay forma de probar que mejora. Queda documentado como pendiente medible.
- **`chunk_size` 800/100, config `'spanish'` en tsvector, reranker multilingüe**: misma razón. El propio `sql/001-chunks-schema.sql:49` declara que "cuál de las dos fórmulas ordena mejor es una pregunta abierta: no hay set de relevancia etiquetado".
- **Reducir e5-large a fp16/ONNX** (~2.2 GB en RAM por proceso): cambia los vectores ⇒ reindex completo. Decisión de calidad, no de performance.
- **Rate limit en Redis**: necesario antes de escalar a >1 worker, pero hoy no hay presión que lo justifique. Ya documentado en `http_server.py:17-24`.
- **`git rm --cached data/syntax.db`**: toca historial de datos y el bootstrap del catálogo en el VPS. **Requiere tu OK explícito** antes de ejecutarlo.
- **Auth en `api.py`**: la superficie admin no tiene auth y tiene CORS `*` con credentials. Hoy la protege la topología (sin puerto host, túnel SSH). Ponerle auth es correcto pero es un cambio de contrato con el frontend — decisión aparte de "optimizar".

---

## Estado del working tree al momento de escribir este plan

⚠️ **Leer antes de tocar nada — hay trabajo sin commitear que puede colisionar.**

```
 M data/syntax.db              (binario, 32K → 45K)
 M scripts/ingest_dump.py      (+79)
 M src/ingestion/bulk_loader.py (+151)
 M src/server.py               (+342)
 M src/vectorstore/pg_indexer.py (+7)
 M src/vectorstore/pg_retriever.py (+5)   ← trabajo en paralelo, ver sección ⚠️
 M tests/conftest.py           (+19)
 M tests/test_bulk_loader.py   (+95)
?? RAG_SYSTEM_MAP.md
?? data/scraped_sac/           (1002 archivos, 2.9 MB)
?? docs/PROMPT-auditoria-agente.md
?? sql/002-unaccent-tsv.sql              ← trabajo en paralelo, ver sección ⚠️
?? tests/test_server_write_tools.py
```

Es un **snapshot**, no un estado estable: hay otro agente trabajando. Correr `git status` al retomar.

Ese diff **ya arregla mentiras de interfaz reales** y no hay que rehacerlo:

- `add_from_url` declaraba `collection` en su schema y el handler **nunca lo leía** — reportaba
  escribir donde el llamador pedía y escribía donde clasificaba (`server.py:655-670`).
- El `ON CONFLICT` de `_UPSERT_SQL` no actualizaba `collection`, así que un artículo ya indexado
  **nunca se movía** de colección (`pg_indexer.py:41`, con el comentario que documenta la medición).
- `ingest_batch` era un stub con descripción que prometía la capacidad; ahora está implementado con
  `dry_run=True` por defecto (`server.py:367`).

**Recomendación: correr `pytest` y commitear esto en commits atómicos ANTES de Fase 0**, para que el
baseline se mida sobre una base estable y no sobre un working tree en movimiento. Si se decide
medir igual, dejarlo dicho explícitamente en `docs/BASELINE-perf.md`.

## ⚠️ Trabajo en paralelo YA aplicado sobre `pg_retriever.py` (2026-08-11)

Otro agente entró a `src/vectorstore/pg_retriever.py` **después** de escrito este plan. Su cambio
está sin commitear y hay que integrarlo, no pisarlo:

- **`sql/002-unaccent-tsv.sql`** (nuevo): crea la config `es_simple` = `simple` + `unaccent` y
  reescribe la columna generada `chunks.tsv`. Guard por `pg_get_expr` para no reescribir 51k filas
  al re-aplicar.
- **`pg_retriever.py:42`**: `to_tsquery('simple', …)` → `to_tsquery('es_simple', …)`.

**Bug que arregla, con medición**: el índice léxico era ciego a las tildes.
`to_tsquery('simple','autorización')` → 96 hits vs `'autorizacion'` → 15. Como la gente escribe sin
tildes, la mitad léxica del RRF perdía ~84% de los chunks relevantes y el híbrido degeneraba a
**densa pura**. Invisible mientras el corpus era solo `gx_docs` (wiki GeneXus, en inglés); saltó al
sumar `bt_docs` en español. Verificado que los identificadores Bantotal siguen matcheando exacto
(`fsd011` → 59 hits antes y después).

**Impacto sobre este plan:**

1. El hallazgo #1 (CTE con `ROW_NUMBER`) **sigue vigente y se aplica ENCIMA de este cambio**, no en
   lugar de él. Tocan el mismo CTE `lexical` pero cosas distintas: ellos la config de la tsquery,
   nosotros la estructura del `LIMIT`.
2. **La urgencia de #1 sube**: `unaccent` ensancha el match set léxico, y ese CTE calcula
   `ts_rank_cd` sobre *todas* las filas que matchean antes de aplicar el `LIMIT`.
3. El baseline de Fase 0 debe medirse **después** de aplicar `sql/002`, o los números no comparan.
4. El corpus creció: `RAG_SYSTEM_MAP.md` documenta 50.056 chunks en `gx_docs` + 26 en `gx_syntax`;
   ahora hay `bt_docs` (~51k filas en total según el 002). Revalidar los conteos.

## Coordinación entre agentes en paralelo

Las fases están cortadas para minimizar colisión de archivos. Si trabajan dos agentes a la vez:

| Franja | Archivos que toca | Choca con |
|---|---|---|
| Fase 0 (baseline) | `scripts/bench_retrieval.py` (nuevo), `docs/BASELINE-perf.md` (nuevo) | nadie |
| Fase 1 (peso muerto) | `.gitignore`, `requirements.txt`, `src/config.py`, `src/server.py` (solo strings 54 y 146) | Fase 2 en `config.py` si se cablea chunking |
| Fase 2 (perf) | `pg_retriever.py`, `embeddings.py`, `pg_indexer.py` | 🔴 **`pg_retriever.py` está EN DISPUTA** — ver sección de arriba |
| Fase 3 (interfaz) | `pg_collections.py`, `api.py`, `ingest_dump.py`, `frontend/` | nadie |
| Fase 4 (bootstrap) | `docs/BOOTSTRAP-agente.md` (nuevo) | nadie |

**Fase 3 y Fase 4 son las más seguras para tomar en paralelo**: no tocan ningún archivo de Fase 0/1/2.
**Antes de entrar a Fase 2, correr `git status` y leer el diff vivo de `pg_retriever.py`.**

## Reglas del repo que este plan no puede violar

- **Nunca inventar métodos, propiedades ni firmas de GeneXus/Bantotal.** El catálogo es la fuente de
  verdad; lo no catalogado es `NO_VERIFICABLE`, no se asume.
- **El crecimiento del corpus pasa por gate humano.** Descubrir candidatos automáticamente está bien;
  ingerir automáticamente no. La rama segura va por defecto.
- **Cada fix lleva un test que falla sin él.** La suite completa en verde es el piso, no la meta.
- **No inventar números de versión de PyPI de memoria** — consultar PyPI.
- SDK oficial `mcp` (no FastMCP standalone). Nada de LangChain/LlamaIndex como framework base
  (`langchain-text-splitters` para chunking está bien).
- **Medir antes de afirmar.** Una salida de herramienta, un conteo, un test. Un log o un "parece que"
  no son evidencia. Si no se pudo medir, se dice así.
- Una fase está terminada cuando el comportamiento se verificó funcionalmente, **no cuando compila**.

## Cómo reportar avance

Este documento es la fuente de verdad del trabajo de optimización. Quien ejecute una fase:

1. Marca la fase con su resultado **medido** (no "hecho": el número).
2. Si un hallazgo se descarta al medirlo, **no se borra de la tabla** — se marca `DESCARTADO` con la
   evidencia que lo descartó. Un hallazgo refutado con medición vale tanto como uno confirmado.
3. Si aparece un hallazgo nuevo, se agrega a la tabla con su `archivo:línea` y su costo real.

Documentos hermanos: `RAG_SYSTEM_MAP.md` (arquitectura real, mapa autoritativo) y
`docs/PROMPT-auditoria-agente.md` (auditar el repo contra sus propios docs de planeación).
