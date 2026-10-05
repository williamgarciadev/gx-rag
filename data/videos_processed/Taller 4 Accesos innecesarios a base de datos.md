---
source_type: "video_training"
source_video: "Taller 4 Accesos innecesarios a base de datos.mp4"
title: "Capacitación Bantotal — Taller 4 Accesos Innecesarios A Base De Datos"
module: "Buenas Prácticas Bantotal"
duration: "02:44"
programs_involved: []
tables_touched:
  - "FSD001"
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Taller 4 Accesos Innecesarios A Base De Datos

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 02:44  
**Programas / Transacciones:** N/A  
**Tablas:** `FSD001`

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: operaciones estándar.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo

**Explicación del Relator:**
> Nos pasa muchas veces y que es... Miren este acceso, acá. Acceder a FSD001, ¿no?


### [00:10] Paso Operativo

**Explicación del Relator:**
> Pero miren, si país es 845 o 845 y documento 1, este queda roto. Entonces, ¿para qué accedo a la 1 si se da esta posición? Distinto, me explico.


### [00:20] Paso Operativo

**Explicación del Relator:**
> Digo, bueno, si tal... Porque estos datos no los tengo en la tabla. Es decir, bueno, trato de... Si hay condiciones que la puedo resolver antes de acceder...


### [00:30] Paso Operativo

**Explicación del Relator:**
> Hay que tratar de pintar lo que se va a hacer. Todo lo que yo pueda resolver, antes de acceder a la base de actos, lo tengo que tratar de superar. Y muchas veces las reales condiciones, nosotros modificamos, que son a veces pequeñas,


### [00:40] Paso Operativo

**Explicación del Relator:**
> pero que a la suma terminan siendo completas. Entonces empezamos a verificar el tipo de persona, está bien. Pero siempre y cuando no sea esta condición, porque si sea esta condición,


### [00:50] Paso Operativo

**Explicación del Relator:**
> capaz que esta condición no se da casi nunca, pero bueno, por lo menos cuando se dé, la solución es la resolvible. Y lo mismo pasa acá abajo, este acceso está igual, le decía, bueno, en City ID accedo acá, en City DNS.


### [01:00] Paso Operativo

**Explicación del Relator:**
> Pero si ve cliente vacío, en City ID ya lo pongo en ese. Entonces, bueno, le digo, bueno, ¿cuál es la solución?


### [01:10] Paso Operativo

**Explicación del Relator:**
> Digo, bueno, City DNS, si el cliente vacío ya lo pongo en City ID, y en el ETH sí accedo a la tabla. ¿Me explico? O sea, preguntarle, decir, bueno, si yo hay condiciones que no necesito acceder a la tabla para verla, la evaluo antes.


### [01:20] Paso Operativo

**Explicación del Relator:**
> Entonces, algo importante es sobre la tabla de datos. Hay que tratar de evitar... Ah, sí, eso, la tabla de datos.


### [01:30] Paso Operativo

**Explicación del Relator:**
> La de caché, bueno, tengo la secuencia de caché que está mal también. Debería quedar así, pero bueno. Pero la que son de datos, tengo que tratar de evitar. Y estas cosas así se dan en muchos programas.


### [01:40] Paso Operativo

**Explicación del Relator:**
> Lo hacemos nosotros. A lo que voy es que siempre tratar de preguntarnos, cuando estoy modificando algo y el tiempo lo tengo, de poder por lo menos navego y más allá de mirar mi código


### [01:50] Paso Operativo

**Explicación del Relator:**
> y bueno, doy si veo que hay alguna cosa carrafal que la puedo tocar con una simple. Yo creo que es importante reutilizar, hay veces que tenemos momentos de prueba.


### [02:00] Paso Operativo

**Explicación del Relator:**
> No sé, por ejemplo, yo estoy tocando un programa que tengo, estoy tocando algo quirúrgico y que sé que se va a probar mucho, ya probé, yo toco alguna cosa más. Si veo que es algo, o sea, eso va en nosotros, es decir, ¿qué más puedo aportar?


### [02:10] Paso Operativo

**Explicación del Relator:**
> Porque hay veces que yo tengo que tocar algo así, que es un acceso mínimo y no va a ir con pruebas y nada. Bueno, no me voy a poner a tocar mucha cosa, capaz.


### [02:20] Paso Operativo

**Explicación del Relator:**
> Entonces ahí, o que me pasó a mi pro, ahora toqué alguna cosa que pensé que... Esto que estamos diciendo, mucho de estos códigos, lo hice yo, capaz.


### [02:30] Paso Operativo

**Explicación del Relator:**
> O sea, lo hizo el Blanco, todo. Si veo que gana, decía, estoy a dos, tres, etcétera, etcétera. Y bueno, trato de ya hacerlo.
