# Auditoría gx-rag — 2026-10-17

Revisión de defectos y mejoras con evidencia reproducida. **No se implementó nada.** HEAD `2730bbf`
(`main`), suite **520 passed**. **Actualización (misma fecha):** H1, H2 y H3 quedaron resueltos en la fase C1 de
`docs/PLAN-arquitectura.md` (`src/syntax_db/snippet_parser.py`). Sus tests están en
`tests/test_syntax_catalog.py` y `tests/test_snippet_parser.py`. H4a y H7 quedaron resueltos en la fase C1b; H4b (cargar el catálogo) espera decisión. Las reproducciones de `verify_syntax` se
hicieron con `_log_verdict` y `_record_missing` neutralizados, para no ensuciar la telemetría real.

## 0. La premisa del prompt estaba desactualizada

| Dato del prompt (2026-10-06) | Medido hoy (`SELECT collection, count(*) FROM chunks`) |
|---|---|
| `gx_sac` 28.155 como colección | **No existe.** SAC vive dentro de `gx_docs` como `meta.origin='sac'` (28.155) |
| `gx_docs` 21.901 | **50.063** = wiki 19.783 + sac 28.155 + wwp 2.125 |
| `bt_docs` 6.077 | **34.198** |
| catálogo 765 | 765 ✅, pero **los 765 tienen `version_since` NULL** (ver H5) |

---

## Hallazgos, por impacto sobre el caso central (verificar sintaxis sin alucinar)

### H1 — `verify_syntax` da INVÁLIDO a código válido cuando la llamada no abre el snippet · CRÍTICO · defecto funcional silencioso

- **Evidencia**: VERIFIED. `gx_repository.py:81` busca la función con un regex anclado (`^Ident\(`);
  si el snippet empieza con otra cosa, `:86` cae a `_normalize_token`, que une **todos** los tokens
  (`'x Trim y'`), y `_find_similar` (`:249-252`) encuentra `Trim` como subcadena → INVÁLIDO.
- **Reproducción** (catálogo real, `version='gx18'`):

  | snippet | veredicto | debería |
  |---|---|---|
  | `Trim(&y)` | VÁLIDO | VÁLIDO |
  | `&x = Trim(&y)` | **INVÁLIDO**, sugerencias `['Trim']` | VÁLIDO |
  | `If Len(&s) > 0` | **INVÁLIDO**, sugerencias `['Length', …]` | NO_VERIFICABLE (no catalogado, H4) |
  | `For Each Customer` | **INVÁLIDO**, token `'Each Customer'` | NO_VERIFICABLE |

- **Impacto**: el agente pega una línea real de código (el uso natural) y recibe "¿quisiste decir
  Trim?" cuando escribió `Trim`. La guía del veredicto dice que INVÁLIDO es un detector de typo, así
  que el agente busca el typo inexistente o reescribe código correcto. La capa que debía **cortar**
  la alucinación la **induce**. El PENDIENTES §1 da este tokenizer por resuelto; lo está solo para
  snippets que empiezan en la llamada.
- **Fix mínimo**: en `_extract_candidate`, (1) quitar los literales de texto antes de parsear (ver
  H2); (2) buscar la primera `Ident(` **sin anclar**, saltando palabras de control (`If`, `Do`,
  `For`, `Each`, `Where`…); (3) no caer nunca a la concatenación de tokens como `query_token`.
- **Test** (rojo hoy, confirmado): `test_contexto_de_sentencia_no_vuelve_invalido_lo_catalogado`
  con `&x = Trim(&y)` y `If Trim(&s) = ''`.
- **Costo de verificación**: bajo. Unit tests con `temp_db`, segundos.

### H2 — Un punto dentro de un literal se toma como invocación de miembro · ALTO · defecto silencioso

- **Evidencia**: VERIFIED. `_RECEIVER_MEMBER` (`gx_repository.py:60`) se aplica con `.search` sobre
  el snippet crudo (`:75`), literales incluidos.
