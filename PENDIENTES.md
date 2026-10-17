# Pendientes — gx-rag

Backlog abierto tras la migración que reemplazó al MCP `genexus-docs` (2026-07-26).
Cada ítem trae **evidencia**, no impresión. Ordenados por impacto sobre la calidad del RAG.

> **Actualización 2026-10-17** (`docs/AUDITORIA-2026-10-17.md`): corpus medido hoy —
> `gx_docs` **50.063** (wiki 19.783 + SAC 28.155 + WWP 2.125; ya no existe la
> colección `gx_sac`, SAC es `meta.origin='sac'` dentro de `gx_docs`) · `bt_docs`
> **34.198** · `gx_syntax` 26. §1 cerrado (catálogo cargado), §2 resuelto, §3 obsoleto,
> §6bis sigue vigente en el corpus pero con el síntoma documentado ya no reproducible.
> La tabla de abajo es la del cierre de la migración de julio, como registro.

Estado del corpus al cierre de esa migración:

| Colección | Chunks | Naturaleza |
|---|---|---|
| `gx_docs` | 21.901 | GeneXus **normativo** (wiki oficial + WorkWithPlus) |
| `gx_sac` | 28.155 | Tickets de soporte — **NO normativo** |
| `bt_docs` | 6.077 | Manuales Bantotal (2.420 docs; PDFs con página citable) |
| `gx_syntax` | 26 | ⚠️ **NO es el catálogo** — artículos wiki (mirror `docs.genexus.com`). Homónima de la tabla SQLite `gx_syntax` de `syntax.db`, que sí lo es (ver §1) |

---

## 1. El catálogo de `verify_syntax` — ✅ POBLADO Y ACTIVO (2026-10-06; ampliado 2026-10-17)

> **2026-10-17**: el catálogo en Postgres tiene **3.379 filas** (`gxtypes` 706, `wiki` 59,
> `corpus` 2.614 cargadas con `scripts/load_catalog.py --apply`; respaldo previo en la
> tabla `gx_syntax_bk_20261017_pre_h4b`) y **ninguna** con `version_since`: los "706 con
> `version_since`" de abajo eran del SQLite `data/syntax.db`, que el server no lee
> mientras `GXRAG_DSN` esté definida. La lectura del snippet se rehízo
> (`snippet_parser.py`) porque daba INVÁLIDO a código válido según el contexto.

**Estado actual en PostgreSQL (2026-10-06)**:
El catálogo cuenta con **765 filas**, 706 con `version_since` y **96 `object_type`** (tipos de datos del lenguaje y categorías wiki).

| Tabla | Filas |
|---|---|
| `gx_syntax` | **765** |
| `bt_apis` | **0** |
| `validation_log` | 56 |
| `missing_methods` | 11 |
| `syntax_candidates` | 814 |

⚠️ **La evidencia anterior de este ítem ("26 chunks") era incorrecta**: confundía dos stores
**homónimos** — la *colección Chroma* `gx_syntax` (26 chunks, artículos wiki) con la *tabla SQLite*
`gx_syntax` de `syntax.db`, que es la única que lee `verify_syntax` y está en 0. La colección Chroma
no alimenta el veredicto; de hecho sirve bien como **2ª pierna de retrieval** (dio mejor hit de
`For each command` que `gx_docs`). Vale renombrar una de las dos para que no se confundan.

**Comportamiento observado** (5 llamadas, quedaron en `validation_log`):

| snippet | veredicto | realidad |
|---|---|---|
| `Len` | NO_VERIFICABLE | **función VÁLIDA** |
| `Length` | NO_VERIFICABLE | método; no traduce en GX16 |
| `&Texto.Length()` | NO_VERIFICABLE, `token='Texto Length'` | **el tokenizer rompe el method-chain**: pierde el `&` y une receptor+método |

Ese último era un **bug aparte del catálogo vacío**: aunque se poblara, un `&Var.Metodo()` nunca
matchearía porque llegaba tokenizado mal.

### ✅ Resueltos (2026-08-27) — quedaba solo el catálogo vacío

Eran **tres** bugs encadenados, no uno. Dos ya están arreglados con tests (9 nuevos en
`tests/test_syntax_catalog.py`; suite completa **151 passed**):

