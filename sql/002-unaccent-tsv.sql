-- Normalizacion de diacriticos en el vector lexico.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/002-unaccent-tsv.sql
--
-- EL PROBLEMA, medido sobre bt_docs (corpus funcional Bantotal, en espanol):
--
--     to_tsquery('simple', 'autorizacion')  ->  15 hits
--     to_tsquery('simple', 'autorización')  ->  96 hits
--
-- La gente escribe sin tildes, asi que la mitad lexica del RRF perdia el 84% de
-- los chunks relevantes y la busqueda hibrida degeneraba a densa pura. No se
-- notaba mientras el corpus era solo gx_docs (wiki GeneXus, en ingles).
--
-- POR QUE 'es_simple' Y NO 'spanish': el 001 descarto 'spanish' por el stemming
-- (linea 34-38) y dejo anotado "evaluar con metricas despues". Esto es ese
-- despues, y respeta la decision original: es_simple = simple + unaccent, o sea
-- SOLO se agrega la normalizacion de diacriticos. Sigue sin stemming y sigue sin
-- stopwords, asi que el corpus en ingles no cambia de comportamiento.
--
-- Verificado: los identificadores Bantotal siguen matcheando exacto con unaccent
-- ('fsd011' -> 59 hits antes y despues). Es la senal lexica que mas vale para
-- reportes, y no se pierde.
--
-- COSTO: reescribe la columna generada de toda la tabla (~51k filas) y regenera
-- el GIN. Sin re-embeddear: los vectores densos no se tocan.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS unaccent;

-- No existe CREATE TEXT SEARCH CONFIGURATION IF NOT EXISTS, de ahi el DO block.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'es_simple') THEN
        CREATE TEXT SEARCH CONFIGURATION es_simple ( COPY = simple );
        -- unaccent ANTES de simple: normaliza el token y despues lo emite tal
        -- cual, sin stemming.
        ALTER TEXT SEARCH CONFIGURATION es_simple
            ALTER MAPPING FOR asciiword, word, numword, asciihword, hword,
                              numhword, hword_asciipart, hword_part, hword_numpart
            WITH unaccent, simple;
    END IF;
END
$$;

-- El swap de la columna generada, solo si hace falta.
--
-- unaccent() es STABLE, no IMMUTABLE, asi que NO puede ir suelta dentro de una
-- columna generada. Por eso hace falta la configuracion: to_tsvector('es_simple',
-- ...) es la forma de 2 argumentos, que si es IMMUTABLE. El 001 ya prueba que esa
-- forma se acepta (linea 50-52).
--
-- El guard evita un rewrite de 51k filas al re-aplicar el archivo. Se compara la
-- expresion de la columna porque es la unica fuente de verdad: PostgreSQL guarda
-- la config resuelta a OID, no el nombre.
DO $$
DECLARE
    expr text;
BEGIN
    SELECT pg_get_expr(d.adbin, d.adrelid)
      INTO expr
      FROM pg_attrdef d
      JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
     WHERE d.adrelid = 'chunks'::regclass AND a.attname = 'tsv';

    IF expr IS NULL OR expr LIKE '%es_simple%' THEN
        RAISE NOTICE 'chunks.tsv ya usa es_simple (o no existe): nada que hacer.';
        RETURN;
    END IF;

    -- DROP COLUMN se lleva el GIN en cascada; se recrea abajo con el mismo nombre.
    ALTER TABLE chunks DROP COLUMN tsv;
    ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('es_simple', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED;
END
$$;

CREATE INDEX IF NOT EXISTS chunks_tsv_gin
    ON chunks USING gin (tsv);

COMMENT ON COLUMN chunks.tsv IS
    'Config es_simple = simple + unaccent: sin stemming ni stopwords, pero '
    'insensible a tildes. Indexa title||content. Ver sql/002-unaccent-tsv.sql.';
