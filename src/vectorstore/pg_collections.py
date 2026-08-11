"""
Acceso a documentos y articulos sobre PostgreSQL.

Reemplaza a collections.py (ChromaDB). Mismo contrato: get_document() devuelve un
chunk por su id y get_article() reensambla el articulo completo a partir de sus
chunks. La diferencia es que el reensamblado lo hace el motor con string_agg en
vez de traer las filas y ordenarlas en Python.
"""

from __future__ import annotations

from typing import Any

from src.vectorstore.pg_client import get_pool

_DOCUMENT_SQL = """
    SELECT chunk_id, content, title, source, url, content_hash, collection
    FROM chunks
    WHERE chunk_id = %(chunk_id)s AND collection = %(collection)s
"""

# El ORDER BY dentro de string_agg garantiza que los chunks se concatenen en el
# orden del articulo y no en el que el motor decida devolverlos.
_ARTICLE_SQL = """
    SELECT
        string_agg(content, E'\\n\\n' ORDER BY chunk_index) AS content,
        min(title)        AS title,
        min(source)       AS source,
        min(url)          AS url,
        min(content_hash) AS content_hash,
        count(*)          AS chunks
    FROM chunks
    WHERE article_id = %(article_id)s AND collection = %(collection)s
"""


def get_document(doc_id: str, collection: str = "gx_docs") -> dict[str, Any] | None:
    """Obtiene un chunk por su id. None si no existe.

    None significa UNA sola cosa: no hay tal fila. Los errores del motor PROPAGAN
    a proposito. Antes se capturaban y se devolvia None, y como None es tambien
    como se escribe "no existe", una caida de Postgres se le presentaba al agente
    como "documento no encontrado" — una afirmacion de inexistencia que nadie
    verifico. El llamador ya distingue: dispatch_tool lo convierte en error
    redactado y la API admin en 500.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DOCUMENT_SQL, {"chunk_id": doc_id, "collection": collection})
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "content": row[1],
        "title": row[2],
        "source": row[3],
        "url": row[4],
        "content_hash": row[5],
        "collection": row[6],
    }


def get_article(article_id: str, collection: str = "gx_docs") -> dict[str, Any] | None:
    """Reensambla un articulo completo a partir de sus chunks.

    search_docs y get_document trabajan a nivel de chunk; un agente que necesita
    fundamentar una respuesta quiere el documento entero.

    Los errores del motor propagan, por el mismo motivo que en get_document: None
    tiene que significar "no hay tal articulo" y nada mas.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            _ARTICLE_SQL, {"article_id": article_id, "collection": collection}
        )
        row = cur.fetchone()

    # La agregacion siempre devuelve una fila: sin chunks, count(*) es 0.
    if row is None or row[5] == 0:
        return None

    return {
        "id": article_id,
        "content": row[0],
        "title": row[1],
        "source": row[2],
        "url": row[3],
        "content_hash": row[4],
        "collection": collection,
        "chunks": row[5],
    }


def collection_exists(name: str) -> bool:
    """True si la coleccion tiene al menos un chunk."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM chunks WHERE collection = %s)", (name,))
        return bool(cur.fetchone()[0])


def list_collections() -> list[str]:
    """Lista las colecciones con contenido."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT collection FROM chunks ORDER BY collection")
        return [r[0] for r in cur.fetchall()]


def collection_counts() -> list[tuple[str, int]]:
    """Colecciones con su cantidad de chunks, la mas poblada primero.

    Alimenta el hint de search_docs cuando una busqueda no devuelve nada: la
    causa mas comun es haber apuntado a una coleccion vacia. Con ChromaDB esto
    era un N+1 (listar y despues contar una por una); aca es un GROUP BY.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT collection, count(*) FROM chunks "
            "GROUP BY collection ORDER BY count(*) DESC"
        )
        return [(r[0], r[1]) for r in cur.fetchall()]
