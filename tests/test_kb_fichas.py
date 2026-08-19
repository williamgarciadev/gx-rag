"""Ficha de objeto GeneXus -> chunks. Tests puros: corren sin Postgres.

El test que más importa acá es `test_the_hash_ignores_the_prose_and_follows_kb_hash`:
sin esa propiedad, indexar 3.157 objetos costaría el re-embedding completo en CADA
resincronización, y "skipped" dejaría de significar nada.
"""

from __future__ import annotations

import pytest

from src.ingestion import kb_fichas as kf

KB = "MatikaErp_3003"


def _ficha(**over) -> dict:
    base = {
        "obj_name": "PrcMayorizar",
        "obj_type": "Procedure",
        "module": "CentralizacionContable",
        "kb_hash": "abc123",
        "related": ["TrnAsiento"],
        "sections": [
            {"kind": "structure", "text": "Recibe &Ejercicio (in, N4) y &Ok (out, B)."},
            {"kind": "logic", "text": "Recorre los movimientos del ejercicio."},
        ],
    }
    base.update(over)
    return base


# ─── chunking ─────────────────────────────────────────────────────────────────


def test_one_section_is_one_chunk_when_it_fits():
    chunks = kf.ficha_to_chunks(_ficha(), kb_name=KB)

    assert len(chunks) == 2
    assert [k for k, _ in chunks] == ["structure", "logic"]


def test_an_oversized_section_is_split_and_every_part_keeps_its_kind():
    """El test que protege el filtro por chunk_kind cuando una sección se parte.

    Si el kind viajara solo en el primer sub-chunk, un filtro por chunk_kind=logic
    encontraría el principio de la lógica y no el resto — y el agente leería una
    versión truncada creyendo que es completa.
    """
    largo = "palabra " * 600  # ~4.800 chars, muy por encima de KB_CHUNK_SIZE
    ficha = _ficha(sections=[{"kind": "logic", "text": largo}])

    chunks = kf.ficha_to_chunks(ficha, kb_name=KB)

    assert len(chunks) > 1, "una sección de ~4.800 chars tiene que partirse"
    assert {k for k, _ in chunks} == {"logic"}


def test_a_section_below_the_limit_is_never_split():
    ficha = _ficha(sections=[{"kind": "rule", "text": "x" * (kf.KB_CHUNK_SIZE - 1)}])

    assert len(kf.ficha_to_chunks(ficha, kb_name=KB)) == 1


def test_the_kb_chunk_size_stays_inside_the_e5_window():
    """e5 trunca a ~512 tokens (~2.000 chars). Un chunk más grande se perdería en
    la mitad densa mientras el tsvector sí lo ve, dejando las dos mitades del RRF en
    desacuerdo sin ningún error."""
    assert kf.KB_CHUNK_SIZE <= 2000


def test_the_kb_splitter_does_not_touch_the_doc_chunk_size():
    """Cambiar el 800 de pg_indexer dejaría el corpus viejo con otra granularidad que
    el nuevo dentro de la MISMA colección."""
    from src.vectorstore import pg_indexer

    assert pg_indexer.CHUNK_SIZE == 800
    assert kf.KB_CHUNK_SIZE != pg_indexer.CHUNK_SIZE


# ─── hash: la propiedad crítica ───────────────────────────────────────────────


def test_the_hash_ignores_the_prose_and_follows_kb_hash():
    """Reescribir el texto con el mismo kb_hash NO cambia el hash.

    Es lo que hace que un generador de fichas no determinista (un LLM redactando) no
    produzca "reindexed" en cada corrida sobre 3.157 objetos.
    """
    a = _ficha()
    b = _ficha(
        sections=[
            {"kind": "structure", "text": "OTRA redacción completamente distinta."},
            {"kind": "logic", "text": "Y esta también cambió de arriba a abajo."},
        ]
    )

    assert kf.ficha_content_hash(a, kb_name=KB) == kf.ficha_content_hash(b, kb_name=KB)


def test_the_hash_changes_when_the_object_changed_in_the_kb():
    a = _ficha(kb_hash="abc123")
    b = _ficha(kb_hash="def456")

    assert kf.ficha_content_hash(a, kb_name=KB) != kf.ficha_content_hash(b, kb_name=KB)


def test_bumping_the_schema_version_changes_every_hash(monkeypatch):
    antes = kf.ficha_content_hash(_ficha(), kb_name=KB)
    monkeypatch.setattr(kf, "FICHA_SCHEMA_VERSION", kf.FICHA_SCHEMA_VERSION + 1)
    despues = kf.ficha_content_hash(_ficha(), kb_name=KB)

    assert antes != despues


def test_the_hash_is_scoped_to_the_kb():
    """Dos KBs con el mismo objeto y el mismo source no comparten hash."""
    f = _ficha()

    assert kf.ficha_content_hash(f, kb_name="A") != kf.ficha_content_hash(f, kb_name="B")


