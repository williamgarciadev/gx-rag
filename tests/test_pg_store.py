"""Tests de integración de la capa Postgres (pgvector + tsvector).

Corren contra un schema descartable, no contra fakes. La razón es concreta: la
lógica que reemplazó a ChromaDB vive en SQL — RRF resuelto en una query,
reensamblado con string_agg, dedup por content_hash en una transacción. Un doble
de prueba de la conexión verificaría que el doble devuelve lo que se le
programó, no que la consulta hace lo que dice.

Se saltean solos si no hay PostgreSQL disponible (ver fixture pg_dsn).
"""

import pytest

pytestmark = pytest.mark.integration


# ─── pg_collections ───────────────────────────────────────────────────────────


def test_get_document_returns_the_chunk_with_its_provenance(pg_store):
    from src.vectorstore.pg_collections import get_document

    pg_store.insert("art_c0", "contenido del chunk", title="Un título")

    doc = get_document("art_c0")

    assert doc is not None
    assert doc["id"] == "art_c0"
    assert doc["content"] == "contenido del chunk"
    assert doc["title"] == "Un título"
    assert doc["collection"] == "gx_docs"


def test_get_document_isolates_collections(pg_store):
    """Mismo chunk_id no alcanza: la colección es parte de la búsqueda."""
    from src.vectorstore.pg_collections import get_document

    pg_store.insert("x_c0", "de gx_docs", collection="gx_docs")

    assert get_document("x_c0", collection="gx_docs") is not None
    assert get_document("x_c0", collection="bt_docs") is None


def test_get_article_reassembles_chunks_in_order(pg_store):
    """El orden lo garantiza el ORDER BY dentro de string_agg, no el motor.

    Se insertan a propósito desordenados: si el ORDER BY se cayera, el artículo
    saldría mal armado y este test es el único que lo notaría.
    """
    from src.vectorstore.pg_collections import get_article

    pg_store.insert("a_c2", "tercero", article_id="a", chunk_index=2)
    pg_store.insert("a_c0", "primero", article_id="a", chunk_index=0)
    pg_store.insert("a_c1", "segundo", article_id="a", chunk_index=1)

    art = get_article("a")

    assert art is not None
    assert art["chunks"] == 3
    assert art["content"] == "primero\n\nsegundo\n\ntercero"


def test_get_article_returns_none_when_absent(pg_store):
    """La agregación siempre devuelve una fila; sin chunks, count(*) es 0."""
    from src.vectorstore.pg_collections import get_article

    assert get_article("no-existe") is None


def test_collection_counts_groups_and_orders_by_size(pg_store):
    from src.vectorstore.pg_collections import collection_counts, list_collections

    for i in range(3):
        pg_store.insert(f"d{i}", "x", collection="gx_docs", seed=i)
    pg_store.insert("s0", "y", collection="gx_syntax")

    assert collection_counts() == [("gx_docs", 3), ("gx_syntax", 1)]
    assert list_collections() == ["gx_docs", "gx_syntax"]


def test_collection_exists_only_with_content(pg_store):
    from src.vectorstore.pg_collections import collection_exists

    pg_store.insert("c0", "algo", collection="gx_docs")

    assert collection_exists("gx_docs") is True
    assert collection_exists("bt_docs") is False


# ─── pg_retriever ─────────────────────────────────────────────────────────────


def test_lexical_signal_finds_a_document_the_vector_missed(pg_store, monkeypatch):
    """El valor del híbrido: lo léxico rescata lo que lo denso no vio.

    El vector de la query se fija lejos del de 'préstamo', así que solo el
    tsvector puede traerlo.
    """
    from src.vectorstore import pg_retriever

    pg_store.insert("a_c0", "genexus transaction procedure", seed=1)
    pg_store.insert("b_c0", "modulo de prestamo bancario", seed=500)
    pg_store.insert("c_c0", "vector search index", seed=2)

    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _unit(1))

    ids = [r["doc_id"] for r in pg_retriever.hybrid_search("prestamo", top_k=3)]

    assert "b_c0" in ids


def test_dense_signal_ranks_the_nearest_vector_first(pg_store, monkeypatch):
    from src.vectorstore import pg_retriever

    pg_store.insert("lejos_c0", "texto sin relacion", seed=700)
    pg_store.insert("cerca_c0", "texto sin relacion", seed=42)

    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _unit(42))

    resultados = pg_retriever.hybrid_search("cualquier cosa", top_k=2)

    assert resultados[0]["doc_id"] == "cerca_c0"


def test_search_is_scoped_to_its_collection(pg_store, monkeypatch):
    from src.vectorstore import pg_retriever

    pg_store.insert("gx_c0", "comando for each", collection="gx_docs", seed=3)
    pg_store.insert("bt_c0", "comando for each", collection="bt_docs", seed=3)

    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _unit(3))

    ids = [r["doc_id"] for r in pg_retriever.hybrid_search("for each", collection="bt_docs")]

    assert ids == ["bt_c0"]


def test_query_with_no_usable_terms_returns_empty(pg_store, monkeypatch):
    """Sin tokens no se arma tsquery, y se corta antes de tocar la base."""
    from src.vectorstore import pg_retriever

    pg_store.insert("a_c0", "algo", seed=1)
    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _unit(1))

    assert pg_retriever.hybrid_search("!!! ???") == []


def test_result_carries_the_provenance_an_agent_needs_to_cite(pg_store, monkeypatch):
    from src.vectorstore import pg_retriever

    pg_store.insert("art_c0", "for each command", article_id="art", title="For Each", seed=7)
    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _unit(7))

    r = pg_retriever.hybrid_search("for each")[0]

    assert set(r) >= {"doc_id", "id", "title", "content", "source", "url",
                      "content_hash", "score"}
    assert r["doc_id"] == "art_c0"   # el chunk, que es lo que acepta get_document
    assert r["id"] == "art"          # el artículo, para agrupar y citar


# ─── pg_indexer ───────────────────────────────────────────────────────────────


def test_reindex_is_transactional_end_to_end(pg_store, monkeypatch):
    """Los tres estados del dedup, contra la base real.

    test_bulk_loader cubre la decisión con un pool falso; acá se comprueba que
    el SQL efectivamente borra los chunks viejos y deja los nuevos.
    """
    from src.vectorstore import pg_indexer

    monkeypatch.setattr(
        pg_indexer.emb, "embed_texts", lambda textos: [_unit(9) for _ in textos]
    )
    monkeypatch.setattr(pg_indexer, "_vector_literal", lambda v: pg_store.vector(9))

    args = ("art", "T", "contenido original", "src", "guide", "url")

    assert pg_indexer.reindex_if_changed(*args, "H1") == "indexed"
    assert pg_indexer.reindex_if_changed(*args, "H1") == "skipped"

    estado = pg_indexer.reindex_if_changed(
        "art", "T", "contenido nuevo mas largo", "src", "guide", "url", "H2"
    )
    assert estado == "reindexed"

    from src.vectorstore.pg_collections import get_article

    art = get_article("art")
    assert "contenido nuevo" in art["content"]
    assert "original" not in art["content"]  # los viejos se fueron


# ─── helpers ──────────────────────────────────────────────────────────────────


def _unit(i: int):
    """Mismo vector que siembra la fixture, como lista de floats."""
    v = [0.0] * 1024
    v[i % 1024] = 1.0
    return v
