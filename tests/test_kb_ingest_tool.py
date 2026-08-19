"""Ingesta de fichas de KB: la librería y su adaptador MCP.

Sin Postgres: `load_fichas` acepta `index_fn` y `resolve_fn` inyectables justamente
para esto, así que se puede probar toda la lógica de decisión sin motor levantado.

El foco de los tests de RENDER no es cosmético. El texto de respuesta es lo que un LLM
lee al concluir, y este repo tiene incidentes documentados de estados que se leyeron al
revés — un "skipped" tomado por fallo dispara un reintento inútil sobre 3.157 objetos.
"""

from __future__ import annotations

import asyncio

import pytest

import src.server as srv
from src.ingestion.kb_loader import KB_MAX_INLINE, load_fichas

KB = "MatikaErp_3003"


def _ficha(nombre="PrcMayorizar", tipo="Procedure", kb_hash="h1", **over) -> dict:
    base = {
        "obj_name": nombre,
        "obj_type": tipo,
        "kb_hash": kb_hash,
        "sections": [
            {"kind": "structure", "text": "Recibe &Ejercicio (in, N4)."},
            {"kind": "logic", "text": "Recorre los movimientos."},
        ],
    }
    base.update(over)
    return base


def _call(**arguments) -> str:
    content = asyncio.run(srv.dispatch_tool("ingest_kb_objects", arguments))
    return content[0].text


@pytest.fixture
def sin_indexar():
    """resolve_fn que dice que no hay nada indexado."""
    return lambda ids: {}


@pytest.fixture
def indexador():
    """index_fn que registra las llamadas y siempre reporta 'indexed'."""
    llamadas = []

    def fn(*args, **kwargs):
        llamadas.append((args, kwargs))
        return "indexed"

    fn.llamadas = llamadas
    return fn


# ─── dry run: es una consulta, no un peaje ────────────────────────────────────


def test_dry_run_is_the_default(indexador, sin_indexar):
    reporte = load_fichas(
        [_ficha()], kb_name=KB, index_fn=indexador, resolve_fn=sin_indexar
    )

    assert reporte.dry_run is True
    assert indexador.llamadas == [], "un dry run no puede escribir"


def test_dry_run_never_embeds(sin_indexar):
    """Si el dry run embeddeara, dejaría de ser barato y nadie lo usaría."""

    def explota(*a, **k):  # pragma: no cover
        raise AssertionError("el dry run no debe llegar al indexador")

    reporte = load_fichas(
        [_ficha()], kb_name=KB, index_fn=explota, resolve_fn=sin_indexar
    )

    assert reporte.contar("would_index") == 1


def test_dry_run_tells_which_fichas_are_actually_new(indexador):
    """El comportamiento que convierte el dry run en una consulta útil.

    El agente navegó 3 objetos; 2 ya están con el mismo hash. Necesita saber que solo
    1 hace falta escribir, sin pagar el re-embedding de los 3.
    """
    from src.ingestion import kb_fichas as kf

    vieja_a, vieja_b, nueva = _ficha("A"), _ficha("B"), _ficha("C")
    ya_estan = {
        kf.ficha_article_id(f, kb_name=KB): kf.ficha_content_hash(f, kb_name=KB)
        for f in (vieja_a, vieja_b)
    }

    reporte = load_fichas(
        [vieja_a, vieja_b, nueva],
        kb_name=KB,
        index_fn=indexador,
        resolve_fn=lambda ids: ya_estan,
    )

    assert reporte.contar("skipped") == 2
    assert reporte.contar("would_index") == 1


def test_a_changed_kb_hash_is_reported_as_a_reindex(indexador):
    from src.ingestion import kb_fichas as kf

    ficha = _ficha(kb_hash="nuevo")
    aid = kf.ficha_article_id(ficha, kb_name=KB)

    reporte = load_fichas(
        [ficha], kb_name=KB, index_fn=indexador, resolve_fn=lambda ids: {aid: "viejo"}
    )

    assert reporte.contar("would_reindex") == 1


# ─── escritura real ───────────────────────────────────────────────────────────


def test_writing_passes_presplit_chunks_and_meta(indexador, sin_indexar):
    """La ficha se parte por SECCIÓN en el llamador, no por 800 chars en el indexer."""
    load_fichas(
        [_ficha()],
        kb_name=KB,
        dry_run=False,
        index_fn=indexador,
        resolve_fn=sin_indexar,
    )

    (_, kwargs) = indexador.llamadas[0]
    assert [k for k, _ in kwargs["chunks"]] == ["structure", "logic"]
    assert kwargs["meta"]["kb_name"] == KB
    assert kwargs["meta"]["obj_type"] == "Procedure"


