"""Loading the verified corpus catalogs into gx_syntax.

Source shape (measured, not assumed):
  api.tsv         3 cols — name, type (command|function|method), url. 100% one word.
  properties.tsv  2 cols — name, url. Only 20% one word.
  datatypes.tsv   2 cols — name, url. 96% one word.
  events.tsv      2 cols — name, url. 72% one word.
All CRLF, no header row.

Two consequences the loader has to face rather than paper over:

- A multi-word name like "Accent Color" is matched by verify_syntax's exact
  path only when the snippet is the bare name: `parse_snippet` never builds one
  out of a statement. Those rows are still loaded — list_methods and the
  near-miss suggestions reach them.
- Names collide ACROSS files: "Call" is both a command and a property. Keying on
  name alone would make the verdict depend on insertion order.
"""

import pytest

from src.syntax_db.catalog_loader import load_entries, parse_catalog_tsv

pytestmark = pytest.mark.unit


def _tsv(tmp_path, name, lines):
    path = tmp_path / name
    # CRLF, like the real corpus files.
    path.write_bytes("\r\n".join(lines).encode("utf-8") + b"\r\n")
    return path


# ─── parsing ──────────────────────────────────────────────────────────────────


def test_a_three_column_file_takes_its_type_from_the_row(tmp_path):
    path = _tsv(tmp_path, "api.tsv", ["Trim\tfunction\thttp://x", "Call\tcommand\thttp://y"])

    entries = list(parse_catalog_tsv(path))

    assert [(e.name, e.object_type) for e in entries] == [
        ("Trim", "function"),
        ("Call", "command"),
    ]


def test_a_two_column_file_takes_its_type_from_the_argument(tmp_path):
    path = _tsv(tmp_path, "events.tsv", ["Start\thttp://x"])

    (entry,) = list(parse_catalog_tsv(path, object_type="event"))

    assert (entry.name, entry.object_type) == ("Start", "event")


def test_carriage_returns_do_not_leak_into_the_url(tmp_path):
    # The files are CRLF; an unstripped \r would end up inside source_doc_id and
    # break every link.
    path = _tsv(tmp_path, "datatypes.tsv", ["Audio\thttp://x"])

    (entry,) = list(parse_catalog_tsv(path, object_type="data type"))

    assert entry.source_doc_id == "http://x"


def test_blank_and_malformed_lines_are_skipped(tmp_path):
    path = _tsv(tmp_path, "api.tsv", ["Trim\tfunction\thttp://x", "", "basura-sin-tab"])

    assert [e.name for e in parse_catalog_tsv(path)] == ["Trim"]


def test_a_two_column_file_without_a_type_is_rejected(tmp_path):
    # Silently defaulting the type would scatter rows under a wrong object_type
    # and make list_methods lie.
    path = _tsv(tmp_path, "events.tsv", ["Start\thttp://x"])

    with pytest.raises(ValueError, match="object_type"):
        list(parse_catalog_tsv(path))


def test_multi_word_names_are_kept(tmp_path):
    # 80% of properties. Unreachable by the exact-match path, still useful to
    # list_methods and to the near-miss suggestions.
    path = _tsv(tmp_path, "properties.tsv", ["Accent Color\thttp://x"])

    (entry,) = list(parse_catalog_tsv(path, object_type="property"))

    assert entry.name == "Accent Color"


# ─── loading ──────────────────────────────────────────────────────────────────


def _entries(*pairs):
    from src.syntax_db.catalog_loader import CatalogEntry

    return [
        CatalogEntry(name=n, object_type=t, source_doc_id="http://x", source="corpus")
        for n, t in pairs
    ]


def test_loading_makes_a_name_verifiable(temp_db):
    from src.syntax_db.gx_repository import verify_syntax

    assert verify_syntax("Trim(&x)")["verdict"] == "NO_VERIFICABLE"

    load_entries(_entries(("Trim", "function")))

    assert verify_syntax("Trim(&x)")["verdict"] == "VÁLIDO"


def test_loading_twice_does_not_duplicate(temp_db):
    from src.syntax_db.models import GxSyntax, get_session

    load_entries(_entries(("Trim", "function")))
    report = load_entries(_entries(("Trim", "function")))

    session = get_session()
    count = session.query(GxSyntax).filter(GxSyntax.name == "Trim").count()
    session.close()
    assert count == 1
    assert report.skipped == 1
    assert report.inserted == 0


def test_the_same_name_under_two_types_are_two_rows(temp_db):
    # "Call" is a command in api.tsv and a property in properties.tsv.
    from src.syntax_db.models import GxSyntax, get_session

    load_entries(_entries(("Call", "command"), ("Call", "property")))

    session = get_session()
    count = session.query(GxSyntax).filter(GxSyntax.name == "Call").count()
    session.close()
    assert count == 2


def test_loaded_rows_carry_no_version_so_gx16_gets_a_hint(temp_db):
    # The whole reason the verdict work came first: these sources have no
    # version column, and a versionless row asked about GX16 must not say VÁLIDO.
    from src.syntax_db.gx_repository import verify_syntax

    load_entries(_entries(("Abs", "function")))

    assert verify_syntax("Abs(&x)", version="gx16")["verdict"] == "PISTA"
    assert verify_syntax("Abs(&x)", version="gx18")["verdict"] == "VÁLIDO"


def test_a_collision_resolves_to_the_invocable_row(temp_db):
    # A snippet being verified is code being CALLED, so "Call" as a command
    # outranks "Call" as a property. Without an order, .first() returns whichever
    # row was inserted first — a verdict that depends on load order.
    from src.syntax_db.gx_repository import verify_syntax

    load_entries(_entries(("Call", "property"), ("Call", "command")))

    assert verify_syntax("Call(&p)")["object_type"] == "command"


def test_a_word_inside_another_construct_name_is_not_valid(temp_db):
    # «Component» no es un construct: es una palabra dentro del título de otro.
    # Medido 2026-10-05: el LIKE '% n %' devolvía «Refresh Web Component command»
    # y el veredicto salía VÁLIDO, la única afirmación fuerte del sistema.
    from src.syntax_db.gx_repository import verify_syntax

    load_entries(_entries(("Refresh Web Component command", "Command"),
                          ("Refresh Grid event", "Event")))

    for token in ("Component", "Grid"):
        assert verify_syntax(token)["verdict"] != "VÁLIDO", token
    # Con receptor se acepta «Dueño Miembro method/property/event», pero un
    # command no es miembro de nada.
    assert verify_syntax("&wc.Component")["verdict"] != "VÁLIDO"


def test_the_head_word_of_a_qualified_title_is_still_valid(temp_db):
    # Los títulos de la wiki califican el construct después de su nombre:
    # «Add method - SDT Collection» ES el Add de una colección.
    from src.syntax_db.gx_repository import verify_syntax

    load_entries(_entries(("Add method - SDT Collection", "Method"),
                          ("Refresh command in web", "Command")))

    assert verify_syntax("&col.Add(&item)")["name"] == "Add method - SDT Collection"
    assert verify_syntax("Refresh")["verdict"] == "VÁLIDO"
