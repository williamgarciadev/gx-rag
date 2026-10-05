---
source_type: "video_training"
source_video: "Buenas prácticas - Uso del debug.mp4"
title: "Capacitación Bantotal — Buenas Prácticas   Uso Del Debug"
module: "Buenas Prácticas Bantotal"
duration: "02:36"
programs_involved: []
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Buenas Prácticas   Uso Del Debug

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 02:36  
**Programas / Transacciones:** N/A  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: operaciones estándar.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo


### [00:10] Paso Operativo

**Explicación del Relator:**
> Después otro error importante es el tema de debug, mensajes de debug. Muchas veces nosotros cuando


### [00:20] Paso Operativo

**Explicación del Relator:**
> hacemos el programa escribimos un mensaje para cuando lo estamos probando o cosa de saber por


### [00:30] Paso Operativo

**Explicación del Relator:**
> dónde va el programa y poder determinar si hay algún error o no. Pero muchas veces el error que cometemos que ese debug sigue y se va a producción sin condicionar a nada. Entonces el cliente me


### [00:40] Paso Operativo

**Explicación del Relator:**
> me dice, no, mirá, la consola del servidor de procesos o en el Tonga de la aplicación, me aparece el debug de mensaje, entra acá, sale. Tener cuidado con eso. Acá hay un caso de un


### [00:50] Paso Operativo

**Explicación del Relator:**
> programa que tiene los mensajes así, sin condicionar. Entonces, lo que se utiliza


### [01:00] Paso Operativo

**Explicación del Relator:**
> siempre es, se genera un campo información o una bandera, algo que determine si yo tengo que generar debug o no. En producción va a estar siempre inhabilitado. Yo no puedo generar debug,


### [01:10] Paso Operativo

**Explicación del Relator:**
> pero si yo tengo que hacer una prueba en algún ambiente o algo y tengo que reproducir un caso le digo habilita esto que ahí te va a generar debug, entonces todo lo que son print o escribir


### [01:20] Paso Operativo

**Explicación del Relator:**
> en la consola o lo que sea condicionado a if, si tengo que imprimir debug, si, si no, no, no puede


### [01:30] Paso Operativo

**Explicación del Relator:**
> estar a fuego en el programa y que siempre se ejecute el debug, eso no puede pasar. ¿Y qué pasa? Hay clientes primero que nada que lo ven el resultado del debug, en un lugar donde no debe nada más,


### [01:40] Paso Operativo

**Explicación del Relator:**
> en producción, entonces se genera doble ruido y el otro problema es que hay algunos clientes que tienen derecho a fuentes, incluso como parte de los contratos nosotros una cosa que hacemos es la


### [01:50] Paso Operativo

**Explicación del Relator:**
> entrega y la certificación de las fuentes, entonces en esas actividades sacan este tipo de cosas, entonces hay que ser muy


### [02:00] Paso Operativo

**Explicación del Relator:**
> profesional en los mensajes que se cuelan, por más que sea un debug de prueba y estemos en un entorno de desarrollo y lo vayamos a sacar, por las dudas si algún día se llega a ir, además de manejar este condicionamiento, me va a dejar terminado y se pueda reproducir.


### [02:10] Paso Operativo


### [02:20] Paso Operativo


### [02:30] Paso Operativo