def test_writing_targets_the_kb_collection(indexador, sin_indexar):
    load_fichas(
        [_ficha()], kb_name=KB, dry_run=False, index_fn=indexador,
        resolve_fn=sin_indexar,
    )

    (args, _) = indexador.llamadas[0]
    assert args[-1] == "gx_kb_objects"


def test_a_failing_ficha_does_not_abort_the_rest(sin_indexar):
    def falla_una(*args, **kwargs):
        if "Malo" in args[0]:
            raise RuntimeError("boom")
        return "indexed"

    reporte = load_fichas(
        [_ficha("Bueno"), _ficha("Malo"), _ficha("Otro")],
        kb_name=KB, dry_run=False, index_fn=falla_una, resolve_fn=sin_indexar,
    )

    assert reporte.contar("indexed") == 2
    assert reporte.contar("error") == 1


def test_a_store_that_cannot_be_queried_does_not_break_the_load(indexador):
    """resolve_fn es una OPTIMIZACIÓN: si falla, reindex_if_changed decide igual."""

    def revienta(ids):
        raise RuntimeError("sin conexión")

    reporte = load_fichas(
        [_ficha()], kb_name=KB, dry_run=False, index_fn=indexador,
        resolve_fn=revienta,
    )

    assert reporte.contar("indexed") == 1


# ─── validación ───────────────────────────────────────────────────────────────


def test_on_invalid_stop_writes_nothing(indexador, sin_indexar):
    """En un lote, seguir de largo deja agujeros que nadie distingue de un objeto
    que la KB no tiene."""
    mala = {"obj_type": "Procedure", "sections": []}  # sin obj_name y sin secciones

    reporte = load_fichas(
        [_ficha("Bueno"), mala],
        kb_name=KB, dry_run=False, index_fn=indexador, resolve_fn=sin_indexar,
    )

    assert indexador.llamadas == []
    assert reporte.contar("invalid") == 1
    assert reporte.contar("indexed") == 0


def test_on_invalid_skip_writes_the_valid_ones(indexador, sin_indexar):
    mala = {"obj_type": "Procedure", "sections": []}

    reporte = load_fichas(
        [_ficha("Bueno"), mala],
        kb_name=KB, dry_run=False, on_invalid="skip",
        index_fn=indexador, resolve_fn=sin_indexar,
    )

    assert len(indexador.llamadas) == 1
    assert reporte.contar("invalid") == 1


def test_an_invalid_ficha_without_identity_is_reported_by_its_index(
    indexador, sin_indexar
):
    """Sin obj_name/obj_type no hay article_id, y el índice en el array es lo único
    que el llamador puede mapear a lo que mandó."""
    reporte = load_fichas(
        [_ficha("Bueno"), {"sections": []}],
        kb_name=KB, on_invalid="skip", index_fn=indexador, resolve_fn=sin_indexar,
    )

    invalida = next(r for r in reporte.results if r.status == "invalid")
    assert invalida.article_id is None
    assert invalida.index == 1


def test_fichas_without_kb_hash_are_counted_to_be_warned_about(indexador, sin_indexar):
    reporte = load_fichas(
        [_ficha(kb_hash=""), _ficha("Otro", kb_hash="h")],
        kb_name=KB, index_fn=indexador, resolve_fn=sin_indexar,
    )

    assert reporte.sin_kb_hash == 1


# ─── el adaptador MCP ─────────────────────────────────────────────────────────


def test_over_the_limit_is_rejected_not_truncated():
    """Un lote truncado reportaría éxito sobre un subconjunto mientras el llamador
    cree que entraron todas."""
    salida = _call(kb_name=KB, objects=[_ficha(f"P{i}") for i in range(KB_MAX_INLINE + 1)])

    assert str(KB_MAX_INLINE) in salida
    assert "NO se indexó ninguna" in salida
    assert "bootstrap_kb.py" in salida  # dice cuál es el camino para volumen


def test_a_missing_kb_name_is_rejected_with_where_to_get_it():
    salida = _call(objects=[_ficha()])

    assert "kb_name" in salida
    assert "genexus_whoami" in salida


def test_an_empty_objects_list_is_rejected():
    salida = _call(kb_name=KB, objects=[])

    assert "vacío" in salida or "vacio" in salida


