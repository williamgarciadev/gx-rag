# Plan de ingesta — PDFs del portal SGR ya descargados

Checklist para meter en `bt_docs` lo que dejó en disco `docs/PLAN-DESCARGA-SGR.md`. Un lote por
vez: la ingesta cuesta horas de embeddings (~40 págs/min en esta máquina, medido con Colocaciones)
y toca la base, así que cada lote se verifica antes de pasar al siguiente.

## Lotes

| ✔ | Lote | Qué | PDFs | Págs | Tiempo est. | Fecha | Resultado |
|---|---|---|---|---|---|---|---|
| ✅ | **I1.1** | Manuales › Core Núcleo | 106 → **74** | 4.527 | ~1 h 10 min real | 2026-10-07 | 1.460 secciones, 74/74 ✓. De Bantotal Servicios solo la última versión (2.8 + su guía, Exposer 1.2, API Institucional, Transacciones por Servicios): 32 versiones y migraciones viejas quedan `ingest: false`. Pérdida: 3 referencias cruzadas en la franja del pie. bt_docs = 15.630 |
| ✅ | **I1.2** | Manuales › Core Captaciones | 31 | 4.447 | ~1 h 50 min real | 2026-10-07 | 1.875 secciones, 31/31 ✓. Pruebas: cierre de caja y renovación de DPF ✅; "apertura de cuenta corriente" ⚠️ top-1 equivocado (Migración de Tarjetas), los 3 hits de una sola mitad del híbrido → primera señal de ruido. bt_docs = 20.874 |
| ✅ | **I1.3** | Manuales › Tesorería, Comercio Exterior, DevOps, Instalación, Conectores, BPeople, BTDevelopers | 30 → **24** | ~1.400 | ~30 min real | 2026-10-08 | 680 secciones, 24/24 ✓. Solo variantes Java Web / SQL Server: 5 de AS400, Oracle o DotNet y el instalador viejo de Descartes quedan `ingest: false`. Instalación SQL Server Windows y Linux comparten capítulos idénticos (chunks duplicados, menor). bt_docs = 22.787 |
| ✅ | **Gate** | Medir la línea base del retrieval (`eval/gold.jsonl`) antes de meter ETPs | — | — | — | 2026-10-08 | Bajó (R@5 0,667→0,583), pero **no por I1**: el HNSW era uno solo para toda la tabla y post-filtraba por colección, así que crecer bt_docs le movió el grafo a gx_docs (el código del 06 rankeó gx_docs distinto sin que gx_docs cambiara). Búsqueda exacta: R@5 0,833. Fix: `sql/006-hnsw-per-collection.sql` → R@1 0,250 · R@5 **0,750** · R@10 **0,833** · MRR 0,438 |
| ✅ | **Verif.** | `verify_ingest` sobre los 189 SGR con `ingest=true` | 189 | 14.066 | — | 2026-10-08 | 189/189 en bt_docs, ninguno con 0 chunks. **Pérdida real**: 125 páginas de contenido marcadas como índice (tablas con líneas que terminan en número), 120 de ellas en los 3 MDI de Originación de Créditos (Hipotecario 51/321, Vehicular 39/277, Consumo 30/217). El verificador no las ve porque excluye las páginas de índice de la cobertura. Log: `_inventario/lotes/verify_SGR_completo_2026-10-08.log` |
| ✅ | **R1** | Reingesta de los 3 MDI de Originación con el extractor corregido (`is_toc_page` exige que las referencias de página mayormente crezcan) | 3 | 815 | ~18 min real | 2026-10-08 | Capítulos 293 → 297, chunks 984 → 1.160. Palabras perdidas: Consumo 2.830 → 485, Hipotecario 4.347 → 195, Vehicular 4.167 → 99. Sin capítulos huérfanos. Regresión sobre los 204 PDF del manifiesto: solo cambian esos 3 docs (119 págs.), ningún índice inicial. Pendiente: Hipotecario p.45 (marcada por la alarma nueva) y las 485 de Consumo (detalles/validaciones/mapeos/propiedades ×56, otra causa) |
| ✅ | **I2.0** | Inventario de los 450 PDF sin decidir (`_inventario/lotes/I2_inventario.json`) | 450 | 4.759 | — | 2026-10-08 | 5 son copias byte a byte de documentos ya en el manifiesto (Impresor V3R1.3/V3R1.4, Auditoría BD, BTSConsumer, una Integración ya excluida) → `ingest: false` (`I2_duplicados.txt`). Quedan **445**. Sin versiones repetidas del mismo código. Decisión del usuario: todos, en lotes de 50 |
| ✅ | **I2.1** | Otros › Core Captaciones (+1 ETP suelto) (`I2.1_in.txt`) | 50 | 556 | ~13 min real (4 hilos) | 2026-10-08 | 101 secciones, 50/50 ✓, sin índice disperso. Secretos: 0 (2 falsos positivos de "Clave:" corregidos en el escáner). Palabras perdidas: solo el título (sale del nombre del archivo) y "¡Error! Marcador no definido" del índice de Word. Pruebas: Cuenta de Ahorro Junior y Cierre Forzoso de Caja → su ETP en top-1. bt_docs = 23.945 |
| [ ] | **I2.2** | Otros › Core Colocaciones y Captaciones (`I2.2_in.txt`) | 50 | 599 | ~22 min | | |
| [ ] | **I2.3** | Otros › Core Colocaciones y Núcleo (`I2.3_in.txt`) | 50 | 576 | ~22 min | | |
| [ ] | **I2.4** | Otros › Core Núcleo (`I2.4_in.txt`) | 50 | 499 | ~19 min | | |
| [ ] | **I2.5** | Otros › Mercados Financieros, Núcleo, Comercio Internacional, Cadena de Cierre y sueltos (`I2.5_in.txt`) | 50 | 457 | ~17 min | | |
| [ ] | **I2.6** | Otros › Procesos Batch, Mercados Financieros, Normativo (`I2.6_in.txt`) | 50 | 508 | ~19 min | | |
| [ ] | **I2.7** | Otros › Procesos Batch (`I2.7_in.txt`) | 50 | 413 | ~15 min | | |
| [ ] | **I2.8** | Otros › Procesos Batch (`I2.8_in.txt`) | 50 | 479 | ~18 min | | |
| [ ] | **I2.9** | Otros › RTE's Genéricas, Procesos Batch, Riesgos (`I2.9_in.txt`) | 45 | 355 | ~13 min | | |

