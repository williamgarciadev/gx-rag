"""Contract tests for the READ tools — the surface an external agent reasons with.

The premise of this repo is that a tool description and a response text ARE the
consuming agent's prompt, so a description that describes a store we deleted, or
a response that reads as an absence when it is really an outage, is a defect of
the same kind as a wrong return value. These tests lock down the properties that
keep an agent from concluding something the system cannot back.

No network, no embeddings, no Postgres: the store is monkeypatched.
"""

import asyncio

import pytest

from src import server as srv

pytestmark = pytest.mark.unit


def _read_tool(name):
    return next(t for t in srv.READ_TOOLS if t.name == name)


def _call(name, **arguments) -> str:
    """Invoke a tool through the real dispatcher and return its text.

    asyncio.run instead of pytest-asyncio on purpose: the suite has no async
    plugin and one dispatcher call does not justify adding a dependency.
    """
    content = asyncio.run(srv.dispatch_tool(name, arguments))
    return "\n".join(c.text for c in content)


# ─────────────────────────────────────────────────────────────────────────────
# Descriptions must describe the system that exists
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", srv.READ_TOOLS, ids=lambda t: t.name)
def test_no_description_names_a_store_or_engine_we_removed(tool):
    """ChromaDB and rank_bm25 were deleted in c4fb609.

    An agent told the lexical half is BM25 reasons about IDF and length
    normalization that no longer exist, and one told doc_id is "an internal
    ChromaDB id" cannot tell what to pass. Stale text in the prompt is a lie
    with the same blast radius as a stale return value.
    """
    text = (tool.description or "").lower()
    assert "chromadb" not in text
    assert "bm25" not in text


@pytest.mark.parametrize("tool", srv.READ_TOOLS, ids=lambda t: t.name)
def test_no_description_hardcodes_a_corpus_size(tool):
    """A count in a description is a claim about ONE deployment.

    Measured 2026-08-11: the VPS serves a ChromaDB corpus with no `bt_docs` at
    all, while the dev box has ~50k chunks in Postgres. A description saying
    "gx_docs ~50.000 chunks; bt_docs ~1.100" ships to both and is false in one of
    them — the same class of lie this file exists to prevent, and it slipped in
    while fixing the others. Sizes are runtime facts: `collection_counts()`
    already reports them in the empty response, where they are true when read.
    """
    import re

    thousands = re.findall(r"\d+[.,]\d{3}\b", tool.description or "")
    assert not thousands, f"{tool.name} hardcodea un conteo: {thousands}"


def test_search_docs_names_the_actual_lexical_engine():
    text = _read_tool("search_docs").description
    assert "tsvector" in text
    # es_simple is what makes the lexical half accent-insensitive (sql/002).
    assert "es_simple" in text
    assert "RRF" in text


def test_get_document_does_not_promise_a_whole_document():
    """It returns ONE chunk (~800 chars), keyed by chunk_id.

    Promising "the complete content of a document" makes an agent read a
    fragment as the entire article and conclude the rest does not exist.
    """
    tool = _read_tool("get_document")
    text = tool.description.lower()

    assert "fragmento" in text or "chunk" in text
    assert "get_article" in text, "must point at the tool that DOES rebuild the article"
    assert "completo" not in tool.input_schema["properties"]["doc_id"]["description"].lower()


def test_verify_syntax_declares_that_an_older_version_gets_a_hint():
    """`version` SÍ cambia el veredicto de verify_syntax: por debajo de la versión
    de las fuentes del catálogo (GX18), un acierto sale PISTA y no VÁLIDO.

    El 2026-10-05 la descripción decía "NO filtra ni discrimina" mientras el código
    devolvía INVÁLIDO a los 706 métodos de gxtypes por gx16 (version_since tenía la
    versión del install). Arreglados los datos, lo que queda es PISTA, y un agente
    que no sepa que existe ese veredicto lo lee como un VÁLIDO débil o un error.
    """
    tool = _read_tool("verify_syntax")
    described = tool.input_schema["properties"]["version"]["description"]

    assert "PISTA" in described
    assert "PISTA" in tool.description, "the verdict list must include PISTA"


def test_list_methods_declares_that_version_is_ignored():
    """list_methods no filtra por versión (gx_repository.list_methods la ignora)."""
    described = _read_tool("list_methods").input_schema["properties"]["version"]["description"]

    assert "ignora" in described.lower()


def test_verify_syntax_does_not_claim_a_tiny_catalog():
    """Medido 2026-10-05: 765 entradas. "Decenas de entradas" ya no es cierto, y
    un agente que cree el catálogo diminuto descarta un VÁLIDO como casualidad."""
    assert "decenas de entradas" not in _read_tool("verify_syntax").description


