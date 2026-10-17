# Plan de arquitectura — profundizar módulos superficiales

Sale de la revisión del 2026-10-17 (`docs/AUDITORIA-2026-10-17.md` y el reporte de arquitectura de
esa sesión). Vocabulario: módulo, interfaz, profundidad, *seam*, *adapter*, *leverage*, *locality*.
Cada fase es independiente y se integra sola; el orden es por riesgo/beneficio.

**Reglas que aplican a todas las fases**

- TDD estricto: primero el test rojo, después el código. Cada fix lleva el test que falla sin él.
- Antes de editar: `git branch --show-current`, `git status --short`, `git log --oneline -3`.
- Declarar la franja de archivos en la tabla de coordinación de `docs/PLAN-optimizacion.md`.
- La interfaz pública que ve el MCP no cambia sin decirlo: `verify_syntax(snippet, language, version) -> dict`.
- Commit solo con OK del usuario; un fix + su test = un commit.

---

## Fase C1 — La lectura del snippet en `verify_syntax` · ✅ cerrada (`e552a0d`)

**Meta**: un módulo puro `parse_snippet` decide qué nombres se consultan; `verify_syntax` juzga
contra el catálogo y escribe la telemetría en un solo punto. Cierra H1, H2 y H3 de la auditoría.

- [x] 1.0 Franja declarada en `docs/PLAN-optimizacion.md`
- [x] 1.1 Tests de `parse_snippet` en `tests/test_snippet_parser.py` (rojo: el módulo no existe)
  - asignación delante (`&x = Trim(&y)` → `Trim`)
  - palabra de control delante (`If Len(&s) > 0` → `Len`; `For Each Customer` sin nombres falsos)
  - literal con punto (`Msg('Error. Revise')` → `Msg`, sin receptor)
  - miembro sobre variable y sobre tipo (`&t.Length()`, `Date.AddDays(1)`)
  - identificador suelto y título de wiki (`Len`, `AddYears method`)
  - snippet sin tokens → sin nombres
- [x] 1.2 Implementar `src/syntax_db/snippet_parser.py` hasta verde
- [x] 1.3 `verify_syntax` usa `parse_snippet`; quitar el xfail de H1 y H2 (deben pasar)
- [x] 1.4 Gate de receptor: tipo solo si existe en el catálogo; quitar el xfail de H3; el test de control sigue verde
- [x] 1.5 Telemetría en un solo punto: el juicio (`_judge`) no escribe; test que lo demuestra
- [x] 1.6 Borrar lo que queda muerto (`_extract_candidate`, `_member_tokens`, la query de receptor duplicada)
- [x] 1.7 Suite completa verde (556) + reproducción contra el catálogo real: H1–H3 dan VÁLIDO; `For Each Customer` da NO_VERIFICABLE
- [x] 1.7b Hallazgo del self-review: el VÁLIDO por receptor tipado se saltaba el gate de versión (`Date.AddDays` con gx16 → VÁLIDO; `&d.AddDays` → PISTA). Ahora pasa por `_judge_entry`; test `test_receptor_tipado_tambien_pasa_por_el_gate_de_version`
- [x] 1.8 Commit `e552a0d` (local, sin push); H1–H3 marcados resueltos en la auditoría; franja cerrada

**Fuera de alcance de C1**: cargar funciones al catálogo (H4b, gate humano) y el umbral de
similitud (H4a, fase propia porque cambia veredictos para todos).

## Fase C1b — Similitud y descripción honesta (H4a, H4b) · ✅ cerrada

- [x] Tests: `Abs`, `Len` y `TrimLeftInexistente` → NO_VERIFICABLE; los typos reales (`Trmi`, `AddDay`, `AddYear`) siguen INVÁLIDO con la sugerencia correcta
- [x] `TYPO_SIMILARITY = 0.75` con difflib contra el nombre y su primera palabra; fuera las reglas de subcadena
- [x] Descripción de `verify_syntax` corregida: no promete funciones y remite a `list_methods` (sin test: es un texto cuya verdad depende de los datos del deployment)
- [x] Voseo → "tú" en `src/` y `scripts/` (82 formas) + `tests/test_registro_tu.py`
- [x] H4b aplicado con OK del usuario: +2.614 filas (`source=corpus`), total 3.379; respaldo en la tabla `gx_syntax_bk_20261017_pre_h4b`. Verificado: `Len`/`Abs` → VÁLIDO con gx18 y PISTA con gx16. Costo medido de la similitud: 120–240 ms por consulta sin match exacto

