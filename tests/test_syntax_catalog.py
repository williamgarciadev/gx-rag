"""Workstream C — reviving the binary-verdict syntax catalog (the 'cut' layer).

The syntax DB engine is a module-level singleton; seeding it with a temp SQLite
path makes every get_session() in every module hit the throwaway DB, so these
tests never touch data/syntax.db.
"""

import pytest

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# Candidate repository — staging + human-gated promotion
# ─────────────────────────────────────────────────────────────────────────────


def test_promote_candidate_makes_verify_syntax_valid(temp_db):
    """The whole point of C: a promoted candidate turns NO_VERIFICABLE → VÁLIDO."""
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    # Before curation the catalog is empty → the cut layer says NO_VERIFICABLE.
    assert verify_syntax("Default")["verdict"] == "NO_VERIFICABLE"

    cid = cr.store_candidate(
        name="Default",
        object_type="Rule",
        signature={"raw": "Default(att | &var, expression);"},
        description="Assigns a default value.",
        evidence="Syntax Default(att | &var, expression);",
        source_doc_id="https://wiki.genexus.com/commwiki/servlet/wiki?6850",
    )

    promoted = cr.promote_candidate(cid)
    assert promoted is not None

    verdict = verify_syntax("Default")
    assert verdict["verdict"] == "VÁLIDO"
    assert verdict["name"] == "Default"
    assert verdict["source"] == "wiki"


def _promote(cr, name, object_type="Method", raw="x"):
    """Alta rápida de una entrada ya curada en el catálogo."""
    return cr.promote_candidate(
        cr.store_candidate(name=name, object_type=object_type, signature={"raw": raw})
    )


def test_verify_syntax_resolves_a_code_snippet(temp_db):
    """El caso de uso real: verificar código, no el título de un artículo.

    La wiki titula "AddYears method"; nadie escribe eso en un procedure. Si
    verify_syntax solo entendiera el título, el catálogo curado no se usaría
    nunca en el flujo que importa.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddYears method", raw="Date | DateTime.AddYears( Numeric-expression )")

    for snippet in ("&fecha.AddYears(5)", "AddYears", "AddYears method"):
        assert verify_syntax(snippet)["verdict"] == "VÁLIDO", snippet


def test_verify_syntax_reports_every_entry_for_an_ambiguous_member(temp_db):
    """Add existe sobre colección de SDT y sobre colección de BC.

    Devolver una sola y callar la otra es exactamente la falsa autoridad que
    este catálogo existe para evitar.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "Add method - SDT Collection", raw="&VarBasedOnSDTCollection.Add( &Item )")
    _promote(cr, "Business Component Add method", raw="&varBasedOnBCCollection.add( &bc )")

    r = verify_syntax("&col.Add(&item)")

    assert r["verdict"] == "VÁLIDO"
    assert r["otras_entradas"], "la segunda entrada tiene que salir a la luz"
    assert {r["name"], *r["otras_entradas"]} == {
        "Add method - SDT Collection",
        "Business Component Add method",
    }


