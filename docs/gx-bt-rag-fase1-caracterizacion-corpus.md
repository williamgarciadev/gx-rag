# Fase 1 — Caracterización del corpus

Documento de planeación. Define **qué** hay que conocer del corpus antes de diseñar el ingestor, el esquema del catálogo y las reglas de extracción.

---

## 1. Objetivo

Producir un **documento de caracterización del corpus**: por cada fuente, una descripción estructurada de su contenido, sus patrones de identificación, su formato físico y sus relaciones extraíbles.

---

## 2. Fuentes a caracterizar

| Fuente | Naturaleza | Método | Aporta principalmente a |
|---|---|---|---|
| Manual Instalador (PDF) | Técnico: programas, procesos, tablas, pasos de instalación | Lectura texto nativo (PyMuPDF) | `gx_syntax`, `cross_ref` |
| Manual de Usuario (PDF) | Funcional: semántica de negocio, transacciones por módulo | Lectura texto nativo | `bt_docs` |
| Wiki GeneXus | Lenguaje y objetos GeneXus base | Scraper | `gx_syntax`, `gx_docs` |
| XPZ de la KB | Firmas reales de objetos propios (BC, SDT, procedures) | Skill análisis XPZ | `gx_syntax`, `cross_ref` |

---

## 3. Ingesta unificada e idempotencia

- **Entrada multiformato, pipeline único**: URL, HTML, Markdown, DOCX, PDF → normalizados a Markdown curado.
- **Identidad combinada**: `source_key` + `content_hash` para deduplicación y detección de cambios.
- **Alcance página única** (sin crawl).
- Normalización de `docs.genexus.com` y `wiki.genexus.com` a un mismo `source_key`.

---

## 4. Curación asistida

El PDF/artículo no entra crudo. Reglas automáticas producen un borrador limpio; humano valida lo crítico.

| Fuente | Nivel de curación |
|---|---|
| PDF Bantotal | Fuerte (ruido de maquetación) |
| Wiki GeneXus | Media (quitar navegación web) |
| XPZ de la KB | Baja (ya estructurados) |

El resultado curado es un **artefacto versionado** que conserva procedencia al original.

---

## 5. Tabla de prefijos (punto de partida)

| Prefijo | Tipo | Tabla destino |
|---|---|---|
| `FST` | Tabla de parametrización | catálogo tablas |
| `FSD` | Tabla de datos | catálogo tablas |
| `FSR` | Tabla de relación | catálogo tablas |
| `HCDT` | Panel / WebPanel | `gx_syntax` |
| `PCDT` | Proceso / procedimiento | `gx_syntax` |
| `PNU` | Proceso batch | `gx_syntax` |
| `PDP` | Proceso batch (depósitos) | `gx_syntax` |
| `PRTE` | Rutina de plazo fijo | `gx_syntax` |
| `PRTE…WEB` | Variante web | `gx_syntax` |

---

## 6. Gate de la Fase 1

- [ ] Las 4 fuentes caracterizadas
- [ ] Tabla de prefijos validada contra identificadores reales
- [ ] Decisión OCR documentada con evidencia
- [ ] Flujo de curación definido y al menos 1 muestra
- [ ] Idempotencia validada con reingesta real
- [ ] Artefacto curado de muestra conservando procedencia
