---
source_type: "video_training"
source_video: "Exotic - Vos Navegas.mp4"
title: "Capacitación Bantotal — Exotic   Vos Navegas"
module: "Buenas Prácticas Bantotal"
duration: "01:50"
programs_involved:
  - "SNG410"
tables_touched:
  - "FSD010"
  - "FSD11"
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Exotic   Vos Navegas

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 01:50  
**Programas / Transacciones:** `SNG410`  
**Tablas:** `FSD010`, `FSD11`

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: `SNG410`.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo


### [00:10] Paso Operativo

**Explicación del Relator:**
> El siguiente caso lo llamamos ¿vos navegas? y primero vamos a ir a la definición de navegación, que es la acción de navegar, navegación aérea, navegación marítima o


### [00:20] Paso Operativo

**Explicación del Relator:**
> viaje o jornada que se hace en un barco o en una nave. La navegación no es solo


### [00:30] Paso Operativo

**Explicación del Relator:**
> salir a un mercado, es fundamental revisar lo que hacen los programas en base a la navegación generada por GeneXus. En el código que vemos en pantalla se pretende


### [00:40] Paso Operativo - Transacción `SNG410`

**Explicación del Relator:**
> desde la tabla SNG410, acceder al estado de cada operación en la FSD11, y luego actualizar


### [00:50] Paso Operativo

**Explicación del Relator:**
> el dato en la FSD010. Si vamos a revisar la navegación GeneXus, observamos que por cada


### [01:00] Paso Operativo - Transacción `SNG410`

**Explicación del Relator:**
> registro de la SNG410 se dispara una subrutina para acceder a la FSD11. Ese acceso, al no tener especificado el índice adecuado, lo instancia por la clave primaria de la tabla,


### [01:10] Paso Operativo

**Explicación del Relator:**
> de la que solo se cuenta con la empresa y la sucursal, eso provoca que se estén recorriendo


### [01:20] Paso Operativo

**Explicación del Relator:**
> muchos registros de manera innecesaria que degradan la performance, luego se reitera el error, porque al grabar el dato en la FCD 010, nuevamente no se instancia el índice


### [01:30] Paso Operativo

**Explicación del Relator:**
> adecuado y también se recorren muchos registros, el error es evidente en la navegación, por


### [01:40] Paso Operativo

**Explicación del Relator:**
> por lo que es obligación de cada programador revisarla antes de compilar coladas.
