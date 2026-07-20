# gx-bt-rag-mcp — Documentación de planeación

Servidor MCP de **retrieval semántico puro** (sin LLM propio) sobre documentación de GeneXus y Bantotal, con una **capa de validación estructurada** que verifica sintaxis y firmas contra un catálogo, para reducir la alucinación de agentes IA en un entorno bancario.

---

## Orden de lectura

1. **`gx-bt-rag-punto-de-partida-desarrollo.md`** — EMPEZAR AQUÍ.
2. **`gx-bt-rag-mcp-stack.md`** — El QUÉ. Stack tecnológico, colecciones, tools.
3. **`CLAUDE.md`** — El CÓMO. Reglas duras y método de trabajo por fases.
4. **`gx-bt-rag-fase1-caracterizacion-corpus.md`** — La FASE 1. Cómo conocer el corpus.
5. **`gx-bt-rag-calidad-y-no-alucinacion.md`** — La FIABILIDAD.
6. **`gx-bt-rag-crecimiento-controlado.md`** — El CRECIMIENTO.
7. **`gx-bt-rag-ui-gestion-corpus.md`** — La GESTIÓN.

---

## Principios que atraviesan todo

- **Dos capas con garantías distintas.** Retrieval (semántico) *mitiga* la alucinación; validación (catálogo SQLite) la *corta*. No mezclar.
- **Fiabilidad sobre velocidad.** Entorno bancario: auditabilidad, procedencia, control humano donde importa.
- **Fases con gates.** No se cruza una puerta sin que su verificación pase.
- **El agente no inventa.** El catálogo es la fuente de verdad; lo no catalogado es NO_VERIFICABLE, no se asume.

---

## Estado del proyecto

**Planeación: madura. Desarrollo: en curso (Fase 1).**
