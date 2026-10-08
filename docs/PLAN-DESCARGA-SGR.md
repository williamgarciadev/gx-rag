# Plan de descarga — portal SGR (solo descargar, NO ingerir)

Checklist para bajar a disco **todos los PDFs Global + Colombia** del portal SGR que el RAG todavía
no tiene. **La ingesta es otro plan**, que se arma después, con estos archivos ya en disco.

> **Por qué separar**: descargar es barato y reversible (archivos en disco, ~1 h en total). La
> ingesta cuesta horas de embeddings y toca la base. Con todo descargado se puede revisar con calma
> (secretos, pérdida, duplicados) y decidir el orden de ingesta sin volver al portal.

**Medido el 2026-10-07** con `sgr_plan` sobre el inventario del mismo día: **614 PDFs** en total
(D1 = 167, D2 = 447), sin duplicados, solo la última versión de cada documento. Criterio y
decisiones: ver `docs/INGESTA-BANTOTAL.md`.

> Primera versión de este plan: 635. Bajó a 614 al corregir la regla de país (una carpeta de país
> a cualquier profundidad manda sobre el código): 21 informes de Uruguay venían con código `-GL-`.

## Cómo se baja un lote (los mismos pasos para cada fila)

```bash
# Desde F:/ProyectosClaude/gx-rag
# 1. Plan: deduplica sobre TODO el árbol y deja solo la parte del lote (GL/CO, lo que el RAG
#    no tiene, última versión). Prefijo de la tabla: relativo a Bantotal V3R1, con '/' y los
#    dobles espacios del portal.
./.venv/Scripts/python.exe -m scripts.sgr_plan \
  F:/ProyectosClaude/docs/Bantotal/SGR/_inventario/inventario-2026-10-07.jsonl \
  --solo "<prefijo>" [--solo "<otro prefijo>"] --out lote.json

# 2. Descarga (desde la raíz de kb-micro/DevGeo, por el .env del portal)
D:/Programas/Python313/python.exe .claude/skills/leer-doc-sgr-bantotal/scripts/sgr_docs.py \
  descargar lote.json "F:/ProyectosClaude/docs/Bantotal/SGR"
```

Un lote está **listo** cuando cumple las cuatro:

- [ ] `sgr_docs descargar` terminó con `fallas=0` y código de salida 0 (si no, re-correr: salta lo ya bajado).
- [ ] La cantidad de PDFs en disco coincide con el tamaño del lote.
- [ ] No quedó ningún archivo de otro país ni duplicado (copias "(2)" con el mismo hash).
- [ ] Se marcó aquí la fila con ✅, la fecha y la cantidad real.

