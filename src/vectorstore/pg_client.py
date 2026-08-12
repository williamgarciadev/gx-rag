"""
Cliente PostgreSQL — pool de conexiones sobre psycopg3.

Reemplaza a chroma_client.get_client(). Misma idea de singleton perezoso, pero
con un pool: el servidor HTTP atiende pedidos concurrentes y abrir una conexion
por query es caro.
"""

from __future__ import annotations

import logging
import os

from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

# El DSN sale del entorno: nunca hardcodear credenciales en el codigo.
DSN_ENV = "GXRAG_DSN"


def get_pool() -> ConnectionPool:
    """Devuelve el pool de conexiones, creandolo la primera vez."""
    global _pool
    if _pool is None:
        dsn = os.environ.get(DSN_ENV)
        if not dsn:
            raise RuntimeError(
                f"falta {DSN_ENV} en el entorno "
                "(ej. postgresql://gxrag:...@localhost:5433/gxrag)"
            )
        # open=True conecta al construir: si las credenciales estan mal, falla
        # al arrancar y no en la primera consulta del usuario.
        _pool = ConnectionPool(dsn, min_size=1, max_size=8, open=True)
        log.info("pool de PostgreSQL inicializado")
    return _pool


def close_pool() -> None:
    """Cierra el pool. Para tests y apagado ordenado."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
