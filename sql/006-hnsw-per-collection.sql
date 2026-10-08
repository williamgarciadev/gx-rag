-- Un HNSW por colección en vez de uno para toda la tabla.
--
--   psql -U gxrag -d gxrag -h 127.0.0.1 -p 5433 -f sql/006-hnsw-per-collection.sql
--
-- POR QUÉ. El 001 crea un único HNSW sobre `chunks`, y la búsqueda densa filtra
-- `WHERE collection = ...` DESPUÉS de recorrer el grafo (post-filtrado; el
-- iterative_scan de pg_retriever.py evita las cero filas, pero no cambia QUÉ grafo
-- se recorre). Con un grafo compartido, una colección que crece le cambia a las demás
-- qué vecinos aproximados encuentran.
--
-- Medido el 2026-10-08, tras ingerir 21.665 chunks en bt_docs (1.122 -> 22.787), sobre
-- los 12 casos de eval/gold.jsonl con el mismo código:
--
--     HNSW global          R@5 0,583  R@10 0,750  MRR 0,399
--     búsqueda exacta      R@5 0,833  R@10 0,833  MRR 0,443
--
-- Y el código del 2026-10-06 rankeó gx_docs distinto sin que gx_docs cambiara.
--
-- Con un índice PARCIAL por colección, el grafo de cada una contiene solo sus filas:
-- crecer bt_docs ya no toca a gx_docs. El planner lo puede usar porque el CTE denso
-- compara `collection = %(collection)s` y se ejecuta sin preparar (plan con el valor
-- real), lo que le permite probar el predicado del índice.
--
-- La lista es la de KNOWN_COLLECTIONS (src/server.py); tests/test_hnsw_per_collection.py
-- falla si una colección conocida no tiene su índice aquí.
--
-- Sin CONCURRENTLY a propósito: tests/conftest.py aplica todos los sql/0*.sql en una
-- sola transacción, donde CONCURRENTLY no se permite. El build bloquea ESCRITURAS (no
-- lecturas) mientras dura; no correr esto con una ingesta en curso.

\set ON_ERROR_STOP on

-- El build de HNSW es mucho más rápido si el grafo entra en memoria.
SET maintenance_work_mem = '1GB';

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_gx_docs
    ON chunks USING hnsw (embedding vector_cosine_ops) WHERE collection = 'gx_docs';
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_bt_docs
    ON chunks USING hnsw (embedding vector_cosine_ops) WHERE collection = 'bt_docs';
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_gx_syntax
    ON chunks USING hnsw (embedding vector_cosine_ops) WHERE collection = 'gx_syntax';
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_bt_apis
    ON chunks USING hnsw (embedding vector_cosine_ops) WHERE collection = 'bt_apis';
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_gx_kb_objects
    ON chunks USING hnsw (embedding vector_cosine_ops) WHERE collection = 'gx_kb_objects';

-- Después de crear los parciales, nunca antes: no hay ventana sin índice. Si quedara,
-- el planner podría seguir eligiéndolo y volvería el grafo compartido.
DROP INDEX IF EXISTS chunks_embedding_hnsw;
