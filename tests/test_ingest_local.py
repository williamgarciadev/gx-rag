"""Tests del CLI `scripts.ingest_local`, el camino que usa la skill rag-ingest.

El 2026-10-05 el script llegó de otra máquina importando `src.vectorstore.indexer`,
un módulo que acá no existe (el indexer es `pg_indexer`), y el dry-run moría con
ModuleNotFoundError antes de leer nada. Un test que importe el módulo y ejercite
el dry-run protege ese contrato.
"""

import pytest

from src.ingestion.bulk_loader import RawDoc


@pytest.mark.unit
def test_dry_run_estimates_chunks_with_the_indexer_chunk_size(capsys):
    from scripts.ingest_local import _dry_run
    from src.vectorstore.pg_indexer import CHUNK_SIZE

    doc = RawDoc(source_key="file:///x.md", title="Doc", content="a" * (CHUNK_SIZE * 3),
                 url="file:///x.md", page_id="")

    _dry_run([doc], collection="bt_docs")

    out = capsys.readouterr().out
    assert "documentos: 1" in out
    assert f"(~{CHUNK_SIZE}c): ~3" in out
    assert "NO se indexa nada" in out
