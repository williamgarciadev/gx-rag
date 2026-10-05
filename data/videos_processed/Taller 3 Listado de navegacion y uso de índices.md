---
source_type: "video_training"
source_video: "Taller 3 Listado de navegacion y uso de índices.mp4"
title: "Capacitación Bantotal — Taller 3 Listado De Navegacion Y Uso De Índices"
module: "Buenas Prácticas Bantotal"
duration: "08:49"
programs_involved:
  - "F0008"
tables_touched: []
tags:
  - "buenas prácticas bantotal"
  - "bantotal"
  - "training"
---

# Capacitación Bantotal — Taller 3 Listado De Navegacion Y Uso De Índices

## Resumen Ejecutivo de Negocio

**Módulo:** Buenas Prácticas Bantotal  
**Duración:** 08:49  
**Programas / Transacciones:** `F0008`  
**Tablas:** N/A

Capacitación operativa y técnica de Bantotal para el módulo de **Buenas Prácticas Bantotal**. Presenta la ejecución y parametrización de las transacciones principales: `F0008`.

---

## Flujo Operativo y Detalle Temporal

### [00:00] Paso Operativo

**Explicación del Relator:**
> Es importante que muchas veces no lo hacemos, o la mayoría de las veces no lo hacemos nadie, es esto.


### [00:10] Paso Operativo

**Explicación del Relator:**
> Alguien pide la navegación cuando yo le da de acá.


### [00:20] Paso Operativo

**Explicación del Relator:**
> Es que casi no se está mirando, porque a veces no se mira. Eso es importante mirar.


### [00:30] Paso Operativo

**Explicación del Relator:**
> Porque en cierto punto GeneXus está avisando de cosas acá cuando navegas. Y cuando yo tengo acceso a tabla que estoy recorriendo, acá como verán de vuelta cayeron,


### [00:40] Paso Operativo

**Explicación del Relator:**
> acceso a tabla y dependiendo de cierta tabla tengo que tener cuidado con acceso. Por ejemplo,


### [00:50] Paso Operativo

**Explicación del Relator:**
> en este particular si bien está usando un índice que va por módulo, acá en cierto punto se está


### [01:00] Paso Operativo

**Explicación del Relator:**
> procesando casi todo aunque tiene que entrar por cuenta para un cliente específico. Entonces acá el en cierto punto va a usar un índice eso no lo podemos garantizar pero debería debería


### [01:10] Paso Operativo

**Explicación del Relator:**
> pero está y acá también porque porque no estamos filtrando por empresa y si yo filtro por empresa


### [01:20] Paso Operativo

**Explicación del Relator:**
> lo más seguro que me tome el índice de empresa y cuenta está filtrando entonces cuando yo navego cuando yo especifico navego me doy cuenta de estas cosas escribiendo que a la 11 le va a


### [01:30] Paso Operativo

**Explicación del Relator:**
> a pegar por módulo y capaz que tengo que abrir los ojos ahí y ver. A ver, va a ser alto si no montemos para determinar cómo va a resultar. Si yo le digo tipo un order en cierto punto, le estoy diciendo que a él devolvérmelo de esa manera,


### [01:40] Paso Operativo

**Explicación del Relator:**
> él va a tratar de ir por ese índice. Pero él se basará en la estadística y cómo pasa.


### [01:50] Paso Operativo

**Explicación del Relator:**
> Le voy a preguntar a Barri y a alguien que está mirando muchas veces los temas de producción y un programa que demoraba ayer 20 minutos pasa a demorar 5 horas porque la estadística se borró,


### [02:00] Paso Operativo

**Explicación del Relator:**
> porque he usado un índice que ahora no usa, por miles de cosas. Y acá, a ver, la navegación,


### [02:10] Paso Operativo

**Explicación del Relator:**
> por lo menos, está acá, te está diciendo que no hay un índice, ¿no? O sea, él te avisa, ¿no? En cierto punto que vos querés ordenar un programa que no hay un índice, lo cual se puede


### [02:20] Paso Operativo

**Explicación del Relator:**
> hacer, él te dice que no tiene un índice, esto lo que va a determinar es que lo más seguro que la base de datos le cueste más. Se puede a veces generar índices temporales. Hay


### [02:30] Paso Operativo

