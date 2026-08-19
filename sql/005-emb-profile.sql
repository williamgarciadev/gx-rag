-- Perfil de geometria del embedding, por chunk.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/005-emb-profile.sql
--
-- POR QUE HACE FALTA MARCARLO. `intfloat/multilingual-e5-large` fue entrenado con
-- prefijos de rol (`query: ` / `passage: `) y este corpus se indexo sin ellos. Eso NO
-- estaba roto: quedo simetricamente sin prefijo, o sea internamente consistente.
--
-- Pero cambiar de geometria NO se puede hacer a medias. Prefijar solo la consulta
-- contra un corpus sin prefijar es un DESAJUSTE, y un desajuste es peor que la
-- ausencia consistente. Y re-embeddear es por lotes, asi que durante el proceso la
-- coleccion tiene parte en una geometria y parte en la otra: sin una marca por fila,
-- una corrida interrumpida deja el corpus devolviendo basura y NADA lo indica.
--
-- La marca va en una COLUMNA y no en `meta` a proposito: `meta` esta documentada como
-- bolsa POR COLECCION con shape por coleccion, y la geometria del embedding es una
-- propiedad TRANSVERSAL de la tabla que hay que poder consultar y restringir
-- globalmente. Mezclarlas obligaria al CHECK y a los indices del 003 a convivir con
-- una clave de otra naturaleza.
--
-- Y el estado de reanudacion vive en la BASE, no en un archivo de checkpoint: la
-- columna se actualiza en la MISMA transaccion que el UPDATE del embedding, asi que
-- no puede desincronizarse del estado real. Un archivo si puede (se escribio el
-- archivo y fallo el commit, o al reves).

\set ON_ERROR_STOP on

-- Default CONSTANTE => PostgreSQL 11+ NO reescribe la tabla. 'raw' es la verdad para
-- todo lo ya indexado.
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS emb_profile text NOT NULL DEFAULT 'raw';

-- Batching por keyset sobre la PK durante el re-embed, y el chequeo de completitud
-- despues. Sin este indice el barrido es un seq scan por lote.
CREATE INDEX IF NOT EXISTS chunks_emb_profile
    ON chunks (collection, emb_profile, chunk_id);

COMMENT ON COLUMN chunks.emb_profile IS
    'Geometria del embedding de esta fila: raw (sin prefijos e5) | e5v1 (query:/passage:). '
    'Una coleccion con MAS DE UN valor esta a medio migrar y sus resultados densos no '
    'son comparables entre si. Ver sql/005-emb-profile.sql y scripts/reembed.py.';
