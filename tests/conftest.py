"""Shared pytest fixtures for the gx-rag test suite."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app

# ─── PostgreSQL de integración ────────────────────────────────────────────────

TEST_SCHEMA = "gxrag_test"
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
EMBEDDING_DIMS = 1024


# TestClient's default peer is the literal string "testclient", which is not a
# loopback address — and the admin API only serves loopback when no key is
# configured. Presenting as 127.0.0.1 models the local curator these tests are
# about; the auth gate itself is exercised in test_api_auth.py.
LOCAL_PEER = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _no_admin_keys():
    """Reset the admin key set around every test.

    It lives on `app.state` (module-level singleton), so a test that configures
    a key would otherwise leak it into every test that runs afterwards.
    """
    previous = getattr(app.state, "admin_api_keys", frozenset())
    app.state.admin_api_keys = frozenset()
    yield
    app.state.admin_api_keys = previous


@pytest.fixture
def client():
    """FastAPI TestClient bound to the Ingestor API app.

    raise_server_exceptions=False so that unhandled errors are returned as the
    app's 500 response (via the global exception handler) instead of being
    re-raised into the test — letting us assert on the 500 contract.
    """
    with TestClient(
        app, client=LOCAL_PEER, raise_server_exceptions=False
    ) as test_client:
        yield test_client


def _schema_ddl() -> str:
    """DDL de la tabla chunks, leído de los MISMOS .sql que corren en producción.

    Duplicar la definición acá sería garantizar que un día divergen y los tests
    validen contra una tabla que ya no existe. Por el mismo motivo se leen TODOS
    los sql/0*.sql en orden y no solo el 001: si una migración posterior agrega
    una columna, el schema de prueba tiene que tenerla. Solo se filtran las
    meta-órdenes de psql (\\set), que no son SQL.
    """
    archivos = sorted(SQL_DIR.glob("0*.sql"))
    if not archivos:
        raise FileNotFoundError(f"No hay DDL en {SQL_DIR}")
    lineas = [
        l
        for archivo in archivos
        for l in archivo.read_text(encoding="utf-8").splitlines()
    ]
    return "\n".join(l for l in lineas if not l.lstrip().startswith("\\"))


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """DSN de integración, o skip si no hay Postgres a mano.

    Los tests de integración no pueden ser obligatorios: en una máquina sin el
    motor levantado la suite tiene que seguir corriendo, no ponerse roja.
    """
    dsn = os.environ.get("GXRAG_DSN")
    if not dsn:
        pytest.skip("sin GXRAG_DSN: se saltean los tests de integración")
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3):
            pass
    except Exception as exc:  # pragma: no cover - depende del entorno
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    return dsn


@pytest.fixture(scope="session")
def pg_schema(pg_dsn):
    """Crea un schema descartable con la tabla chunks y lo borra al terminar.

    Schema y no base: el rol de la app no tiene CREATEDB, pero sí puede crear
    schemas. Y schema y no "usar la tabla real con cuidado": ya pasó una vez que
    unos tests escribieran en el catálogo de producción, y no vuelve a pasar.
    """
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
        # public queda en el path porque el tipo `vector` de pgvector vive ahí;
        # el schema de prueba va PRIMERO, así "chunks" resuelve al de prueba.
        conn.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        conn.execute(_schema_ddl())

    yield TEST_SCHEMA

    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")


@pytest.fixture
def pg_store(pg_dsn, pg_schema, monkeypatch):
    """Apunta pg_retriever / pg_collections / pg_indexer al schema de prueba.

    Hay que parchear los tres: cada módulo hizo `from ... import get_pool`, así
    que tiene su propia referencia y parchear pg_client no los alcanza.

    Devuelve un helper insert(...) para sembrar filas sin invocar el modelo de
    embeddings — cargarlo cuesta ~7 s y acá lo que se prueba es el SQL.
    """
    from psycopg_pool import ConnectionPool

    from src.vectorstore import pg_collections, pg_indexer, pg_retriever

    pool = ConnectionPool(
        pg_dsn,
        min_size=1,
        max_size=4,
        open=True,
        kwargs={"options": f"-csearch_path={pg_schema},public"},
    )

    # GUARDA DE SEGURIDAD, no es paranoia decorativa.
    # public está en el search_path porque ahí vive el tipo `vector`, y la tabla
    # real `chunks` también. Si el CREATE TABLE del schema de prueba hubiera
    # fallado, "chunks" resolvería a la de producción y el TRUNCATE de abajo se
    # llevaría puestos los 50.082 chunks del corpus. Ya hubo una vez tests
    # escribiendo en datos reales; acá se verifica antes de tocar nada.
    with pool.connection() as conn:
        real = conn.execute(
            "SELECT n.nspname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.oid = 'chunks'::regclass"
        ).fetchone()[0]
    if real != pg_schema:
        pool.close()
        pytest.fail(
            f"ABORTADO: 'chunks' resuelve al schema '{real}', no a '{pg_schema}'. "
            "Se cancela para no tocar datos reales."
        )

    for mod in (pg_retriever, pg_collections, pg_indexer):
        monkeypatch.setattr(mod, "get_pool", lambda: pool)

    def vector(seed: float) -> str:
        """Vector unitario determinístico: todo 0 salvo una posición en 1.

        Sirve para fijar distancias esperadas sin depender del modelo real.
        """
        i = int(seed) % EMBEDDING_DIMS
        vals = ["0"] * EMBEDDING_DIMS
        vals[i] = "1"
        return "[" + ",".join(vals) + "]"

    def insert(chunk_id, content, *, collection="gx_docs", article_id=None,
               chunk_index=0, title="", seed=0, content_hash="H", meta=None):
        """meta: dict de metadatos (sql/003), o None para dejar el default '{}'.

        Aditivo con default: los llamadores previos no lo pasan y siguen valiendo.
        """
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO chunks (chunk_id, collection, article_id, chunk_index,"
                " title, content, source, url, genre, content_hash, embedding, meta)"
                " VALUES (%s,%s,%s,%s,%s,%s,'','','',%s,%s,"
                " COALESCE(%s::jsonb, '{}'::jsonb))",
                (chunk_id, collection, article_id or chunk_id, chunk_index,
                 title, content, content_hash, vector(seed),
                 json.dumps(meta) if meta is not None else None),
            )

    with pool.connection() as conn:
        conn.execute("TRUNCATE chunks")

    yield type("Store", (), {"insert": staticmethod(insert),
                             "vector": staticmethod(vector),
                             "pool": pool})

    pool.close()