**Explicación del Relator:**
> otras mecanismos de la base de datos para resolver ciertas cosas, pero acá te está avisando yo. Hay tabla que hay que revisar. Hay tabla FC 011, FCH 012, FCH 021, FCH 015, FCH 016.


### [02:40] Paso Operativo

**Explicación del Relator:**
> Hay ciertas tablas que no podemos hacer de una manera mala. Yendo al caso de este. Esto nos


### [02:50] Paso Operativo

**Explicación del Relator:**
> tiene que llamar la atención ya, ¿no? Todos estos constraints que quedan acá. Y además, además, algo que siempre hacemos,


### [03:00] Paso Operativo

**Explicación del Relator:**
> que siempre hacemos es esto, ¿no? Hacemos un wire y empezamos a pegarle a 11, pam, pam, pam, pam, por cago, ¿me explico?


### [03:10] Paso Operativo

**Explicación del Relator:**
> Entonces, ¿qué es mejor en estos casos? Si yo ya tengo un cliente específico, en este caso, está filtrando por un rango de clientes, pero muchas veces va a ser menos costoso para la base de datos


### [03:20] Paso Operativo

**Explicación del Relator:**
> agarrar, obtener un solo cele por cuenta, por ejemplo. Y después adentro digo, ¿este módulo aplica o no? Y ahí hago un cual de verificación.


### [03:30] Paso Operativo

**Explicación del Relator:**
> Que, por ejemplo, podemos agarrar.


### [03:40] Paso Operativo

**Explicación del Relator:**
> Y después hay un tema de también a veces preguntarnos cuál sería la mejor manera de resolver un problema. En este caso, para entrar en un caso de ejemplo,


### [03:50] Paso Operativo

**Explicación del Relator:**
> No importa lo que hace el programa, pero sí muchas veces preguntamos qué sería la mejor manera.


### [04:00] Paso Operativo

**Explicación del Relator:**
> Es lo que está haciendo acá. Como ustedes verán, se está procesando todos los clientes. Se están procesando todos los algoritmos de estos clientes, cuenta de Reata, que lo tienen acá.


### [04:10] Paso Operativo

**Explicación del Relator:**
> Y como verán, se va a procesar todos los clientes. Y para cada cliente, ¿sí? Para cada cliente va a verificar ciertos módulos y tipos de operaciones. Ciertos productos tienen que verificar.


### [04:20] Paso Operativo

**Explicación del Relator:**
> Y para ciertos productos, hace alguna cosa, ¿no? Entonces, en este caso, vieron que llama después. Por cada saldo que tiene, llama al Buscar Personas. Esto lo está llamando por cada cuenta.


### [04:30] Paso Operativo

**Explicación del Relator:**
> Imagínense que si una cuenta tiene 50 operaciones, esto lo va a llamar 50 veces, cuando estoy parado en la cuenta. Entonces, y después de que estoy parado acá,


### [04:40] Paso Operativo

**Explicación del Relator:**
> por cada una empieza a validar cosas. Como verán, valida el estado de la operación, la calificación, cosas. Entonces, a veces es mejor preguntarme si, bueno,


### [04:50] Paso Operativo

**Explicación del Relator:**
> Pero, pará, yo tengo que procesar todos los clientes, ¿no? Y por cada cliente tengo que verificar ciertas operaciones. Pero, además, tengo ciertas condiciones que son a nivel de


### [05:00] Paso Operativo

**Explicación del Relator:**
> cliente. Por ejemplo, acá ven que accede a buscar una condición para ver si el cliente, no sé, no puede hacer tal cosa. O después acá, si está lista negra o no. O, bueno, o acá si no tiene domicilio o cosas.


### [05:10] Paso Operativo

**Explicación del Relator:**
> Bueno, entonces, algo que se podría hacer es decir, bueno, si yo tengo que procesar todos los clientes, bueno, en vez de partir por la once y por cada cosa,


### [05:20] Paso Operativo

**Explicación del Relator:**
> le pego por la 8 por ejemplo y me filtro los clientes y además como yo estoy parado en la 8 ya puedo verificar o en la concesión que si no es de documento de Uruguay o si el estado de la calificación no es


### [05:30] Paso Operativo