- **Reproducción**: `Msg('Error. Revise los datos')` → **INVÁLIDO**, token `Revise`, sugerencias
  `['Resume', 'Remove', 'RemoveUser', 'ReadLine', 'Renew']`. `Msg('Hola')` → VÁLIDO (`Msg rule`).
- **Impacto**: todo mensaje de error con punto —la norma en los `msg()`/`error()` de Bantotal—
  invalida la regla que lo contiene.
- **Fix**: el mismo paso (1) de H1: `re.sub(r"'[^']*'|\"[^\"]*\"", "''", snippet)` antes de extraer.
- **Test** (rojo hoy): `test_literal_con_punto_no_se_toma_como_receptor`.
- **Costo**: bajo.

### H3 — Un receptor que no es tipo catalogado se trata como tipo · ALTO · defecto silencioso

- **Evidencia**: VERIFIED. `gx_repository.py:304` considera "clase concreta" a **cualquier** receptor
  sin `&`; si el miembro existe en otro tipo, `:332-339` responde INVÁLIDO "no pertenece al tipo".
- **Reproducción**: `PJCCY657.Call(&a)` → **INVÁLIDO**: *"'Call' no pertenece al tipo 'PJCCY657'
  (disponible en: GXDataSelector, GXProgram, SDObject)"*.
- **Impacto**: llamar a un procedure de la KB por nombre es la sintaxis más común del código
  Bantotal, y aquí sale acusada con una razón que **suena** definitiva.
- **Fix**: tratar el receptor como tipo solo si está en `list_object_types()` (comparación sin
  mayúsculas); si no, igual que `&var`: receptor desconocido.
- **Test** (rojo hoy): `test_receptor_desconocido_no_acusa_pertenencia`. Control que debe seguir
  verde (verde hoy): `test_receptor_que_si_es_tipo_catalogado_sigue_detectando_el_cruce`
  (`Date.Trim()` → INVÁLIDO).
- **Costo**: bajo.

### H4 — El catálogo no tiene NINGUNA función, y la descripción de la tool dice que sí · ALTO

- **Evidencia**: VERIFIED. `gx_syntax` en Postgres: `gxtypes` 706 + `wiki` 59; 0 filas de tipo
  función; tampoco `For Each`, `If`, `Len`, `Abs`, `Upper`, `Substr`, `Null`, `Str`, `Val`. Los
  únicos 8 `Command` son Print/Header/Footer/Refresh. Mientras tanto, `server.py:344-346` promete
  *"comandos, funciones y eventos de la wiki"*. `scripts/load_catalog.py` (dry-run) cargaría
  **2.614** entradas, 139 de ellas funciones: nunca se aplicó en este store.
- **Reproducción**: `Abs(&n)` con `gx18` → INVÁLIDO, sugerencias `['Abort', 'SaveAs', 'Assign',
  'AddUser', 'AddDays']`. `Abs` está en `api.tsv` (wiki 57730).
- **Impacto**: toda función estándar sale INVÁLIDO con sugerencias sin relación. `difflib` con
  `cutoff=0.4` (`:259`) y el prefijo de 4 letras (`:252`) son demasiado laxos para llamar a eso
  "typo".
- **Fix, en dos pasos**: (a) inmediato: corregir la descripción y devolver NO_VERIFICABLE cuando
  la mejor similitud quede bajo ~0,75; (b) con gate humano: `load_catalog.py --apply`. Con gx16
  esas entradas responden PISTA, que es lo honesto (`Abs` no existe en GX16).
- **Test**: (a) unit: con el catálogo sembrado con `Abort`/`AddDays`, `verify_syntax("Abs(&n)")`
  ≠ INVÁLIDO; (b) integration (marcador `integration`, contra PG): `Len(&s)` → VÁLIDO.
