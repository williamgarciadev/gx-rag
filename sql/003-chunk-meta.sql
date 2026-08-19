-- Metadatos por chunk, para colecciones cuyo "documento" no es un articulo de wiki
-- sino un OBJETO de una Knowledge Base GeneXus.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/003-chunk-meta.sql
--
-- POR QUE UNA COLUMNA jsonb Y NO 8 COLUMNAS NUEVAS:
-- `chunks` es UNA tabla para TODAS las colecciones, asi que 8 columnas que solo usa
-- la coleccion de KB las verian tambien gx_docs, bt_docs, gx_syntax y bt_apis. Pero
-- el argumento decisivo no es ese: es _UPSERT_SQL, que tiene 11 placeholders
-- POSICIONALES y al que _write_chunks alimenta armando tuplas por posicion.
-- Sumar 8 posiciones a esa cadena es la forma mas barata de terminar escribiendo
-- `module` donde va `source` sin que nada falle ni ningun test lo note.
--
-- NO se toca `tsv`. El nombre y el tipo del objeto van en `title`, que la columna
-- generada ya indexa (001). Meter `meta` adentro obligaria a reescribir las ~51k
-- filas del corpus existente por una senal que ya esta ahi.
--
-- SIN CREATE INDEX CONCURRENTLY, a proposito: tests/conftest.py concatena TODOS los
-- sql/0*.sql y los ejecuta en UNA sentencia multi-statement, o sea dentro de un
-- bloque de transaccion implicito, y CONCURRENTLY esta prohibido ahi. Con
-- CONCURRENTLY en este archivo se cae el SETUP del schema de prueba, o sea todos
-- los tests de integracion a la vez, y no por un assert. La tabla es de ~10^5
-- filas: el lock de escritura dura segundos y la ingesta es manual.

\set ON_ERROR_STOP on

-- Default CONSTANTE => PostgreSQL 11+ NO reescribe la tabla (fast path). Contraste
-- deliberado con el 002, que si reescribio las 51k filas al swapear `tsv`.
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Los chunks de una KB TIENEN que traer identidad de objeto y de KB.
--
-- Sin esto, un chunk con meta='{}' es invisible a todo filtro, y "el filtro no
-- matcheo nada" se lee igual que "la KB no tiene transacciones": una ausencia que
-- nadie verifico. `kb_name` es ademas lo que permite tener MAS DE UNA KB en la
-- misma coleccion sin mezclarlas -- y mezclarlas es peor que no tener RAG, porque
-- el agente escribiria codigo con nombres de objetos que no existen en la KB activa.
--
-- El CHECK es la barrera en el MOTOR, no en la buena voluntad del codigo: hace que
-- una ingesta mal ruteada a esta coleccion falle RUIDOSAMENTE en vez de dejar filas
-- no filtrables.
ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_kb_meta_required;
ALTER TABLE chunks ADD CONSTRAINT chunks_kb_meta_required CHECK (
    collection <> 'gx_kb_objects'
    OR (meta ? 'obj_name' AND meta ? 'obj_type'
        AND meta ? 'chunk_kind' AND meta ? 'kb_name')
);

-- Filtros del hot path: igualdad / IN sobre texto escalar.
--
-- btree de EXPRESION, no GIN: la forma que consulta el retriever es
-- `meta->>'k' = ANY(%(param)s::text[])`, y un GIN de jsonb_ops NO sirve esa forma
-- (sirve @>, ?, ?| sobre el nivel superior). Poner solo un GIN y creer que indexa
-- el filtro es el error clasico aca.
--
-- La coleccion va PRIMERA en cada indice porque esta presente en TODA query (001).
CREATE INDEX IF NOT EXISTS chunks_meta_kb_name
    ON chunks (collection, (meta->>'kb_name'));
CREATE INDEX IF NOT EXISTS chunks_meta_obj_type
    ON chunks (collection, (meta->>'obj_type'));
CREATE INDEX IF NOT EXISTS chunks_meta_chunk_kind
    ON chunks (collection, (meta->>'chunk_kind'));
CREATE INDEX IF NOT EXISTS chunks_meta_module
    ON chunks (collection, (meta->>'module'));
-- Lookup por nombre de objeto: es un punto, no un ranking.
CREATE INDEX IF NOT EXISTS chunks_meta_obj_name
    ON chunks (collection, (meta->>'obj_name'));
-- Procedencia dentro de una coleccion mezclada. gx_docs junta wiki GeneXus, tickets
-- SAC y wiki de WorkWithPlus, y hoy solo se distinguen mirando la url a ojo.
CREATE INDEX IF NOT EXISTS chunks_meta_origin
    ON chunks (collection, (meta->>'origin'));

-- `related` es un array de strings y la forma que lo consulta es `?|` (existe
-- alguna de estas claves). ESA si necesita GIN, y sobre la SUBEXPRESION: un GIN
-- sobre `meta` entero no sirve `meta->'related' ?| ...`.
CREATE INDEX IF NOT EXISTS chunks_meta_related_gin
    ON chunks USING gin ((meta->'related'));

COMMENT ON COLUMN chunks.meta IS
    'Metadatos por coleccion (bolsa abierta, shape por coleccion). '
    'gx_kb_objects: obj_name, obj_type, module, chunk_kind, kb_name, kb_hash, '
    'related[]. gx_docs: origin (gx_wiki|sac|wwp). Vacio ({}) donde no aplica. '
    'Ver sql/003-chunk-meta.sql.';
