# Ingesta de documentación Bantotal a `bt_docs` — estado y plan

Bitácora de **qué documentación Bantotal se trajo al RAG, de dónde, con qué criterio y qué falta**.
Existe para que el próximo (persona o agente) no repita trabajo ni deshaga una decisión ya tomada.

> **Qué NO está aquí**: la lista archivo por archivo. Esa vive en `data/manuals.json` (cada fila con
> su `reason`) y en la base (`chunks.source`). Este documento guarda lo que esos dos no dicen: el
> plan, las decisiones y su porqué.

> **Desde el 2026-10-07 la descarga y la ingesta van separadas**: la checklist de descarga está en
> **`docs/PLAN-DESCARGA-SGR.md`** (635 PDFs). La ingesta de lo descargado se planifica aparte.

## Ruta rápida — traer el próximo lote

1. Mira la tabla **Lotes** y toma el primero ⏳.
2. Si hay que regenerar el inventario del portal, ve **Fuente: portal SGR**.
3. Ingiere siguiendo la skill `rag-ingest` (secretos → manifiesto → ingesta → `verify_ingest` → `search_docs`).
4. Actualiza la fila del lote aquí y commitea este archivo junto con `data/manuals.json`.

## Lotes

| # | Lote | Origen | Estado | Fecha | Notas |
|---|---|---|---|---|---|
| 0 | Carpeta OneDrive "Documentación Bantotal/PDF" (24 PDFs) | OneDrive | ✅ hecho | 2026-10-07 | 21 ingeridos, 3 originales fuera (transcritos a `.md` o duplicados) |
| 1 | Manuales › **Core Colocaciones** (33 PDFs) | SGR | ✅ hecho | 2026-10-07 | 4.353 chunks, 33/33 ✓ en `verify_ingest`. Préstamos **V3R1.22** reemplazó a la V3R1.20 (494 chunks borrados, respaldo en `data/backups/`) y `eval/gold.jsonl` se actualizó a la V3R1.22. Fuera: MDU-10202 V3R1.3 (había V3R1.4) y 9 planillas sin código |
| 2 | **Versiones nuevas** de lo ya ingerido | SGR | ✅ hecho | 2026-10-07 | 5 PDFs, 298 secciones: Contabilidad V3R1.8 · Contrapartes V3R1.29 · Descartes Diseñador V3R1.3 · ETP-10102 V3R1.6 · ETP-10110 V3R1.3. Las 5 viejas retiradas con `retire_source` (1.145 chunks, con respaldo) |
| 3 | **Colombia**: Manuales por País › Colombia + los `-CO-` de Otros Documentos | SGR | ✅ hecho | 2026-10-07 | 22 documentos, 241 secciones, 22/22 ✓. Fuera: 2 copias idénticas "(2)" de ETP-10114 SARC. LDAP (MDI-10109-CO) entró como `.md` con una contraseña de ejemplo tachada. ❓ Pendiente: `Manejo de Calificaciones (HBC201G).pdf` (Colombia, sin código) ya descargado, sin ingerir |
| 4 | Manuales › **Core Núcleo** | SGR | ⏳ siguiente | | ~116 PDFs nuevos (+ el espejo de 128 bajo "MANUALES POR PAÍS", mismos documentos) |
| 5 | Manuales › **Core Captaciones** | SGR | ⏳ | | ~31 PDFs |
| 6 | Tesorería · Reportes Regulatorios · Comercio Exterior · DevOps · Instalación | SGR | ⏳ por decidir | | ~40 PDFs; confirmar cuáles usa Contactar |
| — | **Otros Documentos** (ETPs de programas) | SGR | ❓ por decidir | | ~527 PDFs. NO traer en bloque: filtrar por los programas que usa Contactar |

Leyenda: ✅ hecho · 🔄 en curso · ⏳ pendiente · ❓ requiere decisión.

## Decisiones (no re-litigar sin motivo nuevo)

| Decisión | Por qué | Fecha |
|---|---|---|
| **Solo localización Global (`-GL-`) y Colombia (`-CO-`)** | Lo demás es normativa de otro país (UY, PE, AR, MX…): un agente la tomaría como norma de Contactar | 2026-10-07 |
| En las carpetas "por País" manda la carpeta **solo si su hijo es un país** | "MANUALES POR PAÍS" también tiene `BANTOTAL CORE  NÚCLEO` con 128 manuales Globales: tratarla como país los descartaba (le pasó a Contrapartes V3R1.29) | 2026-10-07 |
| Las descargas reproducen la ruta del portal bajo `docs/Bantotal/SGR/` | Se ve de qué carpeta salió cada archivo. **Excepción**: el lote 1 quedó en `SGR/Colocaciones/` y no se mueve (mover duplica en la base) | 2026-10-07 |
| Sin segmento de país (ECP…) o sin código (planillas, nombres libres): **los decide el usuario caso a caso** | No se pueden clasificar solos | 2026-10-07 |
| **Una versión nueva reemplaza a la vieja** (respaldo + borrado de chunks) | Si conviven, el top-k mezcla versiones | 2026-10-07 |
| Dentro de un lote, de un mismo documento (código **+ título**) se baja **solo la versión más alta** | Ej.: MDU-10202 V3R1.3 y V3R1.4 "Sistema de Descuentos" | 2026-10-07 |
| Comparar por **código + título**, nunca solo código | `MDI-10201` lo comparten 4 documentos distintos (Préstamos, Seguros, Modelador, Vínculo) | 2026-10-07 |
| Valor sensible literal en un PDF (contraseña de ejemplo) → `.md` con el valor reemplazado por un marcador; PDF `ingest: false` | Lo que recupera el RAG entra al contexto del agente y viaja al proveedor del modelo; tachar un valor de ejemplo no le quita nada a la respuesta | 2026-10-07 |
| Copias idénticas (mismo hash, nombre con "(2)") no se ingieren | Duplican chunks; el plan no las junta porque el "(2)" cambia el título | 2026-10-07 |
| PDF que pierde contenido → se transcribe a `.md` y el PDF queda `ingest: false` | Casos: ETP-99000, ETP-TransferenciasACH, Buenas Prácticas GX16, guía de instalación GX16 | 2026-10-07 |
| MDU-99000 (inventario de tablas) **fuera** del RAG | Duplica lo que el MCP `bantotal-dev` responde desde el schema real | 2026-07-27 |
| Cadena de Cierre MDU-10102 V3R1.2 reemplazada por MDU-10109 V3R1.11 | Versión nueva; respaldo en `data/backups/` (el PDF viejo ya no existía) | 2026-10-07 |

