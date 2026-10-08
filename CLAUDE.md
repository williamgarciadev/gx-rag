# CLAUDE.md

Guía para trabajar en este repo. **Verificado contra el código el 2026-10-08**; lo fechado así se
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

| Necesitas | Lee |
|---|---|
| La arquitectura real, con clasificación de evidencia por afirmación | **`RAG_SYSTEM_MAP.md`** — reconstruido desde el código, es el mapa autoritativo |
| El QUÉ SE QUERÍA (hay requisitos escritos y sin implementar) | `docs/` — ⚠️ `docs/CLAUDE.md` es **spec de planeación, no realidad** |
| Auditar el repo contra sus propios docs | `docs/PROMPT-auditoria-agente.md` y `docs/AUDITORIA-2026-10-05.md` |
| **Trabajo de optimización en curso** (hallazgos medidos, fases, coordinación entre agentes) | **`docs/PLAN-optimizacion.md`** — empieza por su sección "Cómo arrancar" |
| **Cómo se ingiere un lote del portal SGR** (pasos, herramientas, qué quedó pendiente por lote) | **`docs/PLAN-INGESTA-SGR.md`** — cada fila tiene el resultado verificado del lote |
| **Qué documentación Bantotal se trajo a `bt_docs`, de dónde y qué lote sigue** (portal SGR, criterio Global + Colombia) | **`docs/INGESTA-BANTOTAL.md`** — antes de traer documentos, para no repetir trabajo |

## Commands

```bash
# Full stack (requiere red externa: docker network create infra_gateway)
docker compose up
# Servicios: gx-rag-api (FastAPI, puerto host 8000), gx-rag-ui (nginx :80 interno, sin puerto host)

# Backend local — SIEMPRE en venv (ver nota abajo)
# Linux/Mac:
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn src.api:app --reload --port 8000   # HTTP API
.venv/bin/python -m src.server                        # MCP server (stdio)
.venv/bin/python -m pytest tests/ -q                 # Tests — 509 passing al 2026-10-08

# Windows (PowerShell):
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn src.api:app --reload --port 8000
.venv\Scripts\python -m src.server
.venv\Scripts\pytest tests/ -q                        # Tests — 509 passing al 2026-10-08
# Marcadores (pytest.ini, --strict-markers): unit | integration

# Ingesta
.venv/bin/python scripts/scrape_wiki.py <url> [--output-dir <dir>]
.venv/bin/python -m scripts.ingest_dump <file> --format genexus|workwithplus [--dry-run]

# Lote de documentos locales (portal SGR): una lista UTF-8 con una ruta por línea
.venv/bin/python -m scripts.scan_secrets --list <lista>     # 1. secretos (informativo)
.venv/bin/python -m scripts.ingest_lote <lista> [--dry-run]  # 2. ingesta con 4 hilos y prioridad baja
.venv/bin/python -m scripts.verify_ingest --list <lista>    # 3. ¿entró ENTERO? (lectura directa de la base)
.venv/bin/python -m scripts.eval_etp_titles --json out.json  # 4. ¿se encuentra cada ETP por tema y por código?

# Frontend (frontend/) — Vite, puerto 5173. NECESITA el backend arriba:
.venv/Scripts/python.exe -m uvicorn src.api:app --host 127.0.0.1 --port 8000   # en otra terminal
npm install && npm run dev
# El proxy apunta por defecto a http://127.0.0.1:8000. Para otro destino:
VITE_API_TARGET=http://otro-host:8000 npm run dev
```

**Puertos**: API `8000`, frontend dev `5173` (Vite incrementa a 5174+ si está tomado). El frontend
llama `/api/*` y Vite lo proxea a `VITE_API_TARGET`.

**`./dev.sh`** levanta backend + frontend juntos en **8050** / **3050** para no colisionar con otros
proyectos de esta máquina (`gx-docs-v1` usa `:8000`). Override con `BACKEND_PORT` / `FRONTEND_PORT`.