1. **Tokenizer** (`_normalize_token`) — unía TODAS las palabras del snippet con espacios, así que
   `Trim(&Variable)` producía el token `"Trim Variable"` y no podía igualar nunca un
   `GxSyntax.name` de una palabra. **Cualquier** consulta con argumentos daba NO_VERIFICABLE,
   con catálogo lleno o vacío. Ahora resuelve al nombre invocado, del más específico al más laxo:
   método sobre receptor (`&Texto.Length()` → `Length`) → llamada a función (`Trim(&x)` → `Trim`)
   → identificador suelto (`Len` → `Len`).
2. **Gate de versión** — el código admitía en un `NOTE` que no filtraba y solo dejaba una nota.
   Ahora `version_since` se interpreta como *"existe A PARTIR de"* y se compara por rango numérico
   (`_version_rank`: `'gx16'` → 16, tolerante al texto libre del campo). Si se pregunta por una
   versión ANTERIOR a `version_since` → **INVÁLIDO** con la razón, en vez de un falso VÁLIDO.
   Sin dato de versión NO se filtra: tratar la ausencia como negativa dejaría el catálogo inútil.

Por qué el gate importa tanto: el catálogo se nutre de fuentes **GX18** y las KB Bantotal que lo
consultan son **GX16**. Un falso VÁLIDO es peor que un falso positivo porque nada lo delata — el
código simplemente no compila después. Caso medido: `Abs()` figura en el corpus GX18 y en GX16 da
`src0294 Unknown function`; el procedure `PJCCY657` de la KB Micro tiene **dos workarounds escritos
por eso**.

**Queda abierto solo el ítem 1 de este documento**: poblar el catálogo. Y sigue bloqueado por la
misma decisión de diseño de abajo, que ahora es más urgente porque el gate ya existe y necesita el
dato: **cómo se versiona una entrada**. Con `version_since` poblado, el gate hace su trabajo; sin
poblarlo, no filtra nada.

**Qué haría falta**: poblar el catálogo desde una fuente estructurada. Existe
`src/syntax_db/extractor.py` y el flujo de `missing_methods` (near-misses que quedan
registrados para curación humana). Candidato de arranque: los catálogos TSV verificados que ya
existen fuera del proyecto (`~/.claude/genexus/corpus/api.tsv` — 139 funciones, 381 métodos,
26 comandos; `properties.tsv` 1.880; `events.tsv` 61; `datatypes.tsv` 127).

⚠️ Ese corpus TSV es de **GeneXus 18**. Los KB que consumen este RAG incluyen **GeneXus 16 +
Bantotal**, donde hay divergencias reales y documentadas (p. ej. `.Length()` como método no
traduce al Java de GX16; `Len()` sí). Poblar el catálogo sin marcar la versión sería peor que
tenerlo vacío: daría veredicto `VÁLIDO` a cosas que no compilan en GX16. **Requiere decidir
antes cómo se versiona una entrada del catálogo.**

---

## 2. Manual Descartes duplicado en `bt_docs` (~570 chunks) — ✅ RESUELTO

> **Verificado 2026-10-17**: queda una sola copia de cada manual (Diseñador Web UI
> V3R1.3, 489 chunks; Servidor de Procesos, 104). Los `-extracted.md` ya no están.

**Qué pasa**: el mismo manual entró dos veces — el PDF original y el `-extracted.md` que había
producido el pipeline del proyecto anterior.

| Fuente | Chunks |
|---|---|
| `MDU-10102-GL-V3R1.2 - Descartes - Diseñador Web UI.pdf` | 465 |
| `MDU-10102-Descartes-DisenadorWebUI-extracted.md` | 465 |
| `MDU-10102-GL-V3R1.2 - Descartes - Servidor de Procesos.pdf` | 106 |
| `MDU-10102-Descartes-ServidorProcesos-extracted.md` | 116 |

**Evidencia de daño (no es teórico)**: la query `"Descartes generateScriptArea grilla"` devuelve
**top-3 = el mismo documento**, servido por la versión `.md`, que **no tiene número de página**.
El duplicado desplaza al PDF citable y consume las tres ranuras.