## Fuente: portal SGR (DLYA)

`support.dlya.com.uy/sgr` › Conocimiento Bantotal › **Documentación** (`#/index/protdoc`). Exige login
y es AngularJS: no sirve `WebFetch`/`curl`, se usa Playwright.

| Dato | Valor |
|---|---|
| Inventario medido | 2026-10-07: 1.438 carpetas, 1.836 archivos, **1.507 PDF** (749 GL · 33 CO · 628 de otros países · 97 sin país) |
| Credenciales | `DLYA_SGR_USER` / `DLYA_SGR_PASS` en el `.env` de `kb-micro/DevGeo` (nunca por argumento) |
| Login reutilizado de | skill `leer-doc-sgr-bantotal` (proyecto DevGeo) |
| Destino de las descargas | `F:/ProyectosClaude/docs/Bantotal/SGR/<Módulo>/<subcarpeta del portal>/` |
| Navegación | `scope.navegar(link, 1)` y esperar `$http.pendingRequests == 0`. Mirar la lista no distingue carpeta vacía de carpeta cargando |
| Descarga | `scope.verArchivo(link, 1)` → descarga de un blob; validar que empiece con `%PDF-` |

🔴 El portal da permisos de **escritura** (botones Modificar/Eliminar, subir documentación). Todo lo
de aquí es **solo lectura**: navegar y descargar, nada más.

**Las tres herramientas** (orden de uso):

| Paso | Script | Dónde | Hace |
|---|---|---|---|
| 1 | `sgr_docs.py inventario <out.jsonl> [--sub "Manuales/<carpeta>"]` | skill `leer-doc-sgr-bantotal` (DevGeo) | recorre el árbol y lista cada archivo |
| 2 | `python -m scripts.sgr_plan <inventario> --carpeta "<carpeta>" --out lote.json` | **este repo** | aplica las Decisiones (GL/CO, última versión, lo que el RAG no tiene) y arma el lote |
| 3 | `sgr_docs.py descargar lote.json <destino>` | skill | baja el lote, valida `%PDF-`, es idempotente |
| 4 | `python -m scripts.retire_source <viejo> --replaced-by <nuevo>` | **este repo** | saca la versión vieja de la base: exige que la nueva ya esté, respalda en `data/backups/` y después borra |

Comandos completos en la skill. El inventario completo del 2026-10-07 está en
`F:/ProyectosClaude/docs/Bantotal/SGR/_inventario/`. Los nombres de carpeta del portal llevan
**dobles espacios** (`BANTOTAL CORE  COLOCACIONES`): cópialos del inventario.

## Dónde queda cada ruta

| Lugar | Forma | Para qué |
|---|---|---|
| `F:/ProyectosClaude/docs/...` | ruta real | el archivo (fuera del repo, sin git) |
| `data/manuals.json` | **relativa** al `root` | qué entra, qué no y por qué (en git) |
| `chunks.source` / `url` | **URI absoluta** `file:///F:/...` (espacios = `%20`) | identidad del documento; es lo que cita el agente |
| `chunks.article_id` | URI + `#ch=N` | identidad del capítulo; con ella `reindex_if_changed` decide saltar o reindexar |

🔴 **No muevas ni renombres un PDF ya ingerido.** La base lo identifica por su URI absoluta: al
reingerirlo desde otra ruta entra como documento NUEVO y el viejo queda huérfano y duplicado, sin aviso
(en la base ya hay fuentes viejas con rutas de otra máquina, `C:\Users\wigam\...`). Para mover: borrar
sus chunks por `source` → mover → actualizar `manuals.json` → reingerir.

## Verificación

- `python -m scripts.verify_ingest <rutas>`: chunks en la base, páginas sin cubrir, palabras perdidas.
- Ruido conocido: la palabra "contenido" (título del índice), páginas que solo tienen imagen, y —en
  documentos con índices largos (36-61 págs.)— miles de "palabras perdidas" falsas. Ante una cifra
  alta, medir por página antes de concluir pérdida (Colocaciones, 2026-10-07: máx. 9 palabras/pág.).

## Pendientes

- [x] Scripts del portal en la skill (`sgr_docs.py`) y el plan de lote en este repo (`scripts/sgr_plan.py`), 2026-10-07. ⚠️ `sgr_docs.py` y el SKILL.md de la skill están **sin commitear en DevGeo** (la rama activa allá era trabajo ajeno).
- [ ] `verify_ingest`: que los índices largos no inflen las "palabras perdidas".
- [ ] Extractor: un pie de página partido en dos líneas se cuela en ETP-99000 ("de Larrobla y Asociados…").
- [ ] Mover los `.env` de los MCP Bantotal fuera de la carpeta de la skill `bantotal_model_docs` (Action `act_muymg7zp_d89ee3b31878`).
- [ ] Candidato fuera del portal: `ETP-10102-GL-V3R1.2 - Paralelizador cadena de cierre (HCAP003).pdf` (en Descargas).