> **Cómo correr un lote de I2 sin ahogar el PC** (pedido del usuario, 2026-10-08): 4 hilos de
> torch (`OMP_NUM_THREADS=4`, la máquina tiene 8) y prioridad *por debajo de lo normal* en Windows.
> El tiempo estimado de la tabla ya incluye ese ×1,5. Un lote por vez; entre lotes, el PC queda libre.

> **Por qué el gate antes de I2**: con 10.000 páginas más el ranking ya se resiente (la búsqueda
> de SARC del 2026-10-07 dio dos hits de una sola mitad del híbrido). Medir después de I1 dice si
> los manuales mejoraron o empeoraron el retrieval antes de sumar 445 ETPs.

## Pasos de cada lote

| # | Paso | Cómo | Se detiene y se consulta al usuario si… |
|---|---|---|---|
| 1 | Secretos (informativo) | `python -m scripts.scan_secrets --list <lista>` | **no bloquea**: los valores de ejemplo se ingieren tal cual (decisión del usuario, ver `INGESTA-BANTOTAL.md`). Solo se avisa si aparece algo que parezca una credencial **real del banco** |
| 2 | Pérdida, antes | `python -m scripts.verify_ingest <rutas>` | una página con texto sin cubrir, o palabras perdidas FUERA del índice (medir por página) |
| 3 | Resolver | `.md` transcrito, borrar copias idénticas | — |
| 4 | Manifiesto | `manifest_add.py` (skill `rag-ingest`) | — |
| 5 | Ingesta | `python -m scripts.ingest_local <rutas>` en segundo plano | — |
| 6 | Pérdida, después | `verify_ingest` | algún documento con 0 chunks |
| 7 | Versiones viejas | `python -m scripts.retire_source <viejo> --replaced-by <nuevo>` | antes de borrar, se informa cuáles |
| 8 | Prueba | `search_docs` con preguntas sobre el lote | el documento no sale en el top-3 |
| 9 | Cierre | marcar la fila ✅ aquí y en `INGESTA-BANTOTAL.md`; commit | — |

⚠️ **Las listas y logs de cada lote van a `docs/Bantotal/SGR/_inventario/lotes/`**, nunca al
scratchpad de la sesión: la noche del 2026-10-07 al 08 el temporal se vació solo y se perdieron
las listas de lote, los logs y los scripts auxiliares (se regeneraron con `sgr_plan`).

⚠️ Pasar las rutas a los scripts **desde un archivo de lista escrito en UTF-8**, no desde un
`print` a la consola: las tildes de "NÚCLEO" o "PAÍS" se rompían en cp1252 y los scripts
recibían rutas que "no existen" (2026-10-07).

## Ruido conocido de `verify_ingest` (no es pérdida)

- La palabra "contenido" (título del índice).
- Páginas que son solo imagen.
- Índices largos (36-61 págs.) inflan las "palabras perdidas": medir por página antes de concluir.