- **Costo**: (a) bajo; (b) medio: hay que revisar el efecto de los 1.481 nombres multipalabra en
  las sugerencias, y la tabla de veredictos cambia para todos los consumidores.

### H5 — `version_since`: los documentos describen otro store · MEDIO · deuda, con un defecto latente

- **Evidencia**: VERIFIED. El server carga `.env` (`src/__init__.py:20`) → `GXRAG_DSN` → Postgres
  (`models.py` `get_engine`). Ahí, **765/765 con `version_since` NULL**. Los "706 con
  `version_since`" que citan `CLAUDE.md` y `PENDIENTES.md` §1 son del SQLite `data/syntax.db`, que
  el server no lee, y valen `'18.0.15'`: la versión del install, la semántica que la memoria
  `catalogo-version-since-semantica` ya marcó como errónea.
- **Consecuencia actual**: la rama INVÁLIDO-por-versión (`gx_repository.py:358-377`) está muerta en
  producción; gx16 siempre da PISTA. El comportamiento es correcto; los documentos no.
- **Defecto latente** (INFERRED, no reproducido en runtime): un proceso sin `GXRAG_DSN` cae al
  SQLite, y con `18.0.15` **todo** lo que se consulte con gx16 sale INVÁLIDO.
- **Fix**: corregir `CLAUDE.md`/`PENDIENTES.md`, y retirar o vaciar `data/syntax.db`, o hacer que
  `get_engine` falle ruidoso sin DSN fuera de los tests.
- **Test**: `get_engine()` sin DSN ni `db_path` explícito → error, no SQLite silencioso.
- **Costo**: bajo.

### H6 — Backlinks (known issue): siguen en el corpus, pero el síntoma documentado ya no aparece · MEDIO

- **Evidencia**: VERIFIED. 4.449 de 19.783 chunks `gx_wiki` (22 %) contienen la sección
  `Backlinks`, casi siempre pegada al final del cuerpo (`… See Also … Backlinks Toc:…`); 208 la
  tienen en el primer cuarto del chunk. No hay filtro en `scripts/` ni en `src/ingestion/`.
- **Reproducción del síntoma original** (`hybrid_search`, `gx_docs`, `origin=gx_wiki`, `top_k=6`,
  query del PENDIENTES §6bis): **0 de 6** hits son listas de enlaces. Antes eran 3 de 4.
- **Impacto**: ruido léxico repartido y no medido. Que ya no se vea en esa query no prueba que no
  degrade otras.
- **Fix**: cortar desde `Backlinks` hasta el final al convertir a Markdown, y reindexar `gx_wiki`
  (cambia el `content_hash` de ~4,4 k artículos → re-embedding).
- **Precondición** (regla 6): medir antes y después con el gold set (`scripts/eval_retrieval.py`).
  Sin esa medición, el fix es un costo de horas de embedding sin beneficio demostrado.
- **Test**: unit sobre la función de limpieza con un Markdown de muestra.
- **Costo**: alto (reindexado y eval).

### H7 — Voseo en el texto que lee el agente consumidor · BAJO · deuda

- **Evidencia**: VERIFIED. `server.py:802-835` ("Podés", "citá", "mirá", "verificala", "leé",
  "Confirmalo", "tratalo", "verificá"), `pg_retriever.py:177` ("Omitilo"). Es el prompt del
  consumidor (CLAUDE.md del repo), y un agente tiende a imitar el registro del texto que recibe.
- **Fix + test**: pasar a "tú"; un test que recorra las descripciones y `_VERDICT_GUIDANCE` buscando
  las formas de voseo.
- **Costo**: muy bajo.

### H8 — Frontmatter indexado como chunk (pendiente conocido de la memoria) · BAJO

- VERIFIED en `bt_docs`: 26 chunks `_c0` son YAML crudo (p. ej.
  `manual/MDU-10115-GL-V3R1.2 - Sistema de Precios_c0`). En `gx_docs` hay 253 coincidencias del
  mismo regex, pero **UNKNOWN** si son frontmatter o separadores `---` de Markdown.

