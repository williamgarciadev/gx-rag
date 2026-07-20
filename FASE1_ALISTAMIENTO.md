# Fase 1 — Estado y dependencias de datos

## Objetivo
Producir el documento de caracterización del corpus con las 4 fuentes.

---

## Avance logrado (2026-07-19)

### Wiki GeneXus — scraper operativo ✅

Script: `scripts/scrape_wiki.py`

**Validaciones pasando:**
- ✅ Descarga artículo desde `docs.genexus.com`
- ✅ Extracción de contenido principal (gx-content-placeholder)
- ✅ Clasificación por género: `For each command` → reference → gx_syntax ✅
- ✅ Clasificación por género: `For each Optimizations` → guide → gx_docs ✅
- ✅ Idempotencia: misma URL dos veces = mismo content_hash ✅
- ✅ source_key normalizada (dominio unificado, pageId numérico)
- ✅ Metadata con version_hint (GeneXus 18 Help)
- ✅ Markdown limpio con sintaxis GeneXus intacta

**Artefactos generados:**
- `/tmp/gx-test2/24744.md` — For each command (reference → gx_syntax)
- `/tmp/gx-test2/26286.md` — For each Optimizations (guide → gx_docs)

---

## Gate de la Fase 1 (criterio de "hecho")

- [x] Validación idempotencia con reingesta real (misma URL dos veces)
- [x] Clasificación wiki por género funcionando (2 muestras probadas)
- [ ] Las 4 fuentes caracterizadas (tipología, patrones, formato, relaciones)
- [ ] Tabla de prefijos validada contra identificadores reales
- [ ] Decisión OCR documentada con evidencia
- [ ] Flujo de curación definido y probado con al menos 1 muestra
- [ ] Artefacto curado de muestra (PDF Bantotal)
- [ ] Gate de muestra wiki (20-30 docs para validar clasificación)
- [ ] Ingesta batch de directorio local (modo batch)

---

## Pendiente: lo que sigue

### 1. Gate de muestra wiki (20-30 docs)
Necesito una lista de 20-30 URLs representativas de la wiki para:
- Validar que la clasificación reference/guide acierta
- Medir false positives/negatives
- Probar que la curación automática no deforma el contenido

### 2. PDFs Bantotal
¿Están disponibles? ¿En qué ruta?

### 3. XPZ y Manual de Usuario
Pendiente de caracterizar.

### 4. Librerías restantes por instalar
```
chromadb>=0.5.0
sentence-transformers>=3.0
rank-bm25>=0.2.2
langchain-text-splitters>=0.2
sqlalchemy>=2.0
pydantic>=2.0
pydantic-settings>=2.0
structlog>=24.0
mcp[cli]
```
⚠️ Fijar versiones exactas desde PyPI al momento de instalar.

---

*Última actualización: 2026-07-19*
