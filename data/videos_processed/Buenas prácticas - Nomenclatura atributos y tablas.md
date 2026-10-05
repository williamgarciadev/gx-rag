---
source_type: "video_training"
source_video: "Buenas prácticas - Nomenclatura atributos y tablas.mp4"
title: "Capacitación Bantotal — Buenas Prácticas   Nomenclatura Atributos Y Tablas"
module: "Buenas Prácticas Bantotal"
duration: "03:48"
programs_involved: []
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Buenas Prácticas   Nomenclatura Atributos Y Tablas

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 03:48  
**Programas / Transacciones:** N/A  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: operaciones estándar.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo


### [00:10] Paso Operativo

**Explicación del Relator:**
> Otro error muy común que a veces pasa es el tema de nomenclatura de atributos y de tablas. ¿No? A veces que nos piden, tengo que crear una nueva tabla. Ahí está si la tabla va a ser


### [00:20] Paso Operativo

**Explicación del Relator:**
> institucional, si se puede reutilizar para todo, si va a ser parte del core o si es particular. Acá hay un caso de ejemplo, se definió una tabla nueva, ¿no? Y lo que se hizo fue,


### [00:30] Paso Operativo

**Explicación del Relator:**
> se definió igual a una tabla que ya existían en GeneXus, ya la teníamos nosotros, parte del modelo.


### [00:40] Paso Operativo

**Explicación del Relator:**
> GeneXus lo que hace, cuando vos tenés una tabla que ya existe y con todas las mismas claves,


### [00:50] Paso Operativo

**Explicación del Relator:**
> lo que hace es tratar de incorporarla a la tabla que ya está, porque dice, ahora se llama, los atributos son de la misma forma y todo lo que hago es, trato de incorporarla a lo que ya tengo,


### [01:00] Paso Operativo

**Explicación del Relator:**
> o hacer relaciones para tratar de generar el menor impacto posible. Entonces cuando yo hago una tabla que tiene nombres de atributos institucionales que ya existen en el tableto,


### [01:10] Paso Operativo

**Explicación del Relator:**
> tener cuidado cuando yo le impacte y analizar ese impacto. ¿Qué es lo que me va a crear GeneXus? ¿Qué va a hacer? ¿Se entiende? En este caso se impactó esa tabla y lo que se hizo fue,


### [01:20] Paso Operativo

**Explicación del Relator:**
> se envió con atributos que estaban mal definidos. Acá tenemos un caso más total mismo que tenemos


### [01:30] Paso Operativo

**Explicación del Relator:**
> una versión que está en producciones GX8 y otra versión que está en producciones GX9. GX8 y GX9, además de diferenciarse por la versión de GeneXus, se diferencian porque


### [01:40] Paso Operativo

**Explicación del Relator:**
> tienen una estructura diferente de tablas. Lo que se hizo fue, en GX9 se evolucionó la versión de GX8 y hay atributos que cambiaron. En GX8, por ejemplo, yo tengo el número de relación,


### [01:50] Paso Operativo

**Explicación del Relator:**
> un clásico problema que daba, es de numérico de 4. 9.999 nomás podría generar relación.


### [02:00] Paso Operativo

**Explicación del Relator:**
> el 9 se impactó al numérico 9, se extendió, entonces esta tabla se hizo para GX9 pero se hizo utilizando el largo de los atributos de GX8, cuando se envió al modelo a impactarse modificó


### [02:10] Paso Operativo

**Explicación del Relator:**
> todos los atributos que se sumaban igual, lo modificó todo desde los guardas largo nuevo que


### [02:20] Paso Operativo

**Explicación del Relator:**
> era el largo más chico, entonces tuvo un impacto muy grande en lo que es la base de datos que teníamos, entonces ojo al modificar las tablas, ojo con los atributos, los nombres de los atributos,


### [02:30] Paso Operativo

**Explicación del Relator:**
> que si yo estoy modificando un atributo que es parte del core institucional tener cuidado, si se llama igual, genesis o van felix que es el mismo atributo y tengo que tener cuidado y saber


### [02:40] Paso Operativo

**Explicación del Relator:**
> diferenciar bien si estoy parado en gx8 o gx9 y qué largo tienen los atributos, son 15 o 20


### [02:50] Paso Operativo

**Explicación del Relator:**
> atributos que cambiaron entre gx8 y gx9, pero esos 15 son muy grandes, yo si toco uno puedo entonces tener cuidado de eso. Después yo ahora les voy a pasar cuáles son los


### [03:00] Paso Operativo

**Explicación del Relator:**
> atributos que cambian, 18 y 9, para que ustedes les presten atención a esos casos, ¿no? bien


### [03:10] Paso Operativo


### [03:20] Paso Operativo


### [03:30] Paso Operativo


### [03:40] Paso Operativo
