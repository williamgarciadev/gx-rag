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
| [ ] | **I2** | ETP de Otros Documentos (D2, 445 PDFs) | ❓ | | | | Requiere decisión: todos o solo los programas que usa Contactar |

> **Por qué el gate antes de I2**: con 10.000 páginas más el ranking ya se resiente (la búsqueda
> de SARC del 2026-10-07 dio dos hits de una sola mitad del híbrido). Medir después de I1 dice si
> los manuales mejoraron o empeoraron el retrieval antes de sumar 445 ETPs.

## Pasos de cada lote

| # | Paso | Cómo | Se detiene y se consulta al usuario si… |
|---|---|---|---|
| 1 | Secretos (informativo) | `scan.py` sobre el lote | **no bloquea**: los valores de ejemplo se ingieren tal cual (decisión del usuario, ver `INGESTA-BANTOTAL.md`). Solo se avisa si aparece algo que parezca una credencial **real del banco** |
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
