"""La columna `meta` y su CHECK por colección (sql/003-chunk-meta.sql).

QUE PROTEGE: que los chunks de una Knowledge Base no puedan entrar sin identidad de
objeto y de KB. Sin esa barrera, un chunk con meta='{}' es invisible a todo filtro, y
"el filtro no matcheó nada" se lee igual que "la KB no tiene transacciones": una
ausencia que nadie verificó. Y `kb_name` es lo que permite tener MAS DE UNA KB en la
misma colección sin mezclarlas — mezclarlas es peor que no tener RAG, porque el
agente escribiría código con nombres de objetos que no existen en la KB activa.

La barrera está en el MOTOR y no en el código a propósito: una ingesta mal ruteada
tiene que fallar ruidosamente, no dejar filas no filtrables.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

KB_COLLECTION = "gx_kb_objects"

META_COMPLETA = {
    "obj_name": "PrcMayorizar",
    "obj_type": "Procedure",
    "chunk_kind": "logic",
    "kb_name": "MatikaErp_3003",
    "module": "CentralizacionContable",
}


def test_meta_defaults_to_empty_for_the_doc_collections(pg_store):
    """Las 4 colecciones de documentación no llevan metadatos y siguen entrando."""
    pg_store.insert("doc_c0", "comando for each", collection="gx_docs")

    with pg_store.pool.connection() as conn:
        meta = conn.execute(
            "SELECT meta FROM chunks WHERE chunk_id = 'doc_c0'"
        ).fetchone()[0]

    assert meta == {}


def test_a_kb_chunk_with_full_identity_is_accepted(pg_store):
    pg_store.insert(
        "kb_c0", "For each Cliente ...", collection=KB_COLLECTION, meta=META_COMPLETA
    )

    with pg_store.pool.connection() as conn:
        meta = conn.execute(
            "SELECT meta FROM chunks WHERE chunk_id = 'kb_c0'"
        ).fetchone()[0]

    assert meta["obj_name"] == "PrcMayorizar"
    assert meta["kb_name"] == "MatikaErp_3003"


def test_the_check_rejects_a_kb_chunk_without_object_identity(pg_store):
    """meta vacío en la colección de KB tiene que fallar EN EL MOTOR."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        pg_store.insert("kb_malo_c0", "texto", collection=KB_COLLECTION)


@pytest.mark.parametrize("faltante", sorted(META_COMPLETA.keys() - {"module"}))
def test_the_check_requires_every_identity_key(pg_store, faltante):
    """Las 4 claves de identidad son obligatorias, una por una.

    `module` NO está en la lista: es `observed:48.6` en el índice del MCP (la mitad
    de la KB sin placement resuelto), así que exigirlo rechazaría objetos legítimos.
    """
    import psycopg

    incompleta = {k: v for k, v in META_COMPLETA.items() if k != faltante}

    with pytest.raises(psycopg.errors.CheckViolation):
        pg_store.insert(
            f"kb_sin_{faltante}_c0", "texto", collection=KB_COLLECTION, meta=incompleta
        )


def test_module_is_optional_because_the_kb_index_often_lacks_it(pg_store):
    sin_module = {k: v for k, v in META_COMPLETA.items() if k != "module"}

    pg_store.insert("kb_sin_mod_c0", "texto", collection=KB_COLLECTION, meta=sin_module)

    with pg_store.pool.connection() as conn:
        fila = conn.execute(
            "SELECT meta->>'obj_name' FROM chunks WHERE chunk_id = 'kb_sin_mod_c0'"
        ).fetchone()

    assert fila[0] == "PrcMayorizar"


def test_the_expression_indexes_exist_for_every_filtered_key(pg_store):
    """btree de EXPRESION, no GIN.

    Medido sobre 3.140 filas sembradas: con el btree, `meta->>'obj_type' = ANY(...)`
    resuelve por `Bitmap Index Scan on chunks_meta_obj_type`. Reemplazándolo por un
    GIN de jsonb_ops sobre `meta`, el mismo filtro cae a `Seq Scan` — un GIN sirve
    @>, ?, ?| sobre el nivel superior, NO la igualdad sobre `->>`.
    """
    esperados = {
        "chunks_meta_kb_name",
        "chunks_meta_obj_type",
        "chunks_meta_chunk_kind",
        "chunks_meta_module",
        "chunks_meta_obj_name",
        "chunks_meta_origin",
        "chunks_meta_related_gin",
    }

    with pg_store.pool.connection() as conn:
        presentes = {
            r[0]
            for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'chunks'"
            ).fetchall()
        }

    assert esperados <= presentes, f"faltan índices: {esperados - presentes}"
