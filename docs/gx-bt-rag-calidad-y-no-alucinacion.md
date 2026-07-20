# No-alucinación y calidad de ingesta

Documento de planeación. Reúne las preguntas que deben responderse para que el sistema (a) no alucine y (b) ingeste con calidad.

---

## 1. Definir qué es "alucinar"

- Inventar un método/objeto/tabla que no existe.
- Dar una firma de la **versión equivocada** (GX16 vs GX18, versión Bantotal).
- Citar una fuente que **no dice** lo que se afirma.
- "Alucinar en negativo": decir INVÁLIDO/NO_VERIFICABLE sobre algo que sí es válido pero no está catalogado.

---

## 2. Distinguir "no lo sé" de "no existe"

`verify_syntax` debe distinguir explícitamente tres estados: **VÁLIDO / INVÁLIDO / NO_VERIFICABLE** y comunicar claramente cuándo la causa es "no catalogado" y no "no existe".

---

## 3. Jerarquía de autoridad

- XPZ de la KB → manda sobre la wiki (estado real de los objetos)
- Wiki → autoridad para lenguaje GeneXus genérico
- Manual Instalador → autoridad para procesos/programas Bantotal

---

## 4. Conjunto de evaluación

De dónde salen los casos de verdad, cuántas coberturas, qué métricas importan:

| Métrica | Definición |
|---|---|
| Precisión | fraction of verify_syntax results that are correct |
| Recall | fraction of true valid methods that are catalogued |
| Tasa de falso INVÁLIDO | métodos válidos marcados como INVÁLIDO |
| Tasa de mala atribución | fuentes mal citadas |

---

## 5. Feedback loop de método faltante

Cada INVÁLIDO/NO_VERIFICABLE se registra en `missing_methods`, priorizado por frecuencia. Lo más consultado y ausente sube al tope de la lista de curación.

---

## 6. Auditoría

Cada veredicto de validación se registra en `validation_log`: qué se consultó, qué se devolvió, con qué versión y fuente. Rastro auditable para Riesgo/Cumplimiento.
