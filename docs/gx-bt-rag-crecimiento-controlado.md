# Crecimiento controlado del corpus

Documento de planeación. Define cómo el sistema **crece** incorporando contenido nuevo sin comprometer la fiabilidad.

---

## 1. Principio rector

**Descubrimiento automático, incorporación con control.** El sistema puede descubrir candidatos solo; la entrada al corpus pasa siempre por una compuerta humana.

Ingesta automática desde web abierta sin control = meter contenido que nadie validó. En banca eso es inaceptable.

---

## 2. Ciclo

1. **Detección de necesidad (automática).** `missing_methods` registra cada INVÁLIDO/NO_VERIFICABLE consultado.
2. **Descubrimiento (automático).** Para ítems más pedidos y ausentes, sugiere fuentes candidatas.
3. **Propuesta al humano.** Cada candidato con fuente, nivel de autoridad, capa destino.
4. **Aprobación humana.** Compuerta obligatoria.
5. **Curación e ingesta.** Lo aprobado pasa por pipeline de curación.
6. **Cierre del ciclo.** Al catalogarse, sale de la cola de método faltante.

---

## 3. Dos capas con autoridad distinta

- **Retrieval (`gx_docs`)**: mejores prácticas, guías, patrones. Aprobación ligera, procedencia marcada.
- **Catálogo (`gx_syntax`)**: sintaxis, firmas, objetos. Compuerta estricta, solo fuentes de autoridad.

---

## 4. Lista blanca de dominios

Solo se descubren candidatos de:
- wiki.genexus.com / docs.genexus.com
- documentación oficial Bantotal
- training.genexus.com

La web abierta queda excluida.

---

## 5. Decisiones abiertas

- ¿Con qué periodicidad corre el descubrimiento? (lote semanal vs. umbral de consultas)
- ¿Cómo se presenta la bandeja de sugerencias al humano?
- ¿Qué pasa si no se encuentra fuente para un ítem muy pedido?
