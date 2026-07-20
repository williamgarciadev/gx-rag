# Punto de partida para el desarrollo (Claude Code)

Instrucción de entrada para la primera iteración de desarrollo. Define qué está listo, qué se pide en esta iteración, y qué **no** debe decidirse sin el humano.

> Léelo junto con los otros tres documentos de planeación:
> - `gx-bt-rag-mcp-stack.md` — stack, tecnologías, decisiones descartadas.
> - `CLAUDE.md` — reglas duras y método de trabajo por fases.
> - `gx-bt-rag-fase1-caracterizacion-corpus.md` — caracterización del corpus.

---

## 1. Regla de oro de esta iteración

**Solo se desarrolla la Fase 1: caracterización del corpus y flujo de curación.** No se construye el RAG completo. No se avanza a fases posteriores sin cerrar el gate de la Fase 1 (ver su criterio de "hecho", sección 8 de ese documento).

Esto es fiel al método de fases con gates definido en `CLAUDE.md`: no se cruza una puerta sin que su verificación pase.

---

## 2. Qué está listo (no re-decidir)

- **Stack tecnológico**: definido en el `.md` de stack. SDK oficial `mcp` (no FastMCP standalone), ChromaDB local, embeddings `multilingual-e5-large`, SQLite para catálogo, todo local. Fijar versiones desde PyPI, nunca de memoria.
- **Principio de dos capas**: retrieval (mitiga alucinación) vs. validación (corta). No mezclar.
- **Ingesta unificada e idempotencia**: entrada URL/HTML/MD por pipeline único; identidad `source_key` + `content_hash`; alcance de página única (sin crawl); dominios `docs.genexus.com` y `wiki.genexus.com` unificados en un mismo `source_key`.
- **Curación asistida**: el PDF/artículo no entra crudo; reglas producen borrador de Markdown limpio, el humano valida lo crítico; el Markdown curado se conserva como artefacto versionado y es la entrada del pipeline.
- **Clasificación por género en la wiki**: referencia → catálogo; guía → retrieval; automática.
- **Taxonomía de prefijos Bantotal** (punto de partida): FST/FSD/FSR tablas, HCDT/PCDT programas (H=panel, P=proceso), PNU/PDP batch, PRTE rutinas, etc.

---

## 3. Qué se pide en esta iteración (Fase 1)

1. **Completar la caracterización de las cuatro fuentes.** Hoy están cubiertas PDF Bantotal y wiki GeneXus. Faltan:
 - **XPZ de la KB** (vía skill de análisis XPZ) — fuente más fiable para objetos propios.
 - **Manual de Usuario** (PDF funcional) — semántica de negocio, transacciones por módulo.
 Para cada una: tipología de contenido, patrones de identificación, formato físico, relaciones extraíbles, nivel de curación.

2. **Montar el flujo de curación asistida.** Reglas que produzcan un borrador de Markdown limpio a partir de una fuente, conservando procedencia al original (PDF/sección/página, o URL). Puntos de validación humana marcados.

3. **Producir artefactos curados de muestra**, versionados:
 - Al menos una sección del Manual Instalador (p. ej. una rutina HCDT/PCDT con su relación a tabla).
 - Los dos artículos wiki de prueba, uno de cada género:
 - `https://docs.genexus.com/en/wiki?24744,For+each+command` (referencia → catálogo)
 - `https://docs.genexus.com/en/wiki?26286,For+each+Optimizations` (guía → retrieval)

4. **Validar la idempotencia con un caso real**: reingestar la misma URL dos veces y confirmar que no duplica (upsert por `source_key`).

5. **Ampliar y validar la tabla de prefijos** contra los identificadores reales encontrados.

6. **Ingesta batch de la wiki (5.400 HTML), en dos tiempos con gate intermedio.** Existe un corpus local de ~5.400 documentos HTML de la wiki de GeneXus. NO se procesa de golpe:
 - **6a. Validar el método sobre una muestra** representativa: que la clasificación automática por género (referencia→catálogo / guía→retrieval) acierte, y que la curación automática no deforme el contenido. Este es el gate.
 - **6b. Solo tras pasar 6a**, lanzar el batch completo del directorio. Lanzar los 5.400 antes de validar multiplicaría por 5.400 cualquier error de curación.
 - Modo de ingesta: **batch de directorio local** (no crawl web; son archivos que ya se tienen). Es un modo nuevo del pipeline, distinto del "página única" de URLs sueltas.

7. **Estrategia de validación a escala** (decisión tomada): para retrieval, **automatización total** (no se revisan los 5.400 a mano); la validación humana se reserva **solo para el catálogo** (`gx_syntax`). La confianza en la automatización de retrieval se sostiene en el gate de muestra de 6a.

8. **Formatos de entrada** (decisión tomada): el pipeline unificado acepta URL, HTML, Markdown, **DOCX** y PDF, todos normalizados al formato interno común. Añadir DOCX es una entrada más al mismo pipeline, no un camino nuevo.

---

## 4. Qué NO debe cerrar por su cuenta (traer al humano)

Estas decisiones están abiertas a propósito. Claude Code **no las resuelve solo**; las plantea y espera decisión:

1. **Arquitectura monolito vs. dos servidores MCP** (sección 12 del stack). Determina si es un proyecto o dos y toda la estructura de carpetas. No asumir monolito por defecto.

2. **Esquema SQLite del catálogo**. No se fija hasta cerrar la caracterización (Fase 1). Es el corazón del diferenciador; se diseña deliberadamente en la fase siguiente, no se improvisa mientras se codea.

3. **Necesidad y alcance de OCR**. La evidencia preliminar dice que los PDF Bantotal tienen texto nativo (probablemente sin OCR), pero está condicionado a confirmar el resto de fuentes. No introducir dependencia de OCR/GPU sin decisión.

Si durante el trabajo aparece una cuarta decisión de este tipo, la trata igual: la plantea, no la cierra.

---

## 5. Criterio para dar por terminada esta iteración

La iteración termina cuando se cumple el criterio de "hecho" de la Fase 1 (sección 8 del documento de caracterización):
- Las cuatro fuentes caracterizadas.
- Tabla de prefijos validada y ampliada, con tipo y tabla destino por prefijo.
- Decidido, con evidencia, si se necesita OCR y para qué fuentes.
- Documentado qué `cross_ref` es extraíble de texto y qué requiere XPZ.
- Identidad `source_key` + `content_hash` validada con reingesta real sin duplicar.
- Flujo de curación asistida definido y al menos un artefacto curado de muestra que conserve procedencia.

Solo entonces se plantean al humano las decisiones de la sección 4 y se pasa a diseñar el esquema SQLite y las reglas del ingestor.

---

## 6. Cómo arrancar la sesión de Claude Code

1. Colocar los cuatro `.md` en la raíz del proyecto.
2. Ejecutar `/init` para generar/registrar el `CLAUDE.md` de proyecto (o usar el ya provisto).
3. Repo de referencia (solo lectura, no copiar código): clonar `knowledge-rag` en una carpeta aparte para consultar patrones de tools MCP y hybrid search.
4. Primer prompt: pedir explícitamente **solo la Fase 1**, empezando por caracterizar las fuentes faltantes (XPZ, Manual de Usuario) y montar el flujo de curación. Recordar: fijar versiones desde PyPI, y plantear —no cerrar— las decisiones de la sección 4.