def test_an_unknown_on_invalid_is_rejected():
    salida = _call(kb_name=KB, objects=[_ficha()], on_invalid="continuar")

    assert "continuar" in salida
    assert "stop" in salida


# ─── el reporte que lee el LLM ────────────────────────────────────────────────


def _reporte(**kw):
    from src.ingestion.kb_loader import KbLoadReport

    r = KbLoadReport(kb_name=KB, **kw)
    return r


def test_the_report_leads_with_the_totals():
    """Una lectura truncada igual se tiene que llevar el resumen."""
    from src.ingestion.kb_loader import FichaResult

    r = _reporte()
    r.results = [FichaResult(0, "kb:X:Procedure:A", "skipped")]

    texto = srv._render_ingest_report(r)

    assert texto.splitlines()[0].startswith("INGESTA")
    assert "total=1" in texto.splitlines()[1]


def test_every_status_present_is_glossed_in_the_same_response():
    """La descripción se lee al elegir la tool; esto se lee al CONCLUIR."""
    from src.ingestion.kb_loader import FichaResult

    r = _reporte()
    r.results = [
        FichaResult(0, "kb:X:Procedure:A", "skipped"),
        FichaResult(1, "kb:X:Procedure:B", "would_index", 2, ("structure", "logic")),
    ]

    texto = srv._render_ingest_report(r)

    assert "QUÉ SIGNIFICA CADA ESTADO" in texto
    assert "skipped" in texto
    assert "would_index" in texto


def test_skipped_is_explained_as_idempotence_not_failure():
    """El modo de falla que esto previene: un reintento inútil sobre miles de objetos."""
    from src.ingestion.kb_loader import FichaResult

    r = _reporte()
    r.results = [FichaResult(0, "kb:X:Procedure:A", "skipped")]

    texto = srv._render_ingest_report(r)

    assert "NO es error" in texto
    assert "no reintentes" in texto


def test_the_report_points_at_get_article_for_independent_verification():
    """Hard Rule del repo: el retorno de una tool no describe el store."""
    from src.ingestion.kb_loader import FichaResult

    r = _reporte(dry_run=False)
    r.results = [FichaResult(0, "kb:X:Procedure:A", "indexed", 2, ("structure",))]

    texto = srv._render_ingest_report(r)

    assert "get_article" in texto
    assert "NO uses search_docs para comprobar pertenencia" in texto


def test_a_dry_run_report_does_not_suggest_verifying_a_write():
    from src.ingestion.kb_loader import FichaResult

    r = _reporte(dry_run=True)
    r.results = [FichaResult(0, "kb:X:Procedure:A", "would_index", 2, ("structure",))]

    texto = srv._render_ingest_report(r)

    assert "DRY RUN" in texto
    assert "get_article" not in texto


def test_problems_are_never_abbreviated():
    from src.ingestion.kb_loader import FichaResult

    r = _reporte()
    r.results = [
        FichaResult(i, None, "invalid", reason=f"problema numero {i}")
        for i in range(6)
    ]

    texto = srv._render_ingest_report(r)

    for i in range(6):
        assert f"problema numero {i}" in texto


def test_the_missing_kb_hash_warning_appears_when_it_applies():
    from src.ingestion.kb_loader import FichaResult

    r = _reporte()
    r.sin_kb_hash = 2
    r.results = [FichaResult(0, "kb:X:Procedure:A", "would_index", 1, ("logic",))]

    texto = srv._render_ingest_report(r)

    assert "sin `kb_hash`" in texto
    assert "re-embeddean" in texto


# ─── exposición ───────────────────────────────────────────────────────────────


def test_the_tool_is_a_write_tool():
    """Que esté en WRITE_TOOLS es lo que la mantiene fuera del transporte público, y
    test_server_write_tools.py verifica esa propiedad para toda la familia."""
    assert "ingest_kb_objects" in {t.name for t in srv.WRITE_TOOLS}
    assert "ingest_kb_objects" not in srv.PUBLIC_TOOL_NAMES


def test_the_kb_collection_is_not_writable_by_the_doc_ingest_tools():
    """add_from_url / ingest_batch no deben poder escribir en la colección de KB: el
    CHECK de sql/003 rechazaría sus filas por falta de identidad, pero la lista blanca
    declara la política antes de que el motor tenga que hacerla cumplir."""
    assert srv.KB_COLLECTION not in srv.WRITABLE_COLLECTIONS