**Decisión propuesta**: quedarse con el PDF (tiene provenance de página, que las hard rules del
KB consumidor exigen citar; además es más completo: 306K chars vs 285K). Ambas versiones son de
texto limpio — no es un problema de calidad de extracción, es literalmente contenido repetido.

**Falta código**: hay `move_matching_by_metadata()` en `src/vectorstore/migrations.py`, pero no
hay `delete_matching_by_metadata()`. Se necesita esa función (con su `--dry-run`, igual que
`scripts/split_collection.py`) antes de borrar nada.

---

## 3. `tests/test_retriever.py` — 5 tests rojos pre-existentes — ⚪ OBSOLETO

> **Verificado 2026-10-17**: el archivo ya no existe (se fue con `retriever.py` y
> ChromaDB). La suite está verde completa.

**No son regresión de la migración.** El fake `_FakeCollection.get()` no acepta los kwargs
`limit`/`offset` que `src/vectorstore/retriever.py:47` ya usa para paginar (la paginación se
agregó para evitar el `too many SQL variables` de SQLite y el fake nunca se actualizó).

```
TypeError: _FakeCollection.get() got an unexpected keyword argument 'limit'
```

Mientras sigan rojos, la suite no sirve de semáforo: cuesta distinguir un fallo nuevo del ruido.

---

## 4. `ingest_local.py` ↔ `ingest_dump.py` duplicados — BAJA

`_dry_run()`, `main()` y `_progress()` son casi idénticos; divergen solo en el iterador de
documentos y el default de `--collection`.

**Esta duplicación ya causó un bug real**: `ingest_dump.py` imprimía `→` en el resumen final, y
al clonarlo `ingest_local.py` heredó el carácter. En consola Windows (cp1252) reventó con
`UnicodeEncodeError` **después** de indexar 6.077 chunks — el trabajo estaba hecho pero parecía
haber crasheado. Se mitigó con `scripts/_console.py` (`use_utf8_stdout()`), pero la duplicación
estructural sigue.

**Propuesta**: extraer `scripts/_ingest_cli.py` con `dry_run_report()` + `run_load()`; cada CLI
aporta solo su iterador. **Riesgo**: bajo, pero **ningún CLI tiene test** → el refactor iría a
ciegas. Escribir primero un test de CLI.

---

## 5. Arranque del server pega ~7 HEAD a HuggingFace — BAJA

Cada arranque del MCP consulta `huggingface.co` para revalidar el modelo (`modules.json`,
`config_sentence_transformers.json`, `README.md`…) aunque ya esté en caché local. Suma latencia
al primer `search_docs` y hace que el server dependa de la red para algo que no la necesita.

**Fix candidato**: `HF_HUB_OFFLINE=1` en el `env` del server en `~/.claude.json`.
**No aplicado** porque si algún archivo faltara en la caché el arranque pasaría de lento a roto.
Verificar primero que la caché esté completa (arrancar con la variable puesta y probar
`search_docs` en las 3 colecciones) antes de dejarlo fijo.

---

## 6bis. Los chunks de *backlinks* desplazan al cuerpo del artículo — MEDIA (era ALTA)

> **Medido 2026-10-17**: 4.449 de 19.783 chunks de wiki (22 %) siguen trayendo la
> sección `Backlinks`, casi siempre pegada al final del cuerpo; 208 la tienen en el
> primer cuarto. Pero la query de abajo ya no lo reproduce: 0 de 6 hits son listas.
> Antes de reindexar `gx_wiki`, medir con el gold set que el fix mejora algo.

_(Agregado 2026-07-27, fuera del orden por impacto para no renumerar. Impacto real: **ALTA**.)_

**Qué pasa**: el chunker parte los artículos de wiki incluyendo el pie de *Backlinks / Table of
contents* como **chunk propio**. Ese chunk es una lista de títulos del mismo tema, así que matchea
casi cualquier query del tema — tanto por BM25 (densidad de keywords) como por dense — y **ocupa
ranuras del top-k desplazando al chunk que tiene la respuesta**.

**Evidencia**: query `"Order clause position in For Each command syntax order before where"` en
`gx_docs`, `top_k=4` → **3 de 4 hits son listas de links** (`Unique Clause`, `Order clause`,
`Database performance from the GeneXus perspective`), todos ~0.030, indistinguibles por score del
hit útil. Los snippets se leen así:

