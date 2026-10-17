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

## Fase C2 — Un módulo de pedido por tool para los tres transportes

- [ ] Test rojo: `/search` sobre `gx_kb_objects` sin `kb_name` se rechaza igual que en el MCP
- [ ] Test rojo: `/verify` devuelve JSON, no `str(dict)`
- [ ] Extraer `SearchRequest` (valida, acota `top_k`/`snippet_chars`/`max_per_article`, rechaza colección y filtros)
- [ ] `dispatch_tool` y `api.py` lo usan; mismos límites en ambos
- [ ] Política de errores única en lugar de los 8 `except` repetidos
- [ ] Mismo tratamiento para `verify_syntax`, `get_document`, `get_article`

## Fase C3 — Una entrada idempotente al indexer

- [ ] Test rojo: `add_from_url` de un artículo que se acortó no deja chunks huérfanos
- [ ] `index_document(Document)` con campos con nombre; garantiza hash y huérfanos
- [ ] `ingestor`, `kb_loader` y `bulk_loader` migran; se borra la entrada no idempotente
- [ ] El formato `{id}_c{i}` queda interno (resolver `server.py:965`)
- [ ] `src.ingestion` deja de importar `scripts.scrape_wiki`

## Fase C4 — Política de colecciones en un módulo

- [ ] Módulo `collections` con `existe / escribible / pública / exige_kb_name / default`
- [ ] Borrar `CollectionsConfig` y la copia de `KB_COLLECTION` en `kb_loader.py`
- [ ] El enum del schema público se deriva de la política (no ofrece `gx_kb_objects`)
- [ ] Los CLIs validan la colección

## Fase C5 — *Seam* de conexión (especulativa)

- [ ] Solo si otra fase toca el store: los módulos reciben conexión en vez de buscar el global
- [ ] `get_engine` falla ruidoso sin DSN fuera de tests (H5)

## Limpieza suelta

- [ ] Borrar `scripts/purge_collection.py`, `scripts/split_collection.py`, `scripts/ab_e5_prefix.py` (no importan)
- [ ] Borrar `get_signature` (`gx_repository.py:478`, sin llamadores)
- [ ] Corregir lo stale de `RAG_SYSTEM_MAP.md`, `CLAUDE.md` y `PENDIENTES.md` (tabla (c) de la auditoría)
