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
| 9 | ✅ **RESUELTO** — Error de DB en `get_document`/`get_article` se disfrazaba de 404 | `pg_collections.py:46-48` | **mentira de interfaz**: caída de Postgres se leía como "no existe" | bajo |
| 10 | ✅ **RESUELTO** — `search_docs` le decía al agente que el motor léxico es **BM25** | `server.py:54`; motor real tsvector | el prompt del consumidor mentía | nulo |
| 11 | ✅ **RESUELTO** — `get_document` describía `doc_id` como "ID interno en **ChromaDB**" | `server.py:146` | ídem | nulo |
| 12 | `/verify` HTTP devuelve veredicto **sin** `_VERDICT_GUIDANCE` | `api.py:268` vs `server.py:598-612` | superficie anti-alucinación asimétrica entre transportes | bajo |
| 13 | `scripts/ingest_dump.py` **escribe por defecto** (`--dry-run` opt-in) | `ingest_dump.py:103` vs `server.py:395` | gate humano invertido respecto de la tool MCP | bajo |
| 14 | `data/syntax.db` **trackeado en git**, binario mutable con telemetría real | `git ls-files data/`; 32K→45K en el diff | diffs opacos, conflictos irresolubles, snippets de usuario versionados | bajo |
| 15 | `data/scraped_sac/` (1002 archivos, 2.9 MB) **no está en `.gitignore`** | `.gitignore` solo cubre `data/scraped/`, `chroma/`, `raw/` | un `git add .` infla el repo para siempre | nulo |
| 16 | `pymupdf` y `structlog` declarados con **cero imports** | `requirements.txt`; `rg` sin resultados | peso de imagen e instalación | nulo |
| 17 | Config muerta: `chunk_size/overlap`, `reranker_model`, `default_version`, `SyntaxDBConfig.path` | `config.py:17-23,50` vs `pg_indexer.py:24-25` | cambiar la config no cambia nada | nulo |
| 18 | `.pyc` huérfanos de los módulos Chroma borrados | `src/vectorstore/__pycache__/` | ruido | nulo |
| 19 | ✅ **RESUELTO — y era MÁS GRAVE de lo diagnosticado.** No eran los campos `chroma_*`: era una **colisión de nombres**. `const { health, status } = useApi()` importa una FUNCIÓN `status`, y el template leía `status?.scraped_articles` → `undefined` siempre. Eran **5 de 6 tarjetas** en "—", no dos | `StatusBar.vue` (script vs template) | la UI de curación era una pantalla de guiones | bajo |
| 20 | ✅ **RESUELTO** — el template iteraba `results` con `filteredResults` computado y sin usar | `SearchPanel.vue:97` vs `:209` | chips decorativos | bajo |

### Hallazgos de la auditoría de interfaz (2026-08-11)

Salieron de auditar **contrato de tool contra estado real de los datos** — no de leer el código
solo. Ninguno estaba en la tabla de arriba, y los tres primeros producían una respuesta que el agente
lee como una **ausencia verificada**.

