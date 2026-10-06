"""El harness de evaluación contra el set gold (`scripts/eval_retrieval.py`).

El 2026-10-05 llegó de otra máquina importando `src.vectorstore.retriever`, un
módulo que acá no existe (el retriever es `pg_retriever`): moría antes de medir
nada, y sin él ningún cambio de retrieval se puede comparar contra la línea base.
"""

import json

import pytest

pytestmark = pytest.mark.unit


def test_harness_runs_the_gold_set_through_the_real_retriever(tmp_path, monkeypatch):
    from scripts import eval_retrieval
    from src.vectorstore import pg_retriever

    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"question": "¿Qué es el API object?",
                                "collection": "gx_docs",
                                "expected": ["wiki?46151"],
                                "note": "caso de prueba"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(pg_retriever, "hybrid_search",
                        lambda query, collection="gx_docs", top_k=5, **kw:
                            [{"id": "otro"}, {"id": "wiki?46151"}])

    _, results, report = eval_retrieval._load_and_run(gold, top_k=5, ks=(1, 5))

    assert results[0].rank == 2
    assert report.recall[5] == 1.0