def test_verify_syntax_does_not_invent_a_match_for_a_made_up_method(temp_db):
    """Un método inventado NO puede dar VÁLIDO.

    Con el catálogo incompleto la respuesta honesta es NO_VERIFICABLE: el
    sistema no tiene la entrada, y eso no es lo mismo que afirmar que no existe.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddYears method", raw="Date | DateTime.AddYears( Numeric-expression )")

    for inventado in ("&fecha.AddCenturies(1)", "AddWeeks method"):
        assert verify_syntax(inventado)["verdict"] != "VÁLIDO", inventado


def test_verify_syntax_does_not_confuse_a_prefix_with_a_member(temp_db):
    """"Add" no puede resolver a "AddRange method" por ser prefijo suyo."""
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddRange method", raw="&c1.AddRange(&c2)")

    assert verify_syntax("&col.Add(&item)")["verdict"] != "VÁLIDO"
    assert verify_syntax("&c1.AddRange(&c2)")["verdict"] == "VÁLIDO"


def test_store_candidate_dedups_and_increments_hit_count(temp_db):
    from src.syntax_db import candidate_repository as cr

    cr.store_candidate(name="Msg", object_type="Method")
    cr.store_candidate(name="Msg", object_type="Method")

    pending = cr.list_candidates(status="pending")
    assert len(pending) == 1
    assert pending[0]["hit_count"] == 2


def test_list_candidates_filters_by_status_and_reject_works(temp_db):
    from src.syntax_db import candidate_repository as cr

    a = cr.store_candidate(name="A", object_type="Property")
    cr.store_candidate(name="B", object_type="Property")

    cr.reject_candidate(a)

    pending = cr.list_candidates(status="pending")
    assert [c["name"] for c in pending] == ["B"]
    assert cr.list_candidates(status="rejected")[0]["name"] == "A"


# ─────────────────────────────────────────────────────────────────────────────
# Extractor — discovery of candidate signatures from dump blocks
# ─────────────────────────────────────────────────────────────────────────────


class _Doc:
    def __init__(self, title, content, url):
        self.title = title
        self.content = content
        self.url = url


def test_extract_candidate_from_reference_block():
    from src.syntax_db.extractor import extract_candidates

    doc = _Doc(
        title="Default",
        content=(
            "Default rule. Assigns a default value to an attribute. "
            "Syntax Default(att | &var, expression); Where att is the attribute."
        ),
        url="https://wiki.genexus.com/commwiki/servlet/wiki?6850",
    )

    drafts = extract_candidates([doc])

    assert len(drafts) == 1
    d = drafts[0]
    assert d.name == "Default"
    assert d.signature.startswith("Default(")
    assert "Where" not in d.signature  # trailing prose trimmed
    assert d.source_doc_id == doc.url
    assert d.object_type  # non-empty (required by GxSyntax)


def test_extract_skips_prose_without_syntax():
    from src.syntax_db.extractor import extract_candidates

    doc = _Doc(
        title="Introduction to WorkWithPlus and its many productivity features",
        content="This is a long guide describing how to get started, step by step.",
        url="https://docs.workwithplus.com/wiki?1",
    )

    assert extract_candidates([doc]) == []


# ─────────────────────────────────────────────────────────────────────────────
# gx_repository fix: bt_api routing must not hit the GxSyntax table
# ─────────────────────────────────────────────────────────────────────────────


def test_verify_bt_api_routes_to_bt_apis_table(temp_db):
    from src.syntax_db.models import BtApi, get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    session.add(BtApi(endpoint="GetBalance", method="GET", path="/accounts/balance"))
    session.commit()
    session.close()

    # bt_api language must query BtApi (found) ...
    assert verify_syntax("GetBalance", language="bt_api")["verdict"] == "VÁLIDO"
    # ... while the default gx language does NOT find it in the empty GxSyntax table.
    assert verify_syntax("GetBalance", language="gx")["verdict"] == "NO_VERIFICABLE"


# ─────────────────────────────────────────────────────────────────────────────
# `version` no puede fabricar una lista vacía
# ─────────────────────────────────────────────────────────────────────────────


def test_list_methods_ignores_version_because_the_catalog_has_none(temp_db):
    """Filtrar por `version` garantizaba CERO resultados, siempre.

    No es que los datos "hoy" tengan version_since en NULL: promote_candidate
    —la UNICA via al catalogo— lo escribe hardcodeado en None
    (candidate_repository.py:94). Medido sobre el catalogo real: 59 filas, las 59
    con NULL. El agente pasaba `version` porque el schema lo invitaba y recibia
    "Sin entradas catalogadas", que leia como "ese objeto no tiene metodos":
    alucinacion en negativo por la via del filtro.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import list_methods

    _promote(cr, "AddYears method", object_type="Method")

    assert list_methods("Method") == ["AddYears method"]
    assert list_methods("Method", version="gx18") == ["AddYears method"]


def test_list_object_types_reports_the_real_taxonomy(temp_db):
    """La respuesta vacia tiene que nombrar los object_type que EXISTEN.

    La descripcion de la tool daba "Transaction, Procedure" como ejemplos y el
    catalogo real no tiene ninguno de los dos (medido: Method 34, Event 9,
    Command 8, Rule 6, Property 2). Un ejemplo que garantiza cero resultados es
    peor que ningun ejemplo.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import list_object_types

    _promote(cr, "AddYears method", object_type="Method")
    _promote(cr, "Default", object_type="Rule")

    assert list_object_types() == ["Method", "Rule"]


def test_valido_declares_that_it_is_not_version_gated(temp_db):
    """Un VALIDO sin aclarar la version es una firma de version indeterminada.

    docs/gx-bt-rag-calidad-y-no-alucinacion.md §1 cuenta "dar una firma de la
    version equivocada" como alucinar. El veredicto NO esta gateado por version,
    asi que tiene que decirlo donde el agente concluye.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddYears method", object_type="Method")

    r = verify_syntax("&fecha.AddYears(5)", version="gx18")

    assert r["verdict"] == "VÁLIDO"
    assert "gx18" in r["notes"]
    assert "no discrimina" in r["notes"]


# ─────────────────────────────────────────────────────────────────────────────
# Un snippet sin tokens no puede producir un veredicto negativo
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("snippet", ["", "   ", "do", "if else", "return true"])
def test_verify_syntax_never_fabricates_invalido_without_a_token(temp_db, snippet):
    """`_normalize_token` filtra stopwords: "do" queda en "".

    Con el token vacio la rama de similitud ejecutaba `name LIKE '%%'`, que
    matchea TODAS las filas, y devolvia INVALIDO con 5 sugerencias arbitrarias.
    Un veredicto negativo FABRICADO, en la tool cuyo unico trabajo es no hacer eso.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddYears method", object_type="Method")
    _promote(cr, "Default", object_type="Rule")

    assert verify_syntax(snippet)["verdict"] == "NO_VERIFICABLE", snippet


def test_a_token_too_short_to_compare_is_not_an_accusation(temp_db):
    """La similitud se calcula con `LIKE '%<token[:8]>%'`.

    Con dos caracteres ese patron matchea cualquier cosa que los contenga
    ("Ad" pesca "AddYears", pero tambien pescaria "Load"), asi que el INVALIDO
    que sale de ahi no es informacion: es ruido con forma de veredicto.
    """
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    _promote(cr, "AddYears method", object_type="Method")

    assert verify_syntax("Ad")["verdict"] == "NO_VERIFICABLE"
