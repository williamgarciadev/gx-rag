"""Loading the gold set.

Same discipline as the ingestion manifest: every entry has to state what it
expects and why, because a case with no ground truth is unscorable and a case
with no rationale is unmaintainable — nobody later can tell whether a miss is a
retrieval bug or a badly-written question.
"""

import json

import pytest

from src.evaluation.gold import GoldCase, load_gold

pytestmark = pytest.mark.unit


def _write(tmp_path, *cases):
    path = tmp_path / "gold.jsonl"
    path.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases), encoding="utf-8"
    )
    return path


def _case(**over):
    case = {
        "question": "¿Qué es el API object?",
        "collection": "gx_docs",
        "expected": ["wiki?46151"],
        "note": "artículo canónico del objeto",
    }
    case.update(over)
    return case


def test_loads_a_case(tmp_path):
    (case,) = load_gold(_write(tmp_path, _case()))

    assert case == GoldCase(
        question="¿Qué es el API object?",
        collection="gx_docs",
        expected=frozenset({"wiki?46151"}),
        note="artículo canónico del objeto",
    )


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text(
        json.dumps(_case()) + "\n\n\n", encoding="utf-8"
    )

    assert len(load_gold(path)) == 1


def test_a_case_without_expected_documents_is_rejected(tmp_path):
    # Unscorable: recall over an empty ground truth is undefined, and letting it
    # through would silently drop a question from the suite.
    with pytest.raises(ValueError, match="expected"):
        load_gold(_write(tmp_path, _case(expected=[])))


def test_a_case_without_a_note_is_rejected(tmp_path):
    # A miss must be diagnosable months later: bad retrieval, or bad question?
    with pytest.raises(ValueError, match="note"):
        load_gold(_write(tmp_path, _case(note="")))


def test_the_failing_line_number_is_reported(tmp_path):
    path = _write(tmp_path, _case(), _case(expected=[]))

    with pytest.raises(ValueError, match="line 2"):
        load_gold(path)


def test_multiple_expected_documents_are_allowed(tmp_path):
    # An answer legitimately split across two manuals or two wiki pages.
    (case,) = load_gold(_write(tmp_path, _case(expected=["a", "b"])))

    assert case.expected == frozenset({"a", "b"})


def test_a_case_without_filters_has_none(tmp_path):
    (case,) = load_gold(_write(tmp_path, _case()))

    assert case.filters is None


def test_a_case_carries_its_filters(tmp_path):
    (case,) = load_gold(_write(tmp_path, _case(filters={"origin": "gx_wiki"})))

    assert case.filters == {"origin": "gx_wiki"}


def test_filters_that_are_not_an_object_are_rejected(tmp_path):
    # Un "filters": "gx_wiki" mal escrito no puede convertirse en una búsqueda
    # sin filtro que el caso cree filtrada.
    with pytest.raises(ValueError, match="filters"):
        load_gold(_write(tmp_path, _case(filters="gx_wiki")))
