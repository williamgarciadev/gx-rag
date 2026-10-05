---
source_type: "video_training"
source_video: "Opciones de proceso - Implementador - Programador.mp4"
title: "Capacitación Bantotal — Opciones De Proceso   Implementador   Programador"
module: "Buenas Prácticas Bantotal"
duration: "03:04"
programs_involved:
  - "HPOP011M"
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Opciones De Proceso   Implementador   Programador

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 03:04  
**Programas / Transacciones:** `HPOP011M`  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: `HPOP011M`.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo

**Explicación del Relator:**
> En esta presentación estaremos viendo los aspectos principales de la utilización de opciones de proceso. Las opciones de proceso operan a modo de interruptor, habilitando o deshabilitando distintas funcionalidades en los programas GeneXus.


### [00:10] Paso Operativo

**Explicación del Relator:**
> En función de su valor, S o N, los programas que consultan una opción de proceso ejecutarán o no alguna acción.


### [00:20] Paso Operativo

**Explicación del Relator:**
> También existen opciones por módulo. Las mismas operan de la misma manera, con las salvedades que permiten dar una mayor especificación de la operativa sobre la cual se quiere realizar el control,


### [00:30] Paso Operativo

**Explicación del Relator:**
> indicando el módulo que será controlado. Aquí se ve la estructura de la tabla FST-200 de opciones de proceso. Esta se compone con el código de la empresa, código de opción general, un campo para almacenar una descripción,


### [00:40] Paso Operativo

**Explicación del Relator:**
> y otro para el valor de la opción S o N. Vemos un ejemplo de un panel que funciona como ventana emergente de selección de operaciones.


### [00:50] Paso Operativo - Transacción `HPOP011M`

**Explicación del Relator:**
> el programa HPOP011M. Este panel se compone de una grilla con las operaciones registradas con


### [01:00] Paso Operativo

**Explicación del Relator:**
> su respectiva información. Las columnas corresponden al número de operación, su operación, tipo de operación, fecha valor, fecha de vencimiento, plazo, sucursal y saldo.


### [01:10] Paso Operativo

**Explicación del Relator:**
> Esta última columna se muestra por defecto, pero se puede ocultar configurando la opción


### [01:20] Paso Operativo

**Explicación del Relator:**
> General de Proceso 62 con valor N. Luego de haber configurado la opción de Proceso 62


### [01:30] Paso Operativo

**Explicación del Relator:**
> en N, notamos que la columna Saldos ya no se visualiza.


### [01:40] Paso Operativo

**Explicación del Relator:**
> Vemos el código del panel, donde se accede a la tabla FST 200, filtrando por Empresa y Código de Opción de Proceso. En el primer Forge se obtiene el valor de la opción 62


### [01:50] Paso Operativo

**Explicación del Relator:**
> y la almacena en la variable Saldos. En la definición del panel está establecido que la columna en cuestión se habilitará cuando la variable saldo vale S.


### [02:00] Paso Operativo

**Explicación del Relator:**
> Se deben tener en cuenta las siguientes buenas prácticas. Definir valores por defecto antes de acceder a la tabla para que el programa se comporte de una manera esperada ante un error de parametrización.


### [02:10] Paso Operativo

**Explicación del Relator:**
> Al acceder a la tabla, filtrar por variables previamente definidas y no por valores a fuego. Asegurarse de que la variable que almacena el valor de la opción de proceso tenga un


### [02:20] Paso Operativo

**Explicación del Relator:**
> nombre representativo de lo que se activa o desactiva. Como se puede ver en el ejemplo, la variable se denomina saldos ya que justamente lo que


### [02:30] Paso Operativo

**Explicación del Relator:**
> almacena la opción de proceso es si los saldos se ven o no en el panel. En el documento de especificación técnica debe existir una referencia a qué opciones


### [02:40] Paso Operativo

**Explicación del Relator:**
> de proceso son utilizadas por el programa y qué impacto tiene en el mismo. Es importante aclarar qué comportamiento tendrá el programa cuando no se define una opción de proceso.


### [02:50] Paso Operativo

**Explicación del Relator:**
> Si este tutorial te fue de utilidad o existen elementos sobre los que quieras profundizar, Te invitamos a comentar y valorar este video. Muchas gracias.
