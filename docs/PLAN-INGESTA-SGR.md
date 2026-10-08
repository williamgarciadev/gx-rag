# Plan de ingesta — PDFs del portal SGR ya descargados

Checklist para meter en `bt_docs` lo que dejó en disco `docs/PLAN-DESCARGA-SGR.md`. Un lote por
vez: la ingesta cuesta horas de embeddings (~40 págs/min en esta máquina, medido con Colocaciones)
y toca la base, así que cada lote se verifica antes de pasar al siguiente.

## Lotes

| ✔ | Lote | Qué | PDFs | Págs | Tiempo est. | Fecha | Resultado |
|---|---|---|---|---|---|---|---|
| ✅ | **I1.1** | Manuales › Core Núcleo | 106 → **74** | 4.527 | ~1 h 10 min real | 2026-10-07 | 1.460 secciones, 74/74 ✓. De Bantotal Servicios solo la última versión (2.8 + su guía, Exposer 1.2, API Institucional, Transacciones por Servicios): 32 versiones y migraciones viejas quedan `ingest: false`. Pérdida: 3 referencias cruzadas en la franja del pie. bt_docs = 15.630 |
| ✅ | **I1.2** | Manuales › Core Captaciones | 31 | 4.447 | ~1 h 50 min real | 2026-10-07 | 1.875 secciones, 31/31 ✓. Pruebas: cierre de caja y renovación de DPF ✅; "apertura de cuenta corriente" ⚠️ top-1 equivocado (Migración de Tarjetas), los 3 hits de una sola mitad del híbrido → primera señal de ruido. bt_docs = 20.874 |
| [ ] | **I1.3** | Manuales › Tesorería, Comercio Exterior, DevOps, Instalación, Conectores, BPeople, BTDevelopers | 30 | 1.662 | ~45 min | | |
| [ ] | **Gate** | Medir la línea base del retrieval (`eval/gold.jsonl`) antes de meter ETPs | — | — | — | | |
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

⚠️ Pasar las rutas a los scripts **desde un archivo de lista escrito en UTF-8**, no desde un
`print` a la consola: las tildes de "NÚCLEO" o "PAÍS" se rompían en cp1252 y los scripts
recibían rutas que "no existen" (2026-10-07).

## Ruido conocido de `verify_ingest` (no es pérdida)

- La palabra "contenido" (título del índice).
- Páginas que son solo imagen.
- Índices largos (36-61 págs.) inflan las "palabras perdidas": medir por página antes de concluir.
