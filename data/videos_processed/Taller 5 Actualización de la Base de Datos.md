---
source_type: "video_training"
source_video: "Taller 5 Actualización de la Base de Datos.mp4"
title: "Capacitación Bantotal — Taller 5 Actualización De La Base De Datos"
module: "Buenas Prácticas Bantotal"
duration: "05:55"
programs_involved: []
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Taller 5 Actualización De La Base De Datos

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 05:55  
**Programas / Transacciones:** N/A  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: operaciones estándar.

---

## Flujo Operativo y Detalle Temporal

### [00:58] Paso Operativo

**Explicación del Relator:**
> Por ejemplo, acá, este está tirando 61 veces el update y miren las veces que está updateando. Este acá, abajo, está tirando, acá el insert, 569 veces y ¿saben cuánto está dando bien? 356 veces. 200 veces está dando clave duplicado. Para que vean la diferencia es el count de la cantidad de veces que lo tira el total count, la cantidad de veces que ejecutó correctamente el insert. Estoy preguntando muchas veces y lo que me está diciendo eso es que estoy dando clara duplicada. Y muchas veces veo la estadística, veo que tiró un millón de inserts y solo 20 dieron... Ok, ¿qué me está diciendo eso? Que yo tengo que cambiar el order. Porque en vez de tener primero insert y después update, lo que casi siempre me está diciendo es... Ah, entonces cambio el order. Acá, en este caso, no pusimos la solución. Pero acá lo mejor es decirle, bueno, me encargo una variable que me determina si graveo o no el load. Y después abajo, digo, si grabé algo, hago el update, y si no, hago el GNS. El GNS, pero que en cierto punto impacta considerablemente. Eso pensando, por ejemplo, en un servicio. Pensando en un batch, por ejemplo, muchas veces nosotros tenemos que recorrer operaciones y agrupamos por monedas o por el siguiente de algo, y sumarizamos en una tabla. Que es este caso acá. Estoy recorriendo, por ejemplo, todas las operaciones de la 11 para una sucursal. Y voy a genero tabla. Y el genero tabla, lo que hace es, va acumulando, estoy procesando cada una de las operaciones y tengo que insertar cada una de nuestras tablas. Puse, porque este es el segundo operador. Por lo general es que siempre va a dar esto. Va a dar OK el insert. Porque lo que estoy haciendo es generando operación a operación. Si yo sé que no me va a dar Cloud Cache, que estoy procesando operaciones y voy a generar operaciones, hago el insert de una y no hago el update. Porque digo, porque aunque el update no le cueste, va a tirar un update. Que esto sí es lo que está pasando en este programa que vimos acá. Acá tenía un ABDE que está tirando 61 veces y está dando 66 veces.


### [02:47] Paso Operativo

**Explicación del Relator:**
> ¿Claro? ¿Por qué? No encuentra registro. Entonces, eso es importante preguntar. Y así como este, hay otro ejemplo más. Este sí es. Recorro las operaciones y grabo fila. Entonces, grabar fila acá, esta subrutina lo que hace es, como verán, está ocupando por moneda. Pero ponele que la clave va hasta acá. Y vieron que si hace NEW y si el NEW da error, sumariza. ¿Qué es lo que pasa acá? Él, como está procesando por operación y está ocupando por moneda, El 99, ¿por qué? Porque ya la moneda, ya la persona, va a ser un índice, va a dar clave oblica y después va a ser clave. Entonces, ¿qué tengo que hacer acá? Y cambio el orden. Agarro primero hago el update y después hago el índice. Porque yo ya sé que lo que estoy pensando va a ser acumulado y tengo que ir sumando. Y lo mejor sería, lo mejor sería en este caso, hacer esto que está acá abajo. Que lo que voy a hacer es, en vez de tirar tanto sarde, en memoria me voy acumulando en un vector, y al final paso el origen de la tabla. ¿Qué hace el paso del origen de la tabla? Recorre. Acá pusimos grabo fila en memoria. ¿Qué significa esto? Que lo que hace es, primero verifica si esa moneda, o esa clave, está en el vector o no. ¿Qué hace esto? Maneja una estructura, que es moneda, índice, plazo, no sé qué. ¿Qué dice? Si lo que yo estoy expresando está acá en el vector, cargo y te devuelvo la posición en la que tengo que ir. Y eso es lo que hace acá arriba y dice, si no tengo rojito en el vector, lo agrego al vector. ¿Estamos de acuerdo? Incremento la posición y agrego todos los datos al vector. Si tengo, lo único que hago es sumaricio. Hay muchas condiciones acá, pero solo para cubrirse, que no se vaya de rasgo, pero... Lo que hago es solo, pumbate, sumaricio, y me voy cargando en este vectorcito todo. Cuando llevo un tope, que sería superé las 9999 posiciones, lo paso a tratar. Esto podría ser más sencillo si yo sé que no me voy a llegar nunca al vector, lo grabo acá de una nomás, y después al final recorro ese vector y lo inserto. ¿Y esto en qué se va a traducir? Es que en vez de mandar un millón de abres, va a mandar cincuenta y seis. Y eso se traduce, en este caso fue uno de los programas que se modificó, Y pasó de demorar 4 horas a demorar 15, incluso 20. El índice, el dato y todo eso, a la base de esto le cuesta todo. El genera load, genera miles de cosas para después de componerse. Actualiza el índice, todo eso. Entonces, hay que evitar hacer todas estas cosas. Tratando de, siempre que se pueda, ir a una solución de estas. Que a ver, lo que vamos es definir por qué estoy agrupando. Tengo 4 vectores, 5 dependiendo de lo que sea la clave. Y sumar y sumar, mantiene mucha complejidad. Además, esto es copy-paste. Pero si es que es importante tenerlo en cuenta, porque miren que a veces los casos bordes son estas cosas. El programa que nacieron con el incendio y después la verde, o al revés, para gustarnos. Y eso te da la estadística. Si yo veo que están tirando un millón de incendios y solo caíste, ¡tá!