⚠️ **Usar `--solo`, no `--carpeta`**: `--carpeta` deduplica solo dentro de la carpeta, y un mismo
documento en dos carpetas (ej. MDI-10103 "Definición de RTE", en Núcleo y en RTE's Genéricas)
terminaba en dos lotes. Medido el 2026-10-07: D1.1 daba 110 con `--carpeta` y 106 con `--solo`.

⚠️ Al armar un lote a mano, **no buscar por cómo termina el nombre**: el 2026-10-07 eso trajo un
homónimo de Uruguay. Usar `sgr_plan` o el `Link` completo.

⚠️ **El código del archivo no alcanza para saber el país**: el portal publica informes de Uruguay
con código `-GL-` dentro de carpetas `...\Uruguay\...`. `sgr_plan` da prioridad a cualquier carpeta
con nombre de país, a cualquier profundidad (lista medida en el inventario).

## Lotes de descarga

Orden: primero los **manuales** (documentan sistemas completos), después los **ETP** de
"Otros Documentos" (especificaciones de programas sueltos).

### D1 — Manuales

| ✔ | Lote | Prefijo (`--solo`, uno por línea) | PDFs | Fecha | Real |
|---|---|---|---|---|---|
| ✅ | D1.1 Core Núcleo | `Manuales/BANTOTAL CORE  NÚCLEO/` y `Manuales/MANUALES POR PAÍS/BANTOTAL CORE  NÚCLEO/` | 106 | 2026-10-07 | 106 · 0 fallas · 0 duplicados · 0 de otro país |
| ✅ | D1.2 Core Captaciones | `Manuales/BANTOTAL CORE  CAPTACIONES/` | 31 | 2026-10-07 | 31 · 0 fallas |
| ✅ | D1.3 DevOps | `Manuales/BANTOTAL  DEVOPS/` | 10 | 2026-10-07 | 10 · 0 fallas |
| ⛔ | D1.4 Reportes Regulatorios | `Manuales/BANTOTAL  REPORTES REGULATORIOS/` | ~~8~~ **0** | 2026-10-07 | Fuera de alcance: los 8 son informes para el BCU de **Uruguay** con código `-GL-`. 5 alcanzaron a bajar y se borraron |
| ✅ | D1.5 Tesorería | `Manuales/BANTOTAL  TESORERÍA/` | 7 | 2026-10-07 | 7 · 0 fallas |
| ✅ | D1.6 Instalación | `Manuales/BANTOTAL INSTALACIÓN/` | 5 | 2026-10-07 | 5 · 0 fallas |
| ✅ | D1.7 BPeople, Comercio Exterior, BTDevelopers, Conectores | (una por carpeta) | 3 + 2 + 2 + 1 | 2026-10-07 | 8 · 0 fallas |

> D1 completo: **167 PDFs** en disco, 0 duplicados, 0 de otro país (verificado por hash y por ruta).

> D1.1: "MANUALES POR PAÍS" tiene un espejo de Núcleo con los mismos documentos. `sgr_plan` se
> queda con una sola copia de cada uno, así que no se baja dos veces.

### D2 — Otros Documentos (ETP)

| ✔ | Lote | Prefijo (`--solo`, uno por línea) | PDFs | Fecha | Real |
|---|---|---|---|---|---|
| ✅ | D2.1 Procesos Batch | `Otros Documentos/Procesos Batch/` | 160 | 2026-10-07 | 159. **1 vacío en el portal** (0 bytes): `ETP-10201 - Re-expresión Contable de Préstamos Fondeados por Terceros`. 2 copias "(2)" de SARC bajadas de nuevo y borradas |
| ✅ | D2.2 Núcleo | `Otros Documentos/BANTOTAL CORE - NÚCLEO/` | 83 | 2026-10-07 | 83 · 0 fallas |
| ✅ | D2.3 Captaciones | `Otros Documentos/BANTOTAL CORE - CAPTACIONES/` | 66 | 2026-10-07 | 66. Se cortó en 25 (bug de consola cp1252 en `sgr_docs`, corregido); el reintento bajó los 41 restantes sin fallas |
| ✅ | D2.4 Colocaciones | `Otros Documentos/BANTOTAL CORE - COLOCACIONES/` | 59 | 2026-10-07 | 59 · 0 fallas |
| ✅ | D2.5 Mercados Financieros | `Otros Documentos/Mercados Financieros/` | 31 | 2026-10-07 | 31 · 0 fallas |
| ✅ | D2.6 RTE's Genéricas | `Otros Documentos/RTE's Genéricas/` | 21 | 2026-10-07 | 21 · 0 fallas |
| ✅ | D2.7 Riesgos | `Otros Documentos/Riesgos/` | 11 | 2026-10-07 | 11 · 0 fallas |
| ✅ | D2.8 Normativo, Comercio Internacional, Cadena de Cierre, Canales, sueltos | (una por carpeta) | 3 + 5 + 3 + 1 + 3 | 2026-10-07 | 15 (14 nuevos + 1 que ya estaba) · 0 fallas |

> **Descarga completa (2026-10-07)**: `docs/Bantotal/SGR/` tiene **670 PDFs (~609 MB)**, 0 de
> otro país y 0 copias duplicadas (verificado por hash). El plan y el disco coinciden salvo lo
> explicado: el Modelo de Datos (excluido) y el archivo vacío del portal. Siguiente: plan de ingesta.

## Fuera de este plan (decidido)

| Qué | Por qué |
|---|---|
| "Modelo de Datos Bantotal" (1 PDF, en Otros Documentos) | El MDU-99000 está fuera del RAG por decisión del 2026-07-27: lo responde el MCP `bantotal-dev` desde el schema real |
| Documentos de otro país (UY, PE, AR, MX…) | Criterio Global + Colombia |
| Planillas (`.xlsx`, `.xls`) y `.zip` | El extractor del RAG solo lee PDF y Markdown |

## Decisiones pendientes (del usuario, antes de la ingesta)

- [ ] **62 PDFs sin país o sin código** (ej. ECP-*, nombres libres): ¿se bajan para revisarlos o se dejan?
- [ ] **`Manejo de Calificaciones (HBC201G).pdf`** (Colombia, sin código): ya está en disco, sin ingerir.
- [ ] **ETP de "Otros Documentos"**: se bajan todos (es barato), pero ¿se ingieren todos o solo los de los programas que usa Contactar?

## Siguiente plan

Cuando D1 y D2 estén ✅: **plan de ingesta**, con su propia checklist (secretos → manifiesto →
ingesta → `verify_ingest` → `search_docs` → retiro de versiones viejas con `retire_source`).
