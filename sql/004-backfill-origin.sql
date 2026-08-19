-- Procedencia explicita para los chunks de gx_docs.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/004-backfill-origin.sql
--
-- EL PROBLEMA QUE RESUELVE. La coleccion gx_docs mezcla TRES fuentes distintas y hasta
-- ahora solo se distinguian mirando la url de cada hit a ojo. Medido sobre el corpus
-- real antes de esta migracion:
--
--     sac       28.155 chunks (56%)  12.756 articulos   <- tickets de soporte
--     gx_wiki   19.783 chunks (40%)   5.320 articulos   <- wiki de GeneXus
--     wwp        2.125 chunks ( 4%)     777 articulos   <- wiki de WorkWithPlus
--
-- O sea: MAS DE LA MITAD del corpus son tickets de incidentes, y WorkWithPlus —que es
-- OTRO PRODUCTO (DVelop), distinto del pattern nativo "Work With for Web"— es el 4%.
-- Una consulta sobre "el pattern" devuelve los dos entremezclados, y el riesgo no es
-- quedarse sin respuesta: es tomar la del producto equivocado.
--
-- La descripcion de search_docs ya pedia "FILTRA POR PROCEDENCIA, NO POR PRESENCIA",
-- pero eso es una exhortacion al agente. Con meta.origin pasa a ser un filtro
-- mecanico: filters={"origin":["wwp"]}.
--
-- NO RE-EMBEDDEA NADA. Es un UPDATE de la columna jsonb: no toca `content`, ni
-- `embedding`, ni `content_hash`. La idempotencia de la ingesta queda intacta, asi que
-- una reingesta posterior del mismo articulo sigue dando "skipped".
--
-- IDEMPOTENTE: el WHERE excluye lo que ya tiene la clave, asi que re-aplicar el archivo
-- no reescribe filas ni infla la tabla con tuplas muertas.

\set ON_ERROR_STOP on

-- El orden de los CASE importa: docs.genexus.com y wiki.genexus.com son el MISMO
-- corpus con dos hostnames (scrape_wiki los unifica al normalizar el source_key), asi
-- que los dos van a 'gx_wiki'. sac.genexus.com se evalua primero porque tambien
-- contiene 'genexus.com'.
UPDATE chunks
   SET meta = meta || jsonb_build_object('origin',
       CASE
         WHEN url LIKE '%sac.genexus.com%'       THEN 'sac'
         WHEN url LIKE '%docs.workwithplus.com%' THEN 'wwp'
         WHEN url LIKE '%wiki.genexus.com%'      THEN 'gx_wiki'
         WHEN url LIKE '%docs.genexus.com%'      THEN 'gx_wiki'
         ELSE 'unknown'
       END)
 WHERE collection = 'gx_docs'
   AND NOT (meta ? 'origin');

-- bt_docs es una sola fuente (manual funcional Bantotal), pero se marca igual: un
-- filtro por origin no puede devolver vacio en una coleccion solo porque nadie la
-- clasifico. La ausencia de la clave y "no matcheo" se leen igual desde afuera.
UPDATE chunks
   SET meta = meta || jsonb_build_object('origin', 'bt_manual')
 WHERE collection = 'bt_docs'
   AND NOT (meta ? 'origin');

-- Por el MISMO argumento, gx_syntax y bt_apis tambien. Se marcan con el nombre de la
-- coleccion porque son catalogos, no scrapes con procedencia web: lo que importa es
-- que NINGUNA fila quede sin la clave, para que un `filters={"origin":...}` que no
-- matchea signifique siempre "ese origen no esta" y nunca "esa coleccion no se
-- clasifico". Son dos ausencias distintas que desde afuera se leen igual.
UPDATE chunks
   SET meta = meta || jsonb_build_object('origin', 'gx_catalog')
 WHERE collection = 'gx_syntax'
   AND NOT (meta ? 'origin');

UPDATE chunks
   SET meta = meta || jsonb_build_object('origin', 'bt_catalog')
 WHERE collection = 'bt_apis'
   AND NOT (meta ? 'origin');

-- El indice de origin ya lo crea el 003; aca solo se puebla el dato.
COMMENT ON COLUMN chunks.meta IS
    'Metadatos por coleccion (bolsa abierta, shape por coleccion). '
    'gx_kb_objects: obj_name, obj_type, module, chunk_kind, kb_name, kb_hash, '
    'related[]. gx_docs: origin (gx_wiki|sac|wwp). bt_docs: origin (bt_manual). '
    'Ver sql/003-chunk-meta.sql y sql/004-backfill-origin.sql.';