### H9 — Un manual indexado desde una carpeta `_skipped` · BAJO

- VERIFIED: `docs/Bantotal/_skipped/MDU-10102-GL-V3R1.2 - Descartes - Servidor de Procesos.pdf`,
  104 chunks. INFERRED: el nombre de la carpeta sugiere que se quiso excluir, pero es la **única**
  copia de ese manual en `bt_docs`; borrarlo perdería el contenido. Confirmar intención antes.

---

## (a) Known issues, verificados

| Issue | Estado |
|---|---|
| Backlinks desplazando cuerpo | **Vigente en el corpus** (4.449 chunks); el síntoma documentado ya no reproduce → H6 |
| Manual Descartes duplicado | **Resuelto**: una sola copia de cada manual (Diseñador V3R1.3 489, Servidor 104); los `-extracted.md` no están |
| 5 tests rojos en `test_retriever.py` | **Obsoleto**: el archivo ya no existe; suite 520 passed |
| Sin set de relevancia | **Resuelto en lo esencial** (VERIFIED que existen `scripts/eval_retrieval.py`, `eval_etp_titles.py`, `tests/test_gold_set.py`; no reevalué su resolución) |
| Sin tool de borrado | **Vigente como tool MCP**; existe por CLI: `scripts/retire_source.py` y `purge_collection.py` |

## (b) Top 3 por riesgo/esfuerzo

1. **H1 + H2**: limpiar literales y buscar la llamada sin anclar. Unas 15 líneas en
   `_extract_candidate`; tres tests rojos ya escritos.
2. **H3**: gate de receptor contra `list_object_types()`. Unas 5 líneas; un test rojo y un control.
3. **H4a**: corregir la descripción de `verify_syntax` y subir el umbral de similitud. Sin tocar
   datos. El `--apply` del catálogo (H4b) va después, con gate humano.

## (c) Afirmaciones de `RAG_SYSTEM_MAP.md` que quedaron stale

| Ubicación | Dice | Realidad (evidencia) |
|---|---|---|
| encabezado | Branch `vps-produccion` | se trabaja en `main` |
| §1 l.40-41 | `bt_docs` 1.122 | 34.198 |
| §1 l.51, §5.2 | Reranking NOT IMPLEMENTED | cableado: `reranker.py`, `pg_retriever.py:355`, `server.py:1243` |
| §1 l.52, §16 | Evaluación NOT IMPLEMENTED | existen `eval_retrieval.py`, `eval_etp_titles.py`, gold set |
| §2 | `server.py` 743 líneas; 6 archivos de test; solo `sql/001` | 1.518 líneas; ~45 archivos; `sql/001…006` |
| §5.1 l.270 | `_to_tsquery` con `\w+`, OR de todo | `[^\W_]+` y sin palabras vacías (`pg_retriever.py:183-215`) |
| §5.1 l.313-317 | "HNSW parcial por colección NO es la alternativa" | es lo implementado (`sql/006`, `pg_retriever.py:53-58`) |
| §5.3 | sin dedup | `max_per_article` (`pg_retriever.py:132-143`) |
| §5.5 | el filtrado por versión no se enforcea | el gate existe (`gx_repository.py:358-398`), aunque hoy está muerto por los NULL (H5) |
| §4.1 | `pymupdf` sin uso | existe `scripts/ingest_pdf.py` (INFERRED que lo usa; no lo abrí) |
| l.995-1218 | "Gateway MCP Protocol Implementation — Migration Research Report" | contenido ajeno (gateway C# de GxGenie) pegado al final del mapa |

También stale fuera del mapa: `CLAUDE.md` ("706 con `version_since`") y `PENDIENTES.md` (la tabla
de corpus, §1, §2 y §3).
