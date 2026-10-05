---
source_type: "video_training"
source_video: "HUB - SCAN SGR.mp4"
title: "Capacitación Bantotal — Hub   Scan Sgr"
module: "Buenas Prácticas Bantotal"
duration: "04:54"
programs_involved:
  - "FCT034"
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Hub   Scan Sgr

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 04:54  
**Programas / Transacciones:** `FCT034`  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: `FCT034`.

---

## Flujo Operativo y Detalle Temporal

### [02:00] Paso Operativo

**Explicación del Relator:**
> para realizar dichos controles. Utilizaré el XPZ de un programa tipo WebPanel que funciona como una RTE que hace control de importes al dar de alta un depósito a plazo fijo. Lo subimos, observamos que ya nos solicita agregar una descripción, ya que ésta es igual al nombre del programa. Escribiremos RTE control de importes. Son realizados controles y se obtienen alertas de nuestro WebPanel. Observamos que indica líneas de código donde se deberá corregir. Empezaremos por línea 38, donde dice los filtros del forage deben ir contra una sola variable a tributo y sin funciones. Iremos entonces a GeneXus, donde podremos observar lo siguiente en la línea 38.


### [02:46] Paso Operativo

**Explicación del Relator:**
> Existe un filtro de un footage contra un valor constante, en este caso el número 30. Estamos referenciando una guía de procesos, pero no de la manera correcta, ya que en este caso debería estar contra una variable. Definiremos entonces una variable previamente, ptpCode, a la cual asignaremos el número de guía correspondiente. Luego, el filtro, haremos contra la misma. De la forma WHERE tpcode="ptpcode", estaría correcto. Para saber sobre qué línea estamos situados, basta con observar la esquina inferior derecha donde se podrá ver el número de la misma. Guardaremos entonces. Y exportaremos el XPZ con los cambios realizados.


### [03:38] Paso Operativo - Transacción `FCT034`

**Explicación del Relator:**
> Regresaremos y lo subiremos nuevamente. Podemos observar que la única advertencia que sigue apareciendo es la del uso del For Each sin índice, línea 69. El motivo de que este control sea realizado es que, en el caso de que se realice una consulta a la base de datos sin incluir un índice, la generación del código GeneXus no toma el mejor índice y el programa podría resultar más lento. Volveremos entonces al código a agregarlo. Sobre línea 69, observamos que la tabla utilizada para la consulta es la FCT034, transacciones. Por lo tanto, listaremos la tabla y buscaremos al índice más apropiado para dicha consulta. En este caso sería PGCODE TRMOD TRNUMERO. Los agregamos, guardamos y volvemos a exportar el XPZ. Regresamos a Inspector a probar nuevamente. Tendría que pasar que ahora el fuente sea correcto, ya que hemos corregido todos los casos indicados.


### [04:44] Paso Operativo

**Explicación del Relator:**
> Y así es, el fuente es correcto.


### [04:48] Paso Operativo
