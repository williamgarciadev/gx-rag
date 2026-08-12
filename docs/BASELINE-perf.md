# Baseline de performance del retrieval — Fase 0

> Medido el **2026-08-12** con `scripts/bench_retrieval.py`, contra el Postgres de
> desarrollo (`localhost:5433`) con el corpus real. HEAD `2db2e71`.
> **Este es el número contra el que se compara toda la Fase 2.** Si una optimización
> no mejora esto medido acá, se revierte.

## Entorno

| | |
|---|---|
| PostgreSQL | **16.2** |
| pgvector | **0.8.6** |
| `hnsw.ef_search` | **40** (default) |
| `work_mem` | 4 MB · `shared_buffers` 128 MB · `max_parallel_workers_per_gather` 2 |
| Corpus | `gx_docs` 50.063 · `bt_docs` 1.122 · `gx_syntax` 26 |
| Retrieval | `RRF_K=60`, `CANDIDATE_POOL=30`, `intfloat/multilingual-e5-large` |

> `hnsw.ef_search` **no se puede leer con un `SHOW` a secas** al abrir la sesión:
> pgvector registra el GUC cuando la extensión se carga, no cuando se instala. El
> harness fuerza la carga con una operación trivial de `vector` antes de consultarlo;
> sin eso devuelve "no disponible" y parece que la config no existe.

## Metodología, y por qué importa

**Las dos mitades se cronometran por separado:** `embedding` (e5-large, CPU) y `SQL`
(pgvector + tsvector + RRF). Sin esa separación no se sabe cuál domina y la Fase 2
optimizaría a ciegas.

Dos decisiones que cambian el resultado:

- **`embed_texts.cache_clear()` antes de cada medición.** El módulo tiene
  `lru_cache`, así que sin limpiarla la 2ª repetición mide un **hit de caché
  (~0,6 ms)** en vez del costo real del modelo (~115 ms). La primera versión de este
  baseline decía "embedding = 0% del tiempo" por ese motivo, y era falso. Lo que se
  mide acá es la **consulta nueva**, que es lo que paga un agente cada vez.
- **El warm-up queda FUERA.** Cargar el modelo cuesta **6,7–8,7 s**; contarlo
  mezclaría el arranque en frío con la latencia de servicio. Ese costo es el hallazgo
  #8, aparte.

## Resultados (5 consultas × 3 repeticiones por colección, `top_k=5`)

| Colección | embedding p50 | SQL p50 | SQL p95 | **TOTAL p50** | TOTAL p95 | reparto p50 |
|---|---|---|---|---|---|---|
| `gx_docs` (50k) | 114 ms | **85 ms** | 245 ms | **212 ms** | 363 ms | embed 54% · SQL 40% |
| `bt_docs` (1,1k) | 118 ms | 24 ms | 27 ms | **140 ms** | 154 ms | embed 84% · SQL 17% |
| `gx_syntax` (26) | 135 ms | 3 ms | 4 ms | **138 ms** | 144 ms | embed 98% · SQL 2% |

### Lo que esto cambia respecto de lo que el plan asumía

**El embedding es el costo dominante, y es constante: ~115–135 ms por consulta,
independiente del tamaño de la colección.** El plan trataba el SQL como el problema
principal («latencia de **toda** query»), y sólo lo es en `gx_docs`. En `bt_docs` y
`gx_syntax` el SQL es 24 ms y 3 ms: optimizarlo ahí **no mueve el total**.

Corolario para la Fase 2: **el techo de mejora por tocar SQL es el 40% del p50 y sólo
en `gx_docs`.** Cachear o abaratar el embedding tiene más alcance — y el `lru_cache`
que ya existe lo baja a ~0,6 ms cuando la consulta se repite.

## `EXPLAIN (ANALYZE, BUFFERS)` — el hallazgo #1 queda REFUTADO

`gx_docs`, consulta `"for each command"`, **206 ms** de ejecución:

```
Limit  (actual time=205.550..205.555 rows=5)
  -> Nested Loop
     -> GroupAggregate  (el RRF)
        -> Append
           -> Subquery Scan on dense    (actual time=9.374..9.836 rows=30)   ←  4,8%
              -> Limit -> WindowAgg
                 -> Index Scan using chunks_embedding_hnsw on chunks   ← SE USA
                       Order By: (embedding <=> '[…1024 dims…]')
                       Filter: (collection = 'gx_docs')
                       Rows Removed by Filter: 5
           -> Subquery Scan on lexical  (actual time=192.906..192.930 rows=30)  ← 94%
              -> Limit -> WindowAgg
                 -> Sort   Sort Method: quicksort  Memory: 1666kB
                       Sort Key: (ts_rank_cd(tsv, '''for'' | ''each'' | ''command''')) DESC
Execution Time: 206.156 ms
```

**El `ROW_NUMBER()` NO anula el índice HNSW.** El CTE `dense` usa
`Index Scan using chunks_embedding_hnsw` y tarda **9,8 ms**: el planner empuja el
`LIMIT 30` dentro del índice sin problema. El fix que el plan proponía —mover el
`LIMIT` a una subquery interna— **no habría cambiado nada**, porque el denso ya está
bien. Hallazgo #1 → **DESCARTADO con evidencia.**

**El cuello de botella real es el CTE léxico: 193 ms de 206, el 94%.** Ordena por
`ts_rank_cd` **todo el match set** de la tsquery OR (`quicksort`, 1.666 kB,
~38.800 buffers) antes de aplicar su `LIMIT 30`.

> 🔴 **#1 y #40 son el mismo problema.** El hallazgo #40 (el OR de `_to_tsquery`
> perdió el IDF al migrar de BM25) se había clasificado como problema de *calidad*.
> Es también **el driver de latencia**: `for` matchea 14.844 chunks, y ordenarlos
> cuesta esos 193 ms. Se ve en los números por consulta: `"for each command"`
> → 186 ms de SQL, `"business component insert"` → 38 ms. La diferencia es cuántas
> filas matchea el OR.

Contraste con `gx_syntax` (26 filas): **0,56 ms** de ejecución total. La latencia
escala con el match set léxico, no con el denso.

## Qué medir en la Fase 2, y contra qué

1. **Atacar el CTE léxico (#40)** — es el 94% del SQL en `gx_docs` y a la vez el
   problema de calidad. Comparar OR vs `plainto_tsquery` vs `websearch_to_tsquery`
   vs filtrar términos por frecuencia documental, midiendo **latencia (acá) y ruteo
   (`scripts/eval_bt_docs.py`) juntos**: un cambio que acelere y empeore el ranking
   no sirve.
2. **El embedding**, que es el 54–98% del total. Opciones sin invalidar los 51k
   vectores ya indexados: `lru_cache` sobre `embed()` en el camino de query,
   pre-warm del modelo, batch. Cambiar de modelo o agregar prefijos `query:`/
   `passage:` **exige reindexar todo** y no entra acá.
3. **NO** reescribir los CTE por el `ROW_NUMBER` (#1 descartado).
4. `hnsw.ef_search=40` con `CANDIDATE_POOL=30` (#4) sigue sin medir, pero su techo es
   esos 9,8 ms del denso: impacto acotado.

## Reproducir

```bash
.venv/Scripts/python.exe scripts/bench_retrieval.py --repeat 3
.venv/Scripts/python.exe scripts/bench_retrieval.py --explain --collection gx_syntax
```

⚠️ Los números de acá son del **entorno de desarrollo** (Windows, Postgres local). El
VPS comparte CPU con otras apps y tiene otro perfil: para comparar contra producción,
correr el harness allá. No mezclar las dos series.
