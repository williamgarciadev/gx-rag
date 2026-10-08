"""Tests de `scripts.retire_source`: sacar de la base un documento reemplazado, sin perderlo.

El 2026-10-07 se hizo a mano tres veces (Cadena de Cierre, Préstamos V3R1.20 y cinco
versiones viejas). Lo que no puede pasar: borrar antes de respaldar, borrar cuando la
versión nueva no entró, o borrar de más por un comodín de LIKE en la URI.
"""

import json

import pytest

from scripts.retire_source import RetireError, retire


class FakeConn:
    """Tabla `chunks` en memoria: (collection, source, article_id, content)."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.log = []

    def execute(self, sql, params):
        self.log.append(sql.split()[0].upper())
        collection, source = params
        match = [r for r in self.rows if r[0] == collection and r[1] == source]
        if sql.lstrip().upper().startswith("DELETE"):
            self.rows = [r for r in self.rows if r not in match]
            return _Cursor([], rowcount=len(match))
        if "count(" in sql:
            return _Cursor([(len(match),)])
        return _Cursor([("collection", "source", "article_id", "content")], data=match)


class _Cursor:
    def __init__(self, rows, rowcount=None, data=None):
        self._rows, self.rowcount, self._data = rows, rowcount, data

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._data if self._data is not None else self._rows

    @property
    def description(self):
        return [(c,) for c in self._rows[0]] if self._data is not None else None


OLD = "file:///F:/docs/a%20b.pdf"
NEW = "file:///F:/docs/SGR/a%20b%20v2.pdf"


@pytest.mark.unit
def test_backs_up_then_deletes_only_the_old_document(tmp_path):
    conn = FakeConn([("bt_docs", OLD, OLD + "#ch=1", "x"), ("bt_docs", OLD, OLD + "#ch=2", "y"),
                     ("bt_docs", NEW, NEW + "#ch=1", "z")])
    result = retire(conn, "bt_docs", OLD, replaced_by=NEW, backup_dir=tmp_path)

    assert result.deleted == 2
    backup = json.loads(result.backup.read_text(encoding="utf-8"))
    assert {row["article_id"] for row in backup} == {OLD + "#ch=1", OLD + "#ch=2"}
    assert [r[1] for r in conn.rows] == [NEW]
    assert conn.log.index("DELETE") > conn.log.index("SELECT")  # respaldo antes del borrado


@pytest.mark.unit
def test_refuses_when_the_replacement_is_not_in_the_base(tmp_path):
    conn = FakeConn([("bt_docs", OLD, OLD + "#ch=1", "x")])
    with pytest.raises(RetireError, match="reemplazo"):
        retire(conn, "bt_docs", OLD, replaced_by=NEW, backup_dir=tmp_path)
    assert len(conn.rows) == 1
    assert "DELETE" not in conn.log


@pytest.mark.unit
def test_refuses_an_old_document_with_no_chunks(tmp_path):
    # Un 0 casi siempre es una ruta mal escrita, no un documento ya retirado.
    conn = FakeConn([("bt_docs", NEW, NEW + "#ch=1", "z")])
    with pytest.raises(RetireError, match="0 chunks"):
        retire(conn, "bt_docs", OLD, replaced_by=NEW, backup_dir=tmp_path)


@pytest.mark.unit
def test_matches_the_source_exactly_never_with_like(tmp_path):
    # '%20' en la URI sería un comodín para LIKE: 'a%20b' casaría con 'aXXb'.
    lookalike = "file:///F:/docs/aXX20b.pdf"
    conn = FakeConn([("bt_docs", OLD, OLD + "#ch=1", "x"), ("bt_docs", lookalike, lookalike + "#ch=1", "w")])
    retire(conn, "bt_docs", OLD, replaced_by=None, backup_dir=tmp_path)
    assert [r[1] for r in conn.rows] == [lookalike]