def test_list_methods_does_not_offer_object_types_the_catalog_lacks():
    """"Transaction" and "Procedure" have 0 rows each — they are not categories.

    Handing an agent an example that guarantees an empty answer is worse than
    handing it none: the empty answer then reads as "that object has no methods".
    Naming them as a TRAP is fine; offering them as valid values is not.
    """
    tool = _read_tool("list_methods")
    declared = tool.input_schema["properties"]["object_type"]["description"]

    assert "Transaction" not in declared
    assert "Procedure" not in declared
    # Medido 2026-10-05: 96 object_type. Los de la wiki son categorías; los de
    # gxtypes son tipos de dato del lenguaje (DateTime 33, Collection 16…). Si la
    # descripción solo nombra las categorías, el agente nunca pide un tipo de dato.
    for real in ("Method", "Command", "DateTime", "Collection"):
        assert real in declared, real
    # If the prose mentions them at all, it must be to warn.
    if "Transaction" in tool.description:
        assert "vacío" in tool.description


def test_verify_syntax_declares_that_the_bt_api_catalog_is_empty():
    """bt_apis has 0 rows, so language="bt_api" can only ever be NO_VERIFICABLE."""
    text = _read_tool("verify_syntax").description.lower()
    assert "bt_apis" in text
    assert "vac" in text  # vacío / vacía


# ─────────────────────────────────────────────────────────────────────────────
# An outage must not be reported as an absence
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def broken_store(monkeypatch):
    """Make every store access blow up, the way a Postgres outage does."""
    from src.vectorstore import pg_collections

    def _explode():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(pg_collections, "get_pool", _explode)


@pytest.mark.parametrize(
    "func, arg",
    [("get_document", "art_c0"), ("get_article", "art")],
)
def test_store_failure_propagates_instead_of_looking_like_a_miss(broken_store, func, arg):
    """pg_collections used to swallow the exception and return None.

    None is how "no such row" is spelled, so a database that is DOWN answered
    the same as a document that does not exist. The caller cannot distinguish
    them, so it reports an absence it never verified.
    """
    from src.vectorstore import pg_collections

    with pytest.raises(RuntimeError):
        getattr(pg_collections, func)(arg)


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("get_document", {"doc_id": "art_c0"}),
        ("get_article", {"article_id": "art"}),
    ],
)
def test_dispatch_reports_an_error_not_a_not_found_on_outage(broken_store, tool, arguments):
    out = _call(tool, **arguments)

    assert out == srv.GENERIC_ERROR
    assert "no encontrado" not in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# An unknown collection must not read as an empty corpus
# ─────────────────────────────────────────────────────────────────────────────


def test_known_collections_matches_the_declared_enum():
    """One source of truth, same as WRITABLE_COLLECTIONS for the write side."""
    enum = _read_tool("search_docs").input_schema["properties"]["collection"]["enum"]
    assert set(enum) == set(srv.KNOWN_COLLECTIONS)


@pytest.fixture
def store_must_not_be_touched(monkeypatch):
    """Blow up on any retrieval work, so validation has to happen strictly before it.

    Not just hygiene: search_docs embeds the query before it looks at anything
    else, and loading e5-large costs ~2.2 GB and seconds of cold start. A
    rejected argument must never pay that.
    """
    from src.vectorstore import pg_collections, pg_retriever

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("validation must reject before reaching the store")

    monkeypatch.setattr(pg_retriever, "hybrid_search", _explode)
    monkeypatch.setattr(pg_collections, "get_article", _explode)
    monkeypatch.setattr(pg_collections, "get_document", _explode)


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("search_docs", {"query": "for each", "collection": "gx_doc"}),
        ("get_article", {"article_id": "art", "collection": "gx_doc"}),
        ("get_document", {"doc_id": "art_c0", "collection": "gx_doc"}),
    ],
)
def test_a_misspelled_collection_is_named_as_such(store_must_not_be_touched, tool, arguments):
    """`gx_doc` is a typo for `gx_docs`, and nothing validated it at runtime.

    The MCP SDK does not enforce the inputSchema enum, so the typo reached SQL,
    matched nothing, and came back as "Sin resultados" / "no encontrado" — an
    agent reads that as evidence about the CORPUS, not about its own argument.
    """
    out = _call(tool, **arguments)

    assert "desconocida" in out.lower()
    assert "gx_docs" in out, "must list the valid collections"
    assert "Sin resultados" not in out


