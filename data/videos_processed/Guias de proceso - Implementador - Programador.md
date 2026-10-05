---
source_type: "video_training"
source_video: "Guias de proceso - Implementador - Programador.mp4"
title: "Capacitación Bantotal — Guias De Proceso   Implementador   Programador"
module: "Buenas Prácticas Bantotal"
duration: "04:33"
programs_involved:
  - "D025"
  - "HPRE200"
tables_touched:
  - "FST098"
  - "FST198"
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Guias De Proceso   Implementador   Programador

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 04:33  
**Programas / Transacciones:** `D025`, `HPRE200`  
**Tablas:** `FST098`, `FST198`

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: `D025`, `HPRE200`.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo

**Explicación del Relator:**
> En esta presentación, estaremos viendo los aspectos principales de la utilización de guías de proceso. Las guías de proceso son tablas en las cuales se disponen campos para la reserva de valores y descripciones que los programas GeneXus utilizarán.


### [00:10] Paso Operativo

**Explicación del Relator:**
> Permiten centralizar datos particulares para que los programas los guíen y utilicen. Luego, si las condiciones del negocio cambian, tan solo se deberá de cambiar los valores de estas tablas y el programa adaptará su comportamiento sin necesidad de reprogramar.


### [00:20] Paso Operativo

**Explicación del Relator:**
> La registración de estas guías constituye parte de nuestro trabajo de parametrización


### [00:30] Paso Operativo

**Explicación del Relator:**
> del ambiente, llevado a cabo por implementadores Bantotal, mientras que el programador se encarga de resolver otros aspectos clave del comportamiento del programa, evitando hardcode o código


### [00:40] Paso Operativo

**Explicación del Relator:**
> a fuego. Las tablas a las que denominamos guías de proceso son dos, FST098 y FST198.


### [00:50] Paso Operativo

**Explicación del Relator:**
> Aquí se ve la estructura de la tabla FST-098 de guías de proceso. La misma provee un código


### [01:00] Paso Operativo

**Explicación del Relator:**
> de empresa, código de guía y correlativo. Estos 3 campos son los que identifican cada registro de parametrización. Además cuenta con un campo de valor específico que acepta


### [01:10] Paso Operativo

**Explicación del Relator:**
> números enteros, descripción que acepta caracteres e importe específico para importes con hasta 12 decimales. La diferencia entre la estructura de esta tabla y la FST-198 guías


### [01:20] Paso Operativo

**Explicación del Relator:**
> especiales de proceso, es solamente la cantidad de correlativos, valores e importes disponibles


### [01:30] Paso Operativo - Transacción `HPRE200`

**Explicación del Relator:**
> para el uso. Veamos un ejemplo, el panel de mantenimiento de productos FISTA por cuenta, el programa HPRE200, el mismo ofrece la posibilidad de


### [01:40] Paso Operativo

**Explicación del Relator:**
> visualizar el botón tarjeta de débito. Si ingresamos a dicho panel sin antes haber


### [01:50] Paso Operativo

**Explicación del Relator:**
> creado la guía de proceso correspondiente, el botón tarjeta de débito no se visualiza, Por lo tanto, proseguiremos creando dicha guía.


### [02:00] Paso Operativo

**Explicación del Relator:**
> Observando su documento de especificación técnica, nos indica que creando la guía de proceso 9845, si se desea habilitar el botón tarjeta de débito, se debe parametrizar


### [02:10] Paso Operativo

**Explicación del Relator:**
> con correlativo 5 y en la descripción indicar el programa de tarjeta de débito.


### [02:20] Paso Operativo

**Explicación del Relator:**
> Luego de haber creado la guía, ingresamos nuevamente al panel y ahora si podremos visualizar el botón tarjeta de débito, desde el cual es posible asociar tarjetas de débito a los distintos productos del cliente.


### [02:30] Paso Operativo

**Explicación del Relator:**
> Si vemos el código del panel, podremos ver que, al momento de inicio, se accede a la tabla FST 098 filtrando por empresa, código de guía y correlativo.


### [02:40] Paso Operativo

**Explicación del Relator:**
> Si se encuentra el correlativo 5, se da el valor S a la variable TARDEV y se obtiene el nombre de programa de tarjeta de débito desde el campo descripción.


### [02:50] Paso Operativo

**Explicación del Relator:**
> En la definición del panel, está establecido que el botón en cuestión se habilitará cuando la variable TARDEV vale S.


### [03:00] Paso Operativo

**Explicación del Relator:**
> Es una buena práctica comentar la tabla que se está intentando acceder y realizar una programación defensiva, es decir, en el caso de que un usuario no haya parametrizado la


### [03:10] Paso Operativo

**Explicación del Relator:**
> guía necesaria o lo haya hecho incorrectamente, el programa deberá tener un comportamiento adecuado, tal como asumir valores por defecto o notificar al usuario el error encontrado.


### [03:20] Paso Operativo - Transacción `D025`

**Explicación del Relator:**
> En este caso se define un valor por defecto a las variables TARDEV y TD, D025 y DF antes de acceder a la tabla.


### [03:30] Paso Operativo

**Explicación del Relator:**
> filtros se realizan por medio de variables previamente definidas. Tener en cuenta, a la hora de obtención de valores de la tabla, el valor exacto y el


### [03:40] Paso Operativo

**Explicación del Relator:**
> tipo de datos que se va a utilizar en el código. Por ejemplo, se usa la función trim para eliminar los espacios innecesarios a la derecha del nombre del programa parametrizado, ya


### [03:50] Paso Operativo

**Explicación del Relator:**
> que es de tipo carácter. El valor del campo de descripción de la guía deberá ser descriptivo según su funcionalidad. En el documento de especificación técnica


### [04:00] Paso Operativo

**Explicación del Relator:**
> debe existir una referencia a qué guías de proceso son utilizadas por el programa y qué impacto tienen en el mismo. Es importante mencionar cómo se comporta el programa ante


### [04:10] Paso Operativo

**Explicación del Relator:**
> una parametrización errónea o ausente. Las instrucciones de parametrización deben ser claras y se deben evitar ambigüedades. Si este tutorial te fue de utilidad o existen


### [04:20] Paso Operativo

**Explicación del Relator:**
> elementos sobre los que quieras profundizar, te invitamos a comentar y valorar este video. Muchas gracias.