| # | Hallazgo | Evidencia (medida) | Costo real | Estado |
|---|---|---|---|---|
| 21 | **`list_methods(version=…)` devolvía vacío SIEMPRE.** Filtraba `version_since == version` y ninguna entrada tiene versión | `gx_repository.py:312-320`; catálogo real: **59 filas, 59 con NULL**; y `promote_candidate` —única vía al catálogo— hardcodea `version_since=None` (`candidate_repository.py:94`), así que es inalcanzable por construcción | el agente pasa `version` porque el schema lo invita y recibe "Sin entradas catalogadas" → **alucinación en negativo** | ✅ **RESUELTO** |
| 22 | **`list_methods` ofrecía `object_type` que no existen.** La descripción y el mensaje de vacío daban "Transaction, Procedure" como ejemplos | medido: `Method` 34, `Event` 9, `Command` 8, `Rule` 6, `Property` 2 — **cero Transaction, cero Procedure** | un ejemplo que garantiza vacío es peor que ninguno: el segundo vacío se lee como "el catálogo no sabe nada" | ✅ **RESUELTO** |
| 23 | **`verify_syntax` fabricaba `INVÁLIDO` para un snippet sin tokens.** `_normalize_token("do")` → `""`, y la rama de similitud armaba `name LIKE '%%'`, que matchea TODAS las filas | `gx_repository.py:283-288`; reproducido con `""`, `"do"`, `"if else"`, `"return true"` | **veredicto negativo FABRICADO** con 5 sugerencias arbitrarias, en la tool que existe para no fabricar veredictos | ✅ **RESUELTO** |
| 24 | **`verify_syntax(version=…)` no gateaba el veredicto y no lo decía.** La nota de versión solo salía si `version_since` difería, y es NULL siempre | `gx_repository.py:247-255` | un `VÁLIDO` de versión indeterminada presentado como confirmado = una de las 4 formas de alucinar de `docs/gx-bt-rag-calidad-y-no-alucinacion.md §1` | ✅ **RESUELTO** |
| 25 | **`get_document` prometía "el contenido completo de un documento"** y devuelve UN chunk de ~800 chars (el `doc_id` es un `chunk_id`) | `server.py:135-156` vs `pg_collections.py:19-23` | el agente lee un fragmento como el artículo entero y concluye que lo que no está en él no existe | ✅ **RESUELTO** |
| 26 | **Una colección mal escrita se leía como ausencia.** El SDK no valida el `enum` del `inputSchema`, así que `gx_doc` llegaba al SQL y volvía "Sin resultados" | `server.py` handlers de `search_docs`/`get_article`/`get_document` | el agente lo lee como evidencia sobre el CORPUS, no sobre su propio argumento | ✅ **RESUELTO** |
| 27 | **`verify_syntax(language="bt_api")` no puede dar `VÁLIDO` jamás** y no lo declaraba | `bt_apis`: **0 filas** medidas | el agente planifica con una capacidad de verificación que no existe | ✅ **RESUELTO** (declarado) |
| 28 | **`search_docs` carga e5-large (~2.2 GB) ANTES de validar sus argumentos** | `server.py` embebe la query antes de mirar `collection`; el crash de torch al testearlo lo expuso | una llamada con argumento inválido paga el arranque en frío completo | ✅ **RESUELTO** para `collection` (se rechaza antes de embeddear); el resto de la validación temprana queda pendiente |
| 29 | **`sql/001-chunks-schema.sql` declara `tsv` con config `'simple'`** mientras `pg_retriever` consulta con `'es_simple'` | `001:50-52` vs `pg_retriever.py:46`; la base viva ya está en `es_simple` porque corrió el `002` | **un install fresco que corra solo el 001 queda incoherente con el retriever**: o revienta (config inexistente) o pierde la mitad léxica | 🔲 documentado, NO tocado |
| 30 | 🔴 **RE-CLASIFICADO (peor de lo pensado): `/discover` NO FUNCIONA CON NINGUNA FUENTE.** No es una URL corrupta que se arregle. **Medido 2026-08-11**: `wiki` devuelve **0 links** con id numérico parseable (con la URL de `广场` *y* con `commwiki/servlet/wiki`, las dos), y `sac` responde **404**. La wiki no expone un buscador HTML que este parser pueda leer | `api.py:322-327`; medido con curl contra las 5 URLs candidatas | **capacidad declarada que no existe**, en el camino de crecimiento de corpus. Arreglar la URL NO alcanza | 🔲 **DECISIÓN**: declarar la limitación o quitar la fuente. Requiere investigar cómo buscar en la wiki programáticamente (sitemap, API, otro markup) |
| 31 | **`/verify` HTTP devuelve `str(dict)`**, no JSON, y **sin** `_VERDICT_GUIDANCE` | `api.py:268-280` vs `server.py:598-612` | la superficie anti-alucinación es asimétrica entre transportes (dup. de #12) | 🔲 documentado, NO tocado |
| 32 | **`bt_docs` no está en `WRITABLE_COLLECTIONS`** aunque el CLI ya escribió ahí 1.122 chunks | `server.py:236` vs `bt_docs` poblado por `scripts/ingest_dump.py --collection bt_docs` | `ingest_batch` rechaza una colección legítima: el MCP no puede ingerir Bantotal | 🔲 **DECISIÓN HUMANA**: es política de crecimiento de corpus |
| 33 | **La procedencia de `bt_docs` no es citable**: los hits traen `C:\Users\wigam\...\*.txt`, la ruta del disco de quien lo ingirió | `bulk_loader.parse_text_files` → `url=str(path.resolve())`; visto en una respuesta real de producción | el agente consumidor recibe una fuente que **no existe y no puede verificar**, contra la regla de citar la url | 🔲 **DECISIÓN**: exige URL canónica + reindexar `bt_docs` |
| 34 | **`DiscoverPanel.vue` (300 líneas) existe pero no se porta** | rescatado del VPS en el scratchpad; consume `/discover` y `/scrape` | portarlo hoy sería desplegar una UI que **no puede funcionar**, porque #30 está muerto. Además traía una IP de docker hardcodeada (`172.18.0.12:8000`) y `http` desde una página `https` (mixed content) | 🔲 bloqueado por #30 |
| 35 | **Los builds del frontend no son reproducibles** | `frontend/Dockerfile:4-5`: `COPY package.json` **sin** el lock, y `npm install` | `vue: ^3.4.0` resuelve a cualquier 3.x; dos builds del mismo commit pueden diferir | 🔲 barato: `COPY package*.json` + `npm ci` (el lock ya está versionado) |
| 36 | ✅ **RESUELTO — el `<select>` de colecciones del buscador estaba VACÍO.** Tercer lugar con el campo muerto, y no estaba en ninguna tabla: `loadCollections()` leía `s.chroma_collections` | `SearchPanel.vue:240` | no se podía elegir dónde buscar. **Y el `\|\| []` es la causa de que nadie lo notara**: convierte "el campo no existe" en "no hay colecciones", así que la promesa nunca rechaza y el fallback del `catch` —que existía para esto— jamás se ejecutaba | bajo |

| 37 | ✅ **RESUELTO — `npm run dev` daba 500 en TODAS las llamadas.** `vite.config.js` hardcodeaba `target: 'http://gx-rag-api:8000'`, hostname que existe sólo dentro de Docker… donde este proxy **no se usa** (ahí sirve nginx). O sea: el default no funcionaba en el único escenario donde corre. Y `VITE_API_TARGET`, documentada en `CLAUDE.md`, **no se leía**: era una variable sin ningún efecto | reportado por el usuario con el 500 de `/api/health` —endpoint que ni toca la base— y confirmado leyendo `vite.config.js:9` | el frontend en dev era inusable; y `CLAUDE.md` documentaba un comportamiento inexistente | bajo |

> **Cómo apareció #36, que es la lección transferible:** no leyendo el código, sino
> **grepeando el bundle JS ya servido por nginx**. Tras desplegar #19 y #20, el
> asset todavía contenía una ocurrencia de `chroma_collections` que no correspondía
> a nada de lo corregido. Es la regla de verificación independiente del
> `PROMPT-auditoria-agente.md` §20 aplicada al frontend: **el artefacto desplegado
> es la fuente de verdad, no el archivo fuente**. Ojo con `grep -c` sobre un bundle
> minificado: es UNA sola línea, así que cuenta 1 aunque haya cien ocurrencias —
> hay que usar `grep -o | wc -l`.

### Primer set de evaluación: `scripts/eval_bt_docs.py` (2026-08-11)

El requisito AUSENTE y declarado CRITICAL deja de estar en cero. Ground truth
débil pero verificable: los títulos de los 33 capítulos del manual funcional.
**Resultado medido sobre 15 consultas: ruteo correcto top-1 13/15 (86%),
top-3 15/15 (100%).** Los dos fallos de top-1 caen en capítulos chicos
(«cajas de seguridad», 5 chunks) que pierden contra capítulos de 160 chunks.

| # | Hallazgo | Evidencia (medida) | Impacto | Estado |
|---|---|---|---|---|
| 38 | 🔴 **El `sql/002` normalizó SÓLO la mitad léxica: el ranking sigue dependiendo de las tildes.** `unaccent` arregla el `tsvector`, pero el embedding de «préstamos» **no** es el de «prestamos» — coseno **0.9355** — y el RRF fusiona las dos mitades | `eval_bt_docs.py`: `garantias` vs `garantías` y `prestamos` vs `préstamos` devuelven **top-3 distintos**. Con «garantías» (bien escrito) entra *Procesos Batch* en el top-2 | acotado: el top-1 aguantó en los 3 pares, el top-3 se contamina. Pero el `sql/002` documenta el problema como resuelto y **sólo lo está a medias** | 🔲 abierto. Normalizar la query antes de embeddear cambia el sentido de los 51k vectores ya indexados ⇒ **exige el set etiquetado que recién arranca** |
| 39 | ✅ **El score RRF sí tiene estructura interpretable** (matiza, no contradice, "no es una probabilidad") | `1/(RRF_K+1) = 0.0164` es el score de un chunk que entró por **un solo** retriever. Medido: «Angular signals» y «kubernetes ingress» puntúan **exactamente 0.0164**; los aciertos rondan **0.029** porque aparecen en el denso Y en el léxico | da un piso empírico para distinguir ruido de match real | ✅ documentado en la descripción de `min_score` |

### ⚠️ Corrección de un diagnóstico propio (2026-08-11)

Afirmé que el deploy había hecho **retroceder el frontend**, porque el diff del VPS
mostraba 946 inserciones en 7 archivos. **Era falso, y la lección importa:** ese
diff era contra el HEAD **viejo** del VPS (`3229fd8`), y ese mismo trabajo ya
estaba en `vps-produccion`. Comparados byte a byte, `App.vue`, `SearchPanel.vue`,
`StatusBar.vue`, `ScrapeForm.vue`, `ArticleList.vue` e `index.html` son
**idénticos** — el diff aparente era **CRLF vs LF** (Windows vs Linux).

Lo único realmente ausente era `DiscoverPanel.vue` (#34). Moraleja: un `diff`
contra la base equivocada no mide ausencia, y `diff --strip-trailing-cr` es
obligatorio al comparar un árbol de Linux con uno de Windows.

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

✅ **EJECUTADA EN PARTE el 2026-08-11** (auditoría de interfaz). Aplicado con un test que falla sin el fix en cada caso — suite completa **109 passed** (piso previo: 82):

| Hallazgo | Qué cambió | Archivos |
|---|---|---|
| #21 | `list_methods` deja de filtrar por `version`; el parámetro se declara **no-filtrante** en el schema. Medido contra el catálogo real: `list_methods('Method', version='gx18')` pasó de **0 a 34** entradas | `gx_repository.py`, `server.py` |
| #22 | Nueva `list_object_types()`; la respuesta vacía lista la taxonomía REAL consultada al catálogo, y la descripción nombra `Transaction`/`Procedure` como **trampa**, no como ejemplo válido | `gx_repository.py`, `server.py` |
| #23 | Guarda temprana: token vacío → `NO_VERIFICABLE` sin consultar. Y piso `MIN_SIMILARITY_TOKEN = 3` antes de la rama de similitud: con 1-2 chars el `LIKE` matchea cualquier cosa, así que el `INVÁLIDO` que salía de ahí era ruido con forma de veredicto | `gx_repository.py` |
| #24 | Cuando llega `version` y la entrada no tiene `version_since`, la nota lo declara: el veredicto **no discrimina versiones** y no prueba que la firma sea la de esa versión | `gx_repository.py`, `server.py` |
| #25 / #11 | `get_document` se describe **y se rotula en la respuesta** como FRAGMENTO (1 chunk), y apunta a `get_article` para el documento entero | `server.py` |
| #26 / #28 | `KNOWN_COLLECTIONS` como fuente única (enum + chequeo de runtime, misma técnica que `WRITABLE_COLLECTIONS`) y rechazo explícito **antes** de embeddear | `server.py` |
| #10 / #27 | Descripciones sincronizadas con la realidad medida: motor léxico `tsvector`/`es_simple` (no BM25), conteos por colección, y `bt_apis` declarada vacía | `server.py` |
| #9 | `pg_collections` deja **propagar** el error del motor: `None` vuelve a significar una sola cosa ("no hay tal fila"), y una caída de Postgres ya no se presenta como una inexistencia | `pg_collections.py` |

Pendiente de Fase 3, sin tocar: **#31** (`/verify` sin guidance ni JSON, era #12), **#13** (default invertido del CLI), **#19** y **#20** (bugs del frontend), **#29** (`sql/001` con config `'simple'`) y **#30** (`/discover` roto).

### Clasificación de los requisitos de `docs/` (§22 de `PROMPT-auditoria-agente.md`)

Los docs de `docs/` describen **qué se quería**; esta tabla dice contra qué quedó. Priorizados los requisitos de no-alucinación, que son los que el prompt de auditoría manda mirar primero.

| Requisito | Documento | Estado | Evidencia |
|---|---|---|---|
| `verify_syntax` distingue VÁLIDO / INVÁLIDO / NO_VERIFICABLE | `calidad §2` | **IMPLEMENTADO** | `gx_repository.py:216+`; `_VERDICT_GUIDANCE` prescribe cómo NO leer cada veredicto |
| No "alucinar en negativo" (no decir inválido sobre algo válido no catalogado) | `calidad §1` | **IMPLEMENTADO** (era PARCIAL) | era la falla #23: un snippet sin tokens salía `INVÁLIDO`. Corregido, con test |
| No dar una firma de la **versión equivocada** (GX16 vs GX18) | `calidad §1` | **PARCIAL** | el veredicto no está gateado por versión y hoy no puede estarlo: `version_since` es NULL en las 59 filas por construcción (#21). La interfaz ahora **lo declara** en vez de fingir; gatear de verdad exige normalizar y poblar `version_since` (datos + curación humana) |
| Registrar cada veredicto para auditoría (Riesgo/Cumplimiento) | `calidad §6` | **IMPLEMENTADO** | `validation_log`, escrito en todo camino de veredicto |
| Feedback loop de método faltante priorizado por frecuencia | `calidad §5` | **IMPLEMENTADO** | `missing_methods` con `hit_count`; `syntax_candidates` + `promote_candidate` como gate humano |
| Conjunto de evaluación con métricas (precisión, recall, tasa de falso INVÁLIDO, mala atribución) | `calidad §4` | **AUSENTE** | sin set de relevancia etiquetado; declarado en `sql/001-chunks-schema.sql:49` y en `RAG_SYSTEM_MAP.md §16` (CRITICAL). **Bloquea** toda decisión de ranking |
| Jerarquía de autoridad XPZ de la KB > wiki > manual instalador | `calidad §3` | **AUSENTE** | no hay ingesta de XPZ ni campo de precedencia que se consulte al resolver (`kb_scope` existe y no se usa) |
| Dos capas separadas: retrieval mitiga, validación corta | `README §Principios` | **IMPLEMENTADO** | almacenes distintos por diseño; escrito como regla dura en `CLAUDE.md` |
| Gate humano para el crecimiento del corpus | `crecimiento-controlado` | **IMPLEMENTADO** en MCP / **PARCIAL** en CLI | `server.py:392` (`dry_run=True` por defecto) vs `ingest_dump.py:103` (`--dry-run` opt-in, #13) |
| Tool `search_cross(query_gx, query_bt)` y colección `cross_ref` | `mcp-stack §4`, `§3` | **AUSENTE** | no existen; `CollectionsConfig.cross_ref` declarada sin implementación |
| Tool `get_signature(method, object_type, version)` | `mcp-stack §4` | **PARCIAL** | la función existe (`gx_repository.get_signature`) pero **no está expuesta como tool MCP** |
| `snippet_mode` y `version` en `search_docs` | `mcp-stack §4` | **AUSENTE** | el retrieval no filtra por metadata más allá de `collection`; hay `snippet_chars`, que no es lo mismo |
| Reranker cross-encoder | `mcp-stack §2` | **DESCARTADO A PROPÓSITO** | documentado en `CLAUDE.md` y `RAG_SYSTEM_MAP.md §5.2`: modelo English-trained sobre corpus español, diferido hasta tener uno multilingüe y un set etiquetado |
| ChromaDB como vector DB | `mcp-stack §2` | **DESCARTADO A PROPÓSITO** | reemplazado por Postgres+pgvector en `c4fb609` |
| Catálogo poblado desde XPZ de la KB (`scripts/ingest_xpz.py`) | `docs/CLAUDE.md` | **AUSENTE** | el script no existe; el catálogo se puebla desde la wiki vía `extractor.py` + curación humana |
| `structlog` en cada tool de validación | `docs/CLAUDE.md` | **AUSENTE** | declarado en `requirements.txt`, cero imports (#16) |
| `ruff` / `mypy` en el flujo de calidad | `docs/CLAUDE.md` | **AUSENTE** | sin config de ninguno de los dos, y no hay `pyproject.toml` |

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

## Estado del working tree

✅ **RESUELTO (2026-08-11, auditoría de interfaz).** El snapshot que decía este plan ya no aplica:
todo aquel trabajo está **commiteado** y el working tree quedó **limpio**. HEAD `c5b69fa`
(`feat(bt-docs): ingesta del manual funcional Bantotal y busqueda lexica en espanol`), rama
`vps-produccion`. `RAG_SYSTEM_MAP.md`, `docs/PROMPT-auditoria-agente.md` y `sql/002-unaccent-tsv.sql`
**ya están trackeados** — donde este documento diga "sin trackear", está desactualizado.

Igual **corré `git status` al retomar**: hay varios agentes trabajando en paralelo (ver la regla de
Git Flow en `CLAUDE.md`). Un working tree sucio es trabajo de otro: se integra, no se pisa.

Aquel diff **ya arreglaba mentiras de interfaz reales** y no hay que rehacerlo:

- `add_from_url` declaraba `collection` en su schema y el handler **nunca lo leía** — reportaba
  escribir donde el llamador pedía y escribía donde clasificaba (`server.py:655-670`).
- El `ON CONFLICT` de `_UPSERT_SQL` no actualizaba `collection`, así que un artículo ya indexado
  **nunca se movía** de colección (`pg_indexer.py:41`, con el comentario que documenta la medición).
- `ingest_batch` era un stub con descripción que prometía la capacidad; ahora está implementado con
  `dry_run=True` por defecto (`server.py:367`).

✅ Hecho: está commiteado y la suite quedó en verde, así que el baseline de Fase 0 ya se puede medir
sobre una base estable.

## ⚠️ Trabajo en paralelo aplicado sobre `pg_retriever.py` (2026-08-11) — ya commiteado

Otro agente entró a `src/vectorstore/pg_retriever.py` **después** de escrito este plan. Su cambio ya
está en HEAD (`c5b69fa`); hay que construir encima, no pisarlo:

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
4. El corpus creció. ✅ **Conteos revalidados el 2026-08-11 contra el Postgres real**: `gx_docs`
   **50.063** · `bt_docs` **1.122** · `gx_syntax` **26** · `bt_apis` **0**. Total 51.211 chunks.

## Coordinación entre agentes en paralelo

Las fases están cortadas para minimizar colisión de archivos. Si trabajan dos agentes a la vez:

| Franja | Archivos que toca | Choca con |
|---|---|---|
| Fase 0 (baseline) | `scripts/bench_retrieval.py` (nuevo), `docs/BASELINE-perf.md` (nuevo) | nadie |
| Fase 1 (peso muerto) | `.gitignore`, `requirements.txt`, `src/config.py`, `src/server.py` (solo strings 54 y 146) | Fase 2 en `config.py` si se cablea chunking |
| Fase 2 (perf) | `pg_retriever.py`, `embeddings.py`, `pg_indexer.py` | 🔴 **`pg_retriever.py` está EN DISPUTA** — ver sección de arriba |
| Fase 3 (interfaz) | `pg_collections.py`, `api.py`, `ingest_dump.py`, `frontend/` | nadie |
| ✅ Auditoría de interfaz (2026-08-11, **cerrada**) | `src/syntax_db/gx_repository.py`, `src/server.py` (descripciones + handlers de lectura), `src/vectorstore/pg_collections.py`, `tests/test_interface_contract.py` (nuevo), `tests/test_syntax_catalog.py` | 🔴 tocó `server.py`, que Fase 1 también toca (ahí eran "solo los strings 54 y 146" — ya están corregidos, revalidar líneas) |
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
