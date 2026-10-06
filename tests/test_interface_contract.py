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