def test_the_hash_is_stable_across_calls():
    """json.dumps con sort_keys: el orden de las claves del dict no puede filtrarse."""
    f1 = {"obj_name": "X", "obj_type": "Procedure", "kb_hash": "h", "sections": []}
    f2 = {"kb_hash": "h", "sections": [], "obj_type": "Procedure", "obj_name": "X"}

    assert kf.ficha_content_hash(f1, kb_name=KB) == kf.ficha_content_hash(f2, kb_name=KB)


# ─── identidad ────────────────────────────────────────────────────────────────


def test_the_article_id_namespaces_kb_and_obj_type():
    assert kf.ficha_article_id(_ficha(), kb_name=KB) == (
        f"kb:{KB}:Procedure:PrcMayorizar"
    )


def test_two_kbs_with_a_homonymous_object_do_not_collide():
    f = _ficha()

    assert kf.ficha_article_id(f, kb_name="A") != kf.ficha_article_id(f, kb_name="B")


def test_two_object_types_with_the_same_name_do_not_collide():
    """En GeneXus es legal que un Procedure y una Transaction se llamen igual."""
    a = _ficha(obj_type="Procedure")
    b = _ficha(obj_type="Transaction")

    assert kf.ficha_article_id(a, kb_name=KB) != kf.ficha_article_id(b, kb_name=KB)


# ─── metadatos ────────────────────────────────────────────────────────────────


def test_meta_carries_the_identity_the_check_requires():
    """sql/003 exige obj_name, obj_type, chunk_kind y kb_name. Los tres primeros
    salen de acá; chunk_kind lo agrega pg_indexer por chunk."""
    meta = kf.ficha_meta(_ficha(), kb_name=KB)

    assert meta["obj_name"] == "PrcMayorizar"
    assert meta["obj_type"] == "Procedure"
    assert meta["kb_name"] == KB


def test_optional_meta_keys_are_omitted_not_empty():
    """Una clave presente con "" haría que `meta ? 'module'` sea verdadero y el filtro
    devolviera filas sin módulo real."""
    meta = kf.ficha_meta(
        {"obj_name": "X", "obj_type": "Procedure", "sections": []}, kb_name=KB
    )

    assert "module" not in meta
    assert "kb_hash" not in meta
    assert "related" not in meta


def test_related_is_a_list_for_the_gin_index():
    meta = kf.ficha_meta(_ficha(), kb_name=KB)

    assert meta["related"] == ["TrnAsiento"]


# ─── validación ───────────────────────────────────────────────────────────────


def test_a_valid_ficha_has_no_problems():
    assert kf.validate_ficha(_ficha()) == []


def test_a_ficha_without_sections_is_rejected_not_indexed_empty():
    """pg_indexer devuelve [] con un warning cuando no hay chunks: entraría
    "exitosamente" sin indexar nada, y después nadie lo distingue de un objeto que la
    KB no tiene."""
    problemas = kf.validate_ficha(_ficha(sections=[]))

    assert problemas
    assert "sections" in problemas[0]
    assert "omitilo" in problemas[0]


@pytest.mark.parametrize("campo", ["obj_name", "obj_type"])
def test_identity_fields_are_required(campo):
    ficha = _ficha()
    del ficha[campo]

    assert any(campo in p for p in kf.validate_ficha(ficha))


def test_an_unknown_chunk_kind_is_rejected():
    """chunk_kind alimenta el filtro: un valor no declarado sería invisible a toda
    búsqueda filtrada."""
    ficha = _ficha(sections=[{"kind": "inventado", "text": "x"}])

    problemas = kf.validate_ficha(ficha)

    assert any("inventado" in p for p in problemas)
    assert any("structure" in p for p in problemas)  # nombra los válidos


def test_an_empty_section_text_is_rejected():
    ficha = _ficha(sections=[{"kind": "logic", "text": "   "}])

    assert kf.validate_ficha(ficha)


def test_validation_reports_every_problem_not_just_the_first():
    """Descubrirlos de a uno por reintento es fricción pura en un lote."""
    ficha = {
        "sections": [
            {"kind": "malo", "text": ""},
            {"kind": "peor", "text": ""},
        ]
    }

    problemas = kf.validate_ficha(ficha)

    assert len(problemas) >= 4  # obj_name, obj_type, y 2 por sección


def test_an_unusual_obj_type_is_accepted():
    """La KB medida tiene 42 tipos distintos y la lista crece con cada release.
    Rechazar un tipo legítimo bloquearía la ingesta de objetos reales."""
    assert kf.validate_ficha(_ficha(obj_type="ThemeTransformation")) == []


def test_validation_does_not_raise_on_garbage():
    assert kf.validate_ficha(None)
    assert kf.validate_ficha("una ficha")
    assert kf.validate_ficha(42)
