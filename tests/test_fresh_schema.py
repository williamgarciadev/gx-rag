"""Un install que aplica SOLO el 001 tiene la mitad lexica funcionando.

POR QUE EXISTE ESTE ARCHIVO Y NO UN TEST EN test_pg_store.py: la fixture pg_schema
concatena TODOS los sql/0*.sql, asi que por construccion no puede detectar que el
001 solo sea incorrecto. El bug vivia exactamente en ese punto ciego.

EL BUG: `tsv` se generaba con la config 'simple' en el 001 y el 002 la migraba a
'es_simple', que es la que consulta pg_retriever.py. Aplicar los dos en orden da un
resultado correcto — pero el comando del header del 001 se presenta como
autosuficiente, y un install que corriera solo ese archivo quedaba con la mitad
lexica del RRF devolviendo CERO filas para cualquier consulta con tilde. Sin error
y sin aviso: la busqueda hibrida degeneraba a densa pura y parecia andar.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.integration

SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"
SOLO_001_SCHEMA = "gxrag_test_solo_001"


def _ddl_de(nombre: str) -> str:
    """DDL de UN archivo, sin las meta-ordenes de psql (\\set)."""
    texto = (SQL_DIR / nombre).read_text(encoding="utf-8")
    return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("\\"))


@pytest.fixture
def schema_solo_001(pg_dsn):
    """Schema descartable con UNICAMENTE el 001 aplicado."""
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SOLO_001_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SOLO_001_SCHEMA}")
        conn.execute(f"SET search_path TO {SOLO_001_SCHEMA}, public")
        conn.execute(_ddl_de("001-chunks-schema.sql"))

    yield SOLO_001_SCHEMA

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SOLO_001_SCHEMA} CASCADE")


def test_the_generated_tsv_uses_the_config_the_retriever_queries_with(
    pg_dsn, schema_solo_001
):
    """La config de `tsv` tiene que ser LA MISMA que usa to_tsquery en el retriever.

    Se compara la expresion de la columna generada porque es la unica fuente de
    verdad: PostgreSQL la guarda resuelta a OID, y pg_get_expr la reimprime con el
    nombre real, no con el que se escribio en el DDL.
    """
    import psycopg

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_solo_001}, public")
        cur.execute(
            """SELECT pg_get_expr(d.adbin, d.adrelid)
                 FROM pg_attrdef d
                 JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                WHERE d.adrelid = 'chunks'::regclass AND a.attname = 'tsv'"""
        )
        expr = cur.fetchone()[0]

    assert "es_simple" in expr, (
        f"tsv se genera con otra config que la que consulta el retriever: {expr}. "
        "La mitad lexica del RRF devolveria cero filas."
    )


def test_an_accented_document_answers_an_unaccented_query(pg_dsn, schema_solo_001):
    """El comportamiento que el usuario nota: la gente escribe sin tildes.

    Medido sobre bt_docs cuando el bug estaba vivo (ver sql/002):
        to_tsquery('simple', 'autorizacion') ->  15 hits
        to_tsquery('simple', 'autorización') ->  96 hits
    """
    import psycopg

    vector_cero = "[" + ",".join(["0"] * 1024) + "]"

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_solo_001}, public")
        cur.execute(
            """INSERT INTO chunks
                   (chunk_id, collection, article_id, chunk_index, title, content,
                    source, url, genre, content_hash, embedding)
               -- El titulo NO puede contener la palabra buscada sin tilde: `tsv`
               -- indexa title||content, asi que un titulo "Autorizacion" daria el
               -- match por si solo y el test pasaria incluso con el bug vivo.
               VALUES ('a_c0', 'bt_docs', 'a', 0, 'Proceso de credito',
                       'El paso de autorización requiere validación previa.',
                       '', '', '', 'h', %s::vector)""",
            (vector_cero,),
        )
        cur.execute(
            """SELECT count(*) FROM chunks, to_tsquery('es_simple', %s) q
                WHERE tsv @@ q""",
            ("autorizacion",),
        )
        sin_tilde = cur.fetchone()[0]
        conn.rollback()

    assert sin_tilde == 1, (
        "una consulta sin tilde no encontro el documento con tilde: el tsvector "
        "no esta normalizando diacriticos"
    )