**Explicación del Relator:**
> igual, si tiene la dirección mal, si tiene los datos laborales, ¿me explico? La parte que no aplica en este caso, a veces capaz que puede ser


### [05:40] Paso Operativo - Transacción `F0008`

**Explicación del Relator:**
> en vez de pivotear en la F0008 le pego a la 11 y agarro y verifico o uso el puerte de control, el puerte de control en eso lo que hacen en corte control, hace eso mismo, se crea una variable, que muchas veces,


### [05:50] Paso Operativo

**Explicación del Relator:**
> los capos no confían mucho en el corte control, pero eso es tema de ir confiando, pero lo hacen, eso es lo que va a hacer Google, lo va a hacer GeneXus. Entonces preguntarle, bueno, ¿cuáles


### [06:00] Paso Operativo

**Explicación del Relator:**
> son las condiciones que yo tengo que evaluar? Y de acuerdo a eso, planteo el orden, o el orden, del acceso de la estabilidad que voy a obtener. Entonces va, esto es importante, y obviamente,


### [06:10] Paso Operativo

**Explicación del Relator:**
> cuando yo navego, como verán, se accede por el índice, entonces, y por qué es


### [06:20] Paso Operativo

**Explicación del Relator:**
> importante ver los accesos, no? Acá esta mirada que ustedes decían, la 2.12,


### [06:30] Paso Operativo

**Explicación del Relator:**
> pero por qué es importante, porque a veces nosotros mirando acá, nos damos cuenta de ciertas cosas, por ejemplo, mirá, si yo acá agarro, es funcional, no va a tener


### [06:40] Paso Operativo

**Explicación del Relator:**
> ningún impacto, no filtro progresa, como siempre uno, no pasa nada, pero a nivel de navegación, como es uno, no me importa, no la filtro y bueno, el acceso que me decía


### [06:50] Paso Operativo

**Explicación del Relator:**
> anterior, que me decía que iba a pararse en una empresa cuenta, que iba a usar el lince,


### [07:00] Paso Operativo

**Explicación del Relator:**
> miren lo que me está diciendo, se va con un simple code, que es uno, pero a veces estas


### [07:10] Paso Operativo

**Explicación del Relator:**
> cosa, me explico, yo al navegar acá, si, ah, pará, olvidé el pdf code, hoy lo pongo,


### [07:20] Paso Operativo

**Explicación del Relator:**
> lo explico, pero salta acá, o sea, nos salta de una en esta navegación.


### [07:30] Paso Operativo

**Explicación del Relator:**
> Pero a ver, es un tema de ir validando o verificando, bueno, cómo debería ser el programa para evidenciar los botones, evitar por lo menos los accesos a esa


### [07:40] Paso Operativo

**Explicación del Relator:**
> estaba empezada y además las condiciones que, si a mí hay condiciones que ya de salida,


### [07:50] Paso Operativo

**Explicación del Relator:**
> las trato de hacer lo antes posible y las que me cueste menos. Si yo estoy mirando a una persona asistiendo en esta línea, lo hago primero, porque ya eso no va a determinar ninguna condición de


### [08:00] Paso Operativo

**Explicación del Relator:**
> salida. Digo, hay que ver la característica del programa, qué es lo que va a procesar nosotros, en un plano, hacernos esa pregunta. Si yo voy a procesar préstamo, ¿por qué capaz que


### [08:10] Paso Operativo

**Explicación del Relator:**
> voy a ir a la FCS en la 11, en la parte 2 voy a ir a la FCS en la 10 si yo tengo la presa muy bien. Es una tabla mucho más chica que la 11. Entonces hacernos esa pregunta y ver, y eso muchas veces capaz que nosotros no lo sabemos, pero a veces lo puede funcionar la contraparte,


### [08:20] Paso Operativo

**Explicación del Relator:**
> o a veces otro compañero que conoce sobre el tema, o si tengo que hablar esta condición, ¿qué tabla puedo, siempre esta?


### [08:30] Paso Operativo

**Explicación del Relator:**
> Capaz que a las 10, en vez de ir a las 11. ¿De acuerdo? O sea, tenemos que pensar en eso, en ver cuáles son las tablas que tengo que tratar de evitar ir, por lo menos.


### [08:40] Paso Operativo