**Corre en el venv, no en el intérprete global.** Un paquete global (`notebooklm_skill`) trae un
package `scripts` de nivel superior que tapa el `scripts/` de este repo (que no tiene `__init__.py`,
así que es namespace package implícito) y rompe `from scripts import scrape_wiki` en `src/api.py`.
Docker no se ve afectado (imagen limpia).

**No existe `pyproject.toml`**: dependencias en `requirements.txt`, config de tests en `pytest.ini`.
Sin ruff ni mypy configurados.

## Architecture

Dos entrypoints sobre un mismo core de ingesta/retrieval:

- **`src/server.py`** — MCP server (SDK oficial `mcp`, stdio). **8 tools**, separadas por permiso:
  - `READ_TOOLS` (5): `search_docs`, `get_article`, `get_document`, `verify_syntax`, `list_methods`.
  - `WRITE_TOOLS` (3): `add_from_url`, `ingest_batch`, `ingest_kb_objects`. **Mutan el corpus ⇒ quedan FUERA del
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
   **Postgres**. `reindex_if_changed()` compara `content_hash` y se salta lo que no cambió, sin re-embeddear.
5. **Retrieve** (`src/vectorstore/pg_retriever.py::hybrid_search`): **denso (pgvector) + full-text
   (`ts_rank_cd`) fusionados con RRF (`RRF_K=60`), resuelto EN SQL** con un CTE por criterio.
   La mitad densa recorre un **HNSW parcial por colección** (`sql/006`): con uno global, crecer una
   colección le cambiaba el ranking a las demás. La léxica hace OR **sin palabras vacías**
   (`src/vectorstore/stopwords_es.txt`): `ts_rank_cd` **no es BM25, no tiene IDF**. El
   reranker (`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) está **cableado y disponible vía `rerank=True`**:
   es multilingüe (soporta español de forma nativa), calcula atención cruzada completa sobre candidatos
   y provee fallback explícito a RRF si el modelo no está disponible.
6. **Valida** (`src/syntax_db/`): catálogo curado vía SQLAlchemy. **Vive en Postgres si `GXRAG_DSN`
   está definida, y cae a SQLite (`data/syntax.db`) si no** (`models.py:113-140`) — o sea que en este
   entorno vive en Postgres, no en el archivo. `gx_repository.verify_syntax()` da veredicto binario —
   match exacto → `VÁLIDO`, match solo difuso → `INVÁLIDO`, nada parecido o **snippet sin tokens** →
   `NO_VERIFICABLE`. Todo veredicto se loguea; los near-miss van a `missing_methods` con `hit_count`
   para curación humana.

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
- Usa el SDK oficial `mcp`, no FastMCP standalone.
- Sin LangChain/LlamaIndex como framework base — solo `langchain-text-splitters` para chunking.
- Consulta PyPI para cualquier número de versión: no los escribas de memoria.
- Una fase está lista cuando el comportamiento se verificó funcionando, no cuando compila.
- **Después de una escritura, verifica el estado por una vía independiente del valor de retorno.**
  El retorno de una tool no describe el store: para probar pertenencia usa una lectura determinista
  (`get_article` por colección), no una búsqueda por ranking.
- **Cada fix lleva un test que falla SIN el fix.** Un test que solo pasa después no demuestra que
  proteja el bug original.

## Git Flow y trabajo concurrente

**En este repo trabajan varios agentes en paralelo, y ya hubo colisión real**: el 2026-08-11
`src/vectorstore/pg_retriever.py` quedó en disputa entre dos agentes (documentado en
`docs/PLAN-optimizacion.md`, sección "Trabajo en paralelo").

### 🔴 Lo primero, porque es lo que se malentiende: la rama es del CLON, no del agente

Un repo tiene **un solo working tree**. Dos agentes en el mismo directorio **están en la misma
rama, siempre** — si uno hace `git checkout`, se lo cambia al otro por debajo, y si el otro tenía
cambios sin commitear se los arrastra o el checkout falla. La tabla de ramas de más abajo NO da
paralelismo por sí sola.

**Antes de editar, siempre tres cosas** (no dos):

```bash
git branch --show-current    # ¿en qué rama me dejó el anterior?
git status --short           # ¿hay trabajo ajeno sin commitear?
git log --oneline -3         # ¿el HEAD es el que dice el plan que estoy ejecutando?
```

Un working tree sucio es trabajo de otro: **se integra, no se pisa.** Y **no cambies de rama con
cambios ajenos encima** — commitéalos en la rama donde están o pregunta.

**Dos agentes, dos modos:**

| Situación | Qué hacer |
|---|---|
| Comparten el clon (caso habitual) | **Comparten rama.** El que llega segundo NO hace `checkout`: trabaja en la rama activa y **declara su franja de archivos** en la tabla de coordinación de `docs/PLAN-optimizacion.md`, eligiendo una que no choque |
| Se quiere paralelismo REAL por rama | `git worktree add ../gx-rag-<tarea> -b <rama>` — directorio propio, rama propia, mismo `.git`. Es la única forma de que dos agentes estén en ramas distintas a la vez |

El checkout del VPS (`/home/admin/.openclaw/workspace/gx-bt-rag-mcp`) es **otro clon**, así que no
interfiere: puede estar en `vps-produccion` mientras aquí se trabaja en otra rama.

⚠️ Ojo con el `git worktree` en esta máquina: cada sesión de agente levanta su propio `src.server`
con su modelo (~3,5 GB de commit reservado). Más worktrees no cuestan RAM por sí mismos, pero más
sesiones sí.
### Ramas, commits y despliegue

- **Ramas — tres roles, y no se mezclan** (decidido 2026-08-12; **sin PR desde el 2026-10-07**):

  | Rama | Rol | Regla |
  |---|---|---|
  | `main` | integración | **Sin PR** (decisión del usuario, 2026-10-07): el trabajo entra con `merge --ff-only` y se pushea. Antes de integrar: `git fetch`, rebasar sobre `origin/main` y correr la suite completa |
  | rama por tarea | trabajo | opcional, se crea **desde `main`** (`fix/...`, `docs/...`) y se borra al integrar. Sirve para agrupar commits, no para revisión |
  | `vps-produccion` | **puntero de release** | NO se desarrolla aquí. Se le hace `merge --ff-only main` **sólo al desplegar**. Es la rama que el VPS tiene checkouteada |

  **Por qué separar release de integración:** un push **no despliega** —el código va horneado en la
  imagen— y el build cuesta ~15 min. "Integrado" y "desplegado" son estados distintos, así que
  tienen rama distinta: puedes mergear a `main` sin tocar producción, y desplegar es un acto
  explícito.

  ⚠️ Durante los primeros 49 commits `vps-produccion` fue la rama de trabajo, con nombre de
  ambiente. Si ves historia vieja ahí, es por eso.
- **Commits atómicos**: un fix + su test = un commit. Formato conventional commits, en imperativo.
  Sin `Co-Authored-By` ni ninguna atribución de IA.
- **Nunca commitear ni pushear sin pedido explícito del usuario.**
- **Un push NO despliega.** Los tres servicios del compose usan `build: .`, así que el código va
  horneado en la imagen: hasta que alguien haga `docker compose up -d --build` en el VPS, el endpoint
  público sigue sirviendo el código anterior — **incluidas las descripciones de tools viejas, que son
  el prompt del agente consumidor**. Procedimiento y verificación en
  `docs/deploy-vps.md` → "Actualizar un deploy que ya está corriendo".
- **Nunca versionar datos**: `data/syntax.db` (binario mutable, con telemetría real de validación) ni
  `data/scraped_sac/` (1002 archivos). Diffs opacos y conflictos irresolubles.
- **Reiniciar el MCP no es automático**: el proceso conserva el código viejo en memoria, así que un
  cambio se verifica por MCP solo después de reconectar el server.

## Known Gotchas

- **Un `top_k` chico NO prueba una ausencia.** `gx_docs` tiene ~50k chunks y el ranking mezcla
  fuentes: un tema presente puede no entrar en los primeros 3. Para afirmar que algo no está, sube
  `top_k` y reformula con términos del dominio.
- **`gx_docs` mezcla tres fuentes**: wiki GeneXus, tickets SAC (`sac.genexus.com`) y wiki de
  WorkWithPlus (`docs.workwithplus.com`). GeneXus tiene un pattern nativo *Work With for Web* y WWP
  (DVelop) es otro producto: una query sobre "el pattern" devuelve los dos entremezclados. **Filtra
  por procedencia (la url del hit), no por presencia.**
- **Estado del corpus, medido 2026-10-08**: `gx_docs` 50.063 chunks · `bt_docs` **~29.880** (manuales
  MDI/MDU, ETP y ECP del portal SGR, más los videos y manuales curados de antes) · `gx_syntax` 26 · `bt_apis` **0**. `search_docs`
  avisa qué colecciones tienen contenido cuando consultas una vacía.
- **Los metadatos de un PDF salen SOLO de su nombre de archivo** (`MDI-10201-CO-V3R1.22 - …`):
  `manual`, `manual_version` y `localization`. Un nombre fuera de formato deja el chunk sin ellos, y la
  carpeta no corrige nada (el F1000-48 es de Colombia y el portal lo archiva en Uruguay). Para un
  documento así, transcríbelo a `.md` con frontmatter (`localization: CO`) y pon el país en el título:
  el agente lee el texto del chunk, no los metadatos. Ojo: el frontmatter también se indexa como chunk.
- **El catálogo del validador (`gx_syntax`, SQL) tiene 765 entradas, 706 con `version_since` y 96 `object_type`.**
  Las fuentes principales provienen de GeneXus 18 (incluyendo `gxtypes.xml`). Para una versión previa
  como `gx16` (las KBs Bantotal), un método no diferenciable por versión sale como veredicto `PISTA`
  (no `VÁLIDO`), indicando que debe verificarse contra un objeto de la KB. `list_methods` no filtra
  por versión pero lista los métodos disponibles por tipo.
- **`object_type` NO es el tipo de objeto GeneXus (Transaction, Procedure...).** Contiene 96 tipos reales
  divididos en dos familias: (1) tipos de dato del lenguaje con sus métodos (`DateTime`, `Date`,
  `Character`, `Collection`, `File`, `BusinessComponent`, etc.); (2) categorías de la wiki (`Method`,
  `Event`, `Command`, `Rule`, `Property`). Pedir `Transaction` o `Procedure` devuelve vacío por
  taxonomía ajena al catálogo.
- **El `tsv` de la base viva usa la config `es_simple`** (= `simple` + `unaccent`, ver
  `sql/002-unaccent-tsv.sql`), y `pg_retriever` consulta con esa misma config. ⚠️ Pero
  `sql/001-chunks-schema.sql` todavía declara `'simple'`: **un install fresco que corra solo el 001
  queda incoherente con el retriever**. Hay que aplicar los `sql/0*.sql` en orden.
- 🔴 **Un mismo artículo entra con DOS identidades según el camino de ingesta**, y eso rompe la
  idempotencia: `add_from_url` normaliza el `source_key` (`normalize_wiki_url`) y el dump usa la URL
  cruda. Como `reindex_if_changed` deduplica por `source_key`, **un artículo ya ingerido por dump se
  DUPLICA si después entra por URL**. Medido con el artículo WWP 1149. Pendiente unificar el esquema.
- **El proceso MCP conserva el código viejo en memoria**: un cambio se verifica por MCP solo
  después de reconectar el server.
- Chunk size: `config.py` declara 1000/200 pero `pg_indexer.py` usa 800/100 — **manda el módulo**, los
  valores del config no se leen.
- El `score` del retrieval es RRF (posicional, rank-based), **no** una similitud calibrada: más alto
  es mejor, pero el valor absoluto no es una probabilidad.
- Los overrides anidados de env-var en `config.py` probablemente no funcionan (las clases anidadas de
  pydantic-settings no heredan el prefijo `GX_` de forma consistente).
- `docs/gx-bt-rag-mcp-stack.md` especifica firmas más ricas y una colección `cross_ref` que no están
  implementadas.