# ─────────────────────────────────────────────────────────────────────────────
# Respuestas que el agente lee: get_article sin hit y el veredicto de verify_syntax
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def article_only_in(monkeypatch):
    """Simula un store donde `art` existe solo en la colección indicada."""
    from src.vectorstore import pg_collections

    def _install(home: str | None, counts: dict[str, int]):
        monkeypatch.setattr(
            pg_collections, "get_article",
            lambda article_id, collection="gx_docs":
                {"title": "T"} if (article_id == "art" and collection == home) else None,
        )
        monkeypatch.setattr(pg_collections, "collection_counts", lambda: list(counts.items()))

    return _install


def test_get_article_miss_names_the_collection_where_the_article_lives(article_only_in):
    """El default es gx_docs: un agente que buscó en bt_docs y llamó get_article sin
    `collection` recibía «Artículo no encontrado.» y lo leía como ausencia."""
    article_only_in("bt_docs", {"gx_docs": 10, "bt_docs": 5})

    out = _call("get_article", article_id="art")

    assert "bt_docs" in out
    assert "collection" in out


def test_get_article_miss_on_an_empty_collection_says_it_is_empty(article_only_in):
    article_only_in(None, {"gx_docs": 0, "bt_docs": 5})

    out = _call("get_article", article_id="nada", collection="gx_docs")

    assert "vacía" in out


def test_get_article_miss_with_a_chunk_id_points_to_get_document(article_only_in):
    article_only_in(None, {"gx_docs": 10})

    out = _call("get_article", article_id="wiki/foo_c3")

    assert "get_document" in out


def test_verify_syntax_verdict_is_json(monkeypatch):
    """El veredicto salía como repr de un dict de Python ({'verdict': 'VÁLIDO'}):
    legible, pero no parseable, y con comillas simples que ningún cliente espera."""
    import json

    from src.syntax_db import gx_repository

    monkeypatch.setattr(gx_repository, "verify_syntax",
                        lambda snippet, language="gx", version=None:
                            {"verdict": "VÁLIDO", "name": "Trim"})

    first_line = _call("verify_syntax", snippet="Trim(&x)").splitlines()[0]

    assert json.loads(first_line)["verdict"] == "VÁLIDO"


def test_search_docs_bounds_arguments_defensively(monkeypatch):
    """top_k y snippet_chars deben acotarse para evitar agotamiento de memoria por valores desmedidos."""
    from src.vectorstore import pg_retriever

    captured = {}
    def fake_hybrid_search(
        q, collection="gx_docs", top_k=5, min_score=None, filters=None, rerank=False, max_per_article=None, **kwargs
    ):
        captured["top_k"] = top_k
        captured["max_per_article"] = max_per_article
        return [{"title": "t", "doc_id": "d", "id": "i", "score": 0.05, "content": "x" * 5000}]

    monkeypatch.setattr(pg_retriever, "hybrid_search", fake_hybrid_search)

    out = _call("search_docs", query="test", top_k=9999, snippet_chars=99999, max_per_article=2)
    assert captured["top_k"] == 50
    assert captured["max_per_article"] == 2
    # Snippet queda acotado a 4000 caracteres
    assert "…" in out


def test_pg_retriever_tokenize_separates_underscores():
    """Identificadores con guión bajo (foo_bar) deben generar tokens separados en OR,
    evitando que to_tsquery los traduzca al operador de frase estricta <->."""
    from src.vectorstore.pg_retriever import _to_tsquery, _tokenize

    tokens = _tokenize("BC_Load customer_id")
    assert tokens == ["bc", "load", "customer", "id"]
    tsq = _to_tsquery("BC_Load customer_id")
    assert tsq == "bc | load | customer | id"


def test_search_params_handles_max_per_article():
    """search_params debe propagar max_per_article y ajustar el pool de candidatos proporcionalmente."""
    from src.vectorstore.pg_retriever import search_params, CANDIDATE_POOL

    params_none = search_params("cadena", "bt_docs", 5)
    assert params_none["max_per_article"] is None
    assert params_none["pool"] == max(CANDIDATE_POOL, 5 * 3)

    params_dedup = search_params("cadena", "bt_docs", 5, max_per_article=1)
    assert params_dedup["max_per_article"] == 1
    assert params_dedup["pool"] == max(CANDIDATE_POOL, 5 * 4)


def test_search_sql_partitions_by_article_id():
    """El SQL de búsqueda debe particionar por article_id en la ventana de ranking."""
    from src.vectorstore.pg_retriever import _SEARCH_SQL

    assert "PARTITION BY c.article_id" in _SEARCH_SQL
    assert "article_rank" in _SEARCH_SQL
    assert "%(max_per_article)s" in _SEARCH_SQL