```
Group statement For Each command Order Clause Specification Unique clause in Data Providers
Unique clause in For Each command Unique property Unique property (GeneXus 18 Upgrade 5 or prior)
```

Cero contenido normativo. El cuerpo real de `Order clause` solo se obtuvo con `get_article`, y la
gramática que resuelve la pregunta (`[Order ...] [Where ...] [defined by ...] [Blocking N]`, art.
**4837**) apareció recién con **otra** query.

**Por qué es ALTA**: degrada el retrieval justo en las preguntas de sintaxis, que son el caso de uso
central, y lo hace **de forma silenciosa** — el consumidor recibe 4 resultados con score normal y
ninguna señal de que son índices. Obliga a que `get_article` sea obligatorio en cada consulta
(ya documentado así en el KB consumidor: `kb-micro/docs/mcp-gx-rag.md`), lo que cuesta un roundtrip
extra por pregunta.

**Fix candidato**: detectar y descartar (o desindexar) la sección de backlinks al chunkear —
heurística: chunk sin puntuación de oración, alta proporción de títulos wiki, o el corte por el
encabezado `Backlinks`/`Table of contents` cuando el scraper lo preserva. Igual que el boilerplate
de PDF (§Notas de operación), es ruido repetido estructural, no contenido.
**Riesgo**: re-chunkear implica reingestar `gx_docs` (21.9K chunks) → hacerlo con `--dry-run` y
medir el delta de conteo antes de reemplazar.

---

## 6. Reranker multilingüe cableado — ✅ RESUELTO (2026-10-06)

Se cableó `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` en `src/config.py`, `src/vectorstore/reranker.py`
y `src/server.py` (`search_docs(rerank=True)`). El modelo cuenta con entrenamiento multilingüe nativo
en español, atención cruzada completa sobre candidatos y fallback explícito a RRF etiquetado si el
modelo no está disponible.


---

## Resuelto durante la migración (queda como registro)

**Deadlock del MCP al primer `search_docs`** — el server aceptaba `initialize` y `tools/list`,
pero **cualquier llamada a herramienta colgaba para siempre** (probado >600 s, proceso a 63 MB y
0,8 s de CPU: bloqueado, no lento). Habría dejado el MCP inservible en Claude Code.

Causa raíz (stack vía `py-spy dump`):

```
create_module (<frozen importlib._bootstrap_external>:1321)   ← cargando multiarray.pyd
<module> (numpy\_core\multiarray.py:11)
...
dispatch_tool (src\server.py:262)        ← el import lazy
call_tool (src\server.py:248)
run_forever (asyncio\base_events.py:683) ← dentro del event loop
```

`dispatch_tool` hacía **7 imports lazy** dentro del handler async. Cargar una extensión nativa
(`.pyd` de numpy/torch/chromadb) desde dentro del event loop de asyncio en Windows deadlockea en
el loader del SO.

**Fix**: todos esos imports subidos a nivel de módulo en `src/server.py`, con comentario
explicando por qué no pueden volver a ser lazy. Beneficio extra: el costo de importar y cargar el
modelo se paga al arrancar, no dentro de la primera llamada (que igual habría reventado el
timeout del cliente).

**Verificación**: handshake MCP stdio real, levantando el server con el mismo `command`/`args`/
`env` de `~/.claude.json` — `tools/list` (7 tools) + `search_docs` en `gx_docs`/`bt_docs`/`gx_sac`
+ `verify_syntax`, todos con respuesta.

---

## Notas de operación (no son pendientes)

- El `score` que devuelve `search_docs` es **RRF (basado en rank), NO una similitud calibrada**.
  Mayor es mejor; el valor absoluto no es una probabilidad. No usarlo como umbral de confianza.
- La estimación de chunks del `--dry-run` **subestima**: calcula `len(texto) // 800` e ignora el
  overlap del splitter. Real observado: estimó ~4.130, indexó 6.077. Sirve como orden de
  magnitud, no como conteo.
- Los PDFs se ingestan **una página = un documento**, y se descartan las líneas que aparecen en
  más del 50% de las páginas (encabezado, copyright, "Página N de M"). En los manuales Bantotal
  ese boilerplate era ~121 chars sobre una página mediana de 492 → **~25% de cada chunk** era
  ruido repetido.
