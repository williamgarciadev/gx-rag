# Plan de descarga — portal SGR (solo descargar, NO ingerir)

Checklist para bajar a disco **todos los PDFs Global + Colombia** del portal SGR que el RAG todavía
no tiene. **La ingesta es otro plan**, que se arma después, con estos archivos ya en disco.

> **Por qué separar**: descargar es barato y reversible (archivos en disco, ~1 h en total). La
> ingesta cuesta horas de embeddings y toca la base. Con todo descargado se puede revisar con calma
> (secretos, pérdida, duplicados) y decidir el orden de ingesta sin volver al portal.

**Medido el 2026-10-07** con `sgr_plan` sobre el inventario del mismo día: **635 PDFs** por bajar,
sin duplicados, solo la última versión de cada documento. Criterio y decisiones: ver
`docs/INGESTA-BANTOTAL.md`.

## Cómo se baja un lote (los mismos pasos para cada fila)

```bash
# Desde F:/ProyectosClaude/gx-rag
# 1. Plan: arma el lote de la carpeta (GL/CO, lo que el RAG no tiene, última versión)
./.venv/Scripts/python.exe -m scripts.sgr_plan \
  F:/ProyectosClaude/docs/Bantotal/SGR/_inventario/inventario-2026-10-07.jsonl \
  --carpeta "<carpeta exacta, con sus dobles espacios>" --out lote.json

# 2. Descarga (desde la raíz de kb-micro/DevGeo, por el .env del portal)
D:/Programas/Python313/python.exe .claude/skills/leer-doc-sgr-bantotal/scripts/sgr_docs.py \
  descargar lote.json "F:/ProyectosClaude/docs/Bantotal/SGR"
```

Un lote está **listo** cuando cumple las cuatro:

- [ ] `sgr_docs descargar` terminó con `fallas=0` y código de salida 0 (si no, re-correr: salta lo ya bajado).
- [ ] La cantidad de PDFs en disco coincide con el tamaño del lote.
- [ ] No quedó ningún archivo de otro país ni duplicado (copias "(2)" con el mismo hash).
- [ ] Se marcó aquí la fila con ✅, la fecha y la cantidad real.

⚠️ Al armar un lote a mano, **no buscar por cómo termina el nombre**: el 2026-10-07 eso trajo un
homónimo de Uruguay. Usar `sgr_plan` o el `Link` completo.

## Lotes de descarga

Orden: primero los **manuales** (documentan sistemas completos), después los **ETP** de
"Otros Documentos" (especificaciones de programas sueltos).

### D1 — Manuales

| ✔ | Lote | Carpeta (`--carpeta`) | PDFs | Fecha | Real |
|---|---|---|---|---|---|
| [ ] | D1.1 Core Núcleo | `BANTOTAL CORE  NÚCLEO` | 106 | | |
| [ ] | D1.2 Core Captaciones | `BANTOTAL CORE  CAPTACIONES` | 31 | | |
| [ ] | D1.3 DevOps | `BANTOTAL  DEVOPS` | 10 | | |
| [ ] | D1.4 Reportes Regulatorios | `BANTOTAL  REPORTES REGULATORIOS` | 8 | | |
| [ ] | D1.5 Tesorería | `BANTOTAL  TESORERÍA` | 7 | | |
| [ ] | D1.6 Instalación | `BANTOTAL INSTALACIÓN` | 5 | | |
| [ ] | D1.7 BPeople, Comercio Exterior, BTDevelopers, Conectores | (una por carpeta) | 3 + 2 + 2 + 1 | | |

> D1.1: "MANUALES POR PAÍS" tiene un espejo de Núcleo con los mismos documentos. `sgr_plan` se
> queda con una sola copia de cada uno, así que no se baja dos veces.

### D2 — Otros Documentos (ETP)

| ✔ | Lote | Carpeta (`--carpeta`) | PDFs | Fecha | Real |
|---|---|---|---|---|---|
| [ ] | D2.1 Procesos Batch | `Procesos Batch` | 163 | | |
| [ ] | D2.2 Núcleo | `BANTOTAL CORE - NÚCLEO` | 83 | | |
| [ ] | D2.3 Captaciones | `BANTOTAL CORE - CAPTACIONES` | 66 | | |
| [ ] | D2.4 Colocaciones | `BANTOTAL CORE - COLOCACIONES` | 59 | | |
| [ ] | D2.5 Mercados Financieros | `Mercados Financieros` | 31 | | |
| [ ] | D2.6 RTE's Genéricas | `RTE's Genéricas` | 21 | | |
| [ ] | D2.7 Riesgos | `Riesgos` | 15 | | |
| [ ] | D2.8 Normativo, Comercio Internacional, Cadena de Cierre, Canales, sueltos | (una por carpeta) | 9 + 5 + 3 + 1 + 3 | | |

## Fuera de este plan (decidido)

| Qué | Por qué |
|---|---|
| "Modelo de Datos Bantotal" (1 PDF, en Otros Documentos) | El MDU-99000 está fuera del RAG por decisión del 2026-07-27: lo responde el MCP `bantotal-dev` desde el schema real |
| Documentos de otro país (UY, PE, AR, MX…) | Criterio Global + Colombia |
| Planillas (`.xlsx`, `.xls`) y `.zip` | El extractor del RAG solo lee PDF y Markdown |

## Decisiones pendientes (del usuario, antes de la ingesta)

- [ ] **101 PDFs sin país o sin código** (ej. ECP-*, nombres libres): ¿se bajan para revisarlos o se dejan?
- [ ] **`Manejo de Calificaciones (HBC201G).pdf`** (Colombia, sin código): ya está en disco, sin ingerir.
- [ ] **ETP de "Otros Documentos"**: se bajan todos (es barato), pero ¿se ingieren todos o solo los de los programas que usa Contactar?

## Siguiente plan

Cuando D1 y D2 estén ✅: **plan de ingesta**, con su propia checklist (secretos → manifiesto →
ingesta → `verify_ingest` → `search_docs` → retiro de versiones viejas con `retire_source`).
