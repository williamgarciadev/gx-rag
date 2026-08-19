-- Schema del vector store sobre PostgreSQL + pgvector.
-- Reemplaza ChromaDB (dense) y el indice BM25 en memoria (lexico) por un solo motor.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/001-chunks-schema.sql
--
-- Verificado contra el corpus real: 50.056 chunks en gx_docs, 26 en gx_syntax,
-- embeddings de intfloat/multilingual-e5-large -> 1024 dims, norma L2 = 1.0.

\set ON_ERROR_STOP on

-- La configuracion lexica se crea ACA, antes de la tabla, porque la columna
-- generada `tsv` la necesita resuelta en el momento del CREATE TABLE.
--
-- Vivia solo en el 002, y eso dejaba una trampa: el comando del header de arriba
-- se presenta como autosuficiente, asi que un install que corriera solo este
-- archivo nacia con `tsv` en 'simple' mientras pg_retriever.py consulta con
-- 'es_simple'. Configs distintas = CERO filas en la mitad lexica del RRF, sin
-- error y sin aviso: la busqueda hibrida degeneraba a densa pura y parecia andar.
--
-- El 002 sigue existiendo para las bases ya creadas. Sus dos guards
-- (pg_ts_config y `expr LIKE '%es_simple%'`) detectan que no hay nada que migrar
-- y lo dejan como no-op, asi que aplicar 001+002 en orden sigue siendo correcto
-- y NO reescribe la tabla.
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

CREATE TABLE IF NOT EXISTS chunks (
    -- Id del chunk tal como lo genera indexer.py: "{article_id}_c{i}".
    -- Es texto libre (suele ser una URL), no un entero.
    chunk_id     text PRIMARY KEY,

    -- Reemplaza a las colecciones de ChromaDB: gx_docs, bt_docs, gx_syntax, ...
    collection   text    NOT NULL,

    -- Agrupador de los chunks de un mismo articulo (metadata "id" en Chroma).
    article_id   text    NOT NULL,
    chunk_index  integer NOT NULL,

    title        text NOT NULL DEFAULT '',
    content      text NOT NULL,
    source       text NOT NULL DEFAULT '',
    url          text NOT NULL DEFAULT '',
    genre        text NOT NULL DEFAULT '',

    -- Permite la reindexacion idempotente que hoy hace reindex_if_changed().
    content_hash text NOT NULL DEFAULT '',

    embedding    vector(1024) NOT NULL,

    -- Configuracion 'es_simple' = simple + unaccent: sin stemming ni stopwords,
    -- deliberadamente, pero insensible a tildes (ver el bloque de arriba y el 002).
    -- El BM25 que reemplazamos tokeniza con re.findall(r"\w+", text.lower()),
    -- o sea matching literal. 'spanish' aplicaria stemming que el ranking actual
    -- no tiene, y ademas buena parte del corpus (WorkWithPlus) esta en ingles.
    -- Arrancar fiel al comportamiento actual; evaluar 'spanish' con metricas despues.
    --
    -- OJO, dos deltas medidos contra el BM25 que se reemplaza (no son bugs):
    --  1) Aca se indexa title || content. El BM25 indexaba solo `documents`, que
    --     en indexer.py son los chunks SIN el titulo. Sumar el titulo es una
    --     mejora deliberada: es la senal lexica mas fuerte del corpus. No se
    --     revierte para "parecerse" a un motor que se va a dar de baja.
    --  2) ts_rank_cd (cover density) != BM25Okapi (IDF + saturacion k1 +
    --     normalizacion por longitud b). Son formulas distintas.
    -- Consecuencia medida: la senal lexica solapa ~30% @5 con el baseline. La
    -- densa empata exacto o mejora (nunca pierde). Cual de las dos formulas
    -- ordena MEJOR es una pregunta abierta: no hay set de relevancia etiquetado.
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('es_simple', coalesce(title, '') || ' ' || coalesce(content, ''))
    ) STORED
);

-- Busqueda densa. Los vectores estan normalizados, asi que coseno e inner product
-- ordenan igual; se usa coseno por ser correcto aunque algun vector no lo este.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Busqueda lexica: esto es lo que reemplaza al BM25 en memoria.
CREATE INDEX IF NOT EXISTS chunks_tsv_gin
    ON chunks USING gin (tsv);

-- get_article(): todos los chunks de un articulo, en orden.
CREATE INDEX IF NOT EXISTS chunks_article
    ON chunks (collection, article_id, chunk_index);

-- Filtro por coleccion, presente en toda query.
CREATE INDEX IF NOT EXISTS chunks_collection
    ON chunks (collection);

COMMENT ON TABLE  chunks     IS 'Chunks del corpus con embedding denso y vector lexico.';
COMMENT ON COLUMN chunks.tsv IS 'Config es_simple = simple + unaccent (sin stemming ni stopwords, insensible a tildes). Indexa title||content; el BM25 que reemplaza usaba solo content y otra formula de ranking.';