## Fase C2 — Un módulo de pedido por tool para los tres transportes · ✅ cerrada (sin commit aún)

- [x] Test rojo: `/search` sobre `gx_kb_objects` sin `kb_name` se rechaza igual que en el MCP (400, sin tocar el store)
- [x] Test rojo: `/verify` devuelve JSON, no `str(dict)` (era el #31 de Fase 3)
- [x] `src/tool_requests.py`: `parse_search`/`parse_document`/`parse_article` → pedido válido o `RequestRejected`; los filtros se validan antes de cargar e5-large
- [x] `dispatch_tool` y `api.py` lo usan; `top_k` acotado a 1–50 en los dos (api topaba en 20)
- [x] Tools de lectura: un handler por tool (`_READ_HANDLERS`) y un solo `try` en `dispatch_tool`; en `api.py`, un exception handler para `RequestRejected` → 400. Las 3 tools de escritura conservan su manejo propio
- [x] Mismo tratamiento para `get_document` y `get_article`; `verify_syntax` y `list_methods` no tienen argumentos que validar

## Fase C3 — Una entrada idempotente al indexer · ✅ cerrada (sin commit aún)

- [x] Test rojo: `add_from_url` de un artículo que se acortó no deja chunks huérfanos
- [x] Test rojo (hallado al leer el código): reindexar en OTRA colección dejaba huérfanos en la vieja, y con el mismo hash reportaba `indexed`
- [x] `index_document(...) -> IndexResult(status, chunk_count)`: única entrada de escritura; borra por `article_id` en todas las colecciones (chunk_id es clave global) y repara mezclas de hash; `reindex_if_changed` queda como su status para los cargadores por lote
- [x] Borrada la entrada no idempotente (`pg_indexer.index_article`); `ingestor` (ruta de `add_from_url` y `/scrape`) usa `index_document` y reporta el status
- [x] El formato `{id}_c{i}` tiene un solo dueño: `src/vectorstore/chunk_ids.py` (lo usan el indexer y `server.py`)
- [x] Medido en la base real: 0 artículos con hashes mezclados, repartidos entre colecciones o con huecos → el defecto era latente, no hay nada que limpiar
- [ ] Diferido, con razón: pasar a un `Document` con campos con nombre. Costaría ~20 fakes de `index_fn` en los tests para quitar dos llamadas con argumentos de relleno (`kb_loader`, `bulk_loader`). Retomarlo si aparece un tercer llamador
- [ ] Diferido: que `src.ingestion` deje de importar `scripts.scrape_wiki` (`ScrapedArticle`, `compute_hash`). Exige mover el scraper a `src/`, y `api.py` también lo importa; es su propia fase

## Fase C4 — Política de colecciones en un módulo · ✅ cerrada (sin commit aún)

- [x] `src/collection_policy.py` (puro, sin DB): colecciones conocidas y default, `KB_COLLECTION` y la regla de `kb_name`, escribibles, públicas por sustracción, `reject_unknown_collection`, `reject_non_public_collection` y `add_collection_argument`
- [x] Borrados `CollectionsConfig` (nadie lo leía; listaba `cross_ref`) y la copia de `KB_COLLECTION` en `kb_loader.py`; test que impide redefinir las colecciones fuera de la política (con `is` no se detecta: Python interna los literales)
- [x] `PUBLIC_READ_TOOLS`: el transporte público publica el enum de colección recortado a las públicas (ya no ofrece `gx_kb_objects`); el stdio no cambia
- [x] Los 11 CLIs con `--collection` validan con `add_collection_argument` (`choices`); test que impide declararlo a mano

## Fase C5 — *Seam* de conexión (especulativa)

- [ ] Solo si otra fase toca el store: los módulos reciben conexión en vez de buscar el global
- [ ] `get_engine` falla ruidoso sin DSN fuera de tests (H5)

## Limpieza suelta

- [ ] Borrar `scripts/purge_collection.py`, `scripts/split_collection.py`, `scripts/ab_e5_prefix.py` (no importan)
- [ ] Borrar `get_signature` (`gx_repository.py:478`, sin llamadores)
- [ ] Corregir lo stale de `RAG_SYSTEM_MAP.md`, `CLAUDE.md` y `PENDIENTES.md` (tabla (c) de la auditoría)
