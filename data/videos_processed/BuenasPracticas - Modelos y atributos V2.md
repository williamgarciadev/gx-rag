---
source_type: "video_training"
source_video: "BuenasPracticas - Modelos y atributos V2.mp4"
title: "Capacitación Bantotal — Buenaspracticas   Modelos Y Atributos V2"
module: "Buenas Prácticas Bantotal"
duration: "03:43"
programs_involved: []
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Buenaspracticas   Modelos Y Atributos V2

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 03:43  
**Programas / Transacciones:** N/A  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: operaciones estándar.

---

## Flujo Operativo y Detalle Temporal

### [00:37] Paso Operativo

**Explicación del Relator:**
> máximo de 10, por ejemplo. Pero después tenemos otros modelos que son como BT Workflow o modelos


### [00:40] Paso Operativo

**Explicación del Relator:**
> micro que trabaja con atributos de largo 30. ¿Cuál es un error muy común que se comete


### [00:51] Paso Operativo

**Explicación del Relator:**
> y queremos tratar de evitarlo? Es que se llevan transacciones de atributos de nombre largo


### [00:56] Paso Operativo

**Explicación del Relator:**
> al modelo de atributos con nombre corto. ¿Qué pasa con eso? En eso cuando vos haces el traspaso lo que hace es corta los atributos entonces un atributo que tenía un largo de 15 cuando vos lo llevás a un modelo de largo 10 en GeneXus lo que hace es corta el atributo en 10 entonces cuando vos lo quieras utilizar en la base de datos se va a llamar con el largo 15 y en GeneXus se va a llamar con el largo 10 entonces eso te da rol uno de los errores que encontramos en una rutina institucional cuando el concepto institucional o no lo tienen bien una rutina institucional se modificó y se accedió a una tabla que después se lo voy a mostrar se accedió a una tabla que tiene atributos largos en un modelo que tiene atributos cortos qué pasa con eso en la ejecución explota porque porque cuando GeneXus cree la sentencia va a decir yo quiero acceder al atributo con nombre si se llamaba tenía un largo 15 va a acceder con el nombre 10 cuando vaya a la base de datos va a decir este atributo no existe qué pasa con eso Acá hay dos temas importantes a destacar. Uno es, ¿no se probó? Porque eso sí se prueba en cualquier ambiente de error. Y la segunda es, ¿se está accediendo a atributos en un programa institucional a una tabla que está en otro modelo que tiene un atributo largo? Por lo cual hay que tener cuidado a la hora de modificar, ¿no? ¿Qué puedo llevar? ¿Qué tablas puedo llevarme de de atributos largos y de atributos cortos, ¿se entiende? porque lo que se hizo acá fue una tabla que estaba en un modelo de micro de Bethe Wolflo, ¿no? se sacó, se creó como fachada pero en un modelo de atributos cortos, ¿se entiende? yo puedo hacer de pasar una tabla como fachada de algún modelo, pero tengo que tener cuidado en qué largo que estoy manejando en cada tabla y en los modelos, ¿no? y es un error muy común que a veces pasa, pero pasa cuando no se prueba, porque si hubiese probado, te daba cuenta que daba mal, entonces cuidado con hacer el pasaje de cosas de modelos con atributos largos como puede ser micro, como puede ser workflow, a atributos, perdón a modelos con atributos cortos como puede ser el modelo web o el total web o eso, ¿no? bien
