"""
Compara el retriever viejo (ChromaDB + BM25 en memoria) contra el nuevo
(PostgreSQL + pgvector + tsvector), sobre las mismas queries.

Migrar sin comparar es mudarse a ciegas: el conteo de filas puede coincidir y el
ranking igual haberse degradado. Lo que se mide aca es el solapamiento de ids en
el top-K y cuantos coinciden ademas en la misma posicion.

Se corre como modulo desde la raiz del repo, igual que el resto de scripts/:
el -m pone la raiz en sys.path y "src" resuelve. Invocarlo por ruta
(python scripts/compare_retrievers.py) falla con ModuleNotFoundError.

    python -m scripts.compare_retrievers
"""

from __future__ import annotations

import os
import sys
import time

# Importar el paquete carga el .env (ver src/__init__.py). Tiene que pasar antes
# de leer GXRAG_DSN mas abajo. Es barato: src/__init__ no arrastra torch ni chroma
# — por eso los imports pesados siguen adentro de main(), para fallar rapido.
import src  # noqa: F401

# Queries de referencia: dos salen de docs/windows-paso-a-paso.md y el resto
# cubre terminos GeneXus/Bantotal tipicos del corpus.
QUERIES = [
    "Microsoft.Data.SqlClient",
    "Postgresql NOWAIT",
    "For each command",
    "Business Component save",
    "web panel grid load event",
    "data provider",
    "SDT collection",
    "GAM authentication",
]

TOP_K = 5


def main() -> int:
    if not os.environ.get("GXRAG_DSN"):
        print("ERROR: falta GXRAG_DSN en el entorno")
        return 1

    from src.vectorstore import retriever as viejo
    from src.vectorstore import pg_retriever as nuevo

    print(f"{'query':<32} {'solapa':>8} {'mismo_pos':>10} {'chroma_ms':>10} {'pg_ms':>8}")
    print("-" * 74)

    solapes: list[float] = []
    t_chroma: list[float] = []
    t_pg: list[float] = []

    for q in QUERIES:
        t0 = time.time()
        r_viejo = viejo.hybrid_search(q, top_k=TOP_K)
        ms_chroma = (time.time() - t0) * 1000

        t0 = time.time()
        r_nuevo = nuevo.hybrid_search(q, top_k=TOP_K)
        ms_pg = (time.time() - t0) * 1000

        ids_viejo = [r["doc_id"] for r in r_viejo]
        ids_nuevo = [r["doc_id"] for r in r_nuevo]

        comunes = len(set(ids_viejo) & set(ids_nuevo))
        solape = comunes / max(len(ids_viejo), 1) * 100
        misma_pos = sum(1 for a, b in zip(ids_viejo, ids_nuevo) if a == b)

        solapes.append(solape)
        t_chroma.append(ms_chroma)
        t_pg.append(ms_pg)

        print(
            f"{q[:31]:<32} {solape:>7.0f}% {misma_pos:>6}/{len(ids_viejo):<3} "
            f"{ms_chroma:>9.0f} {ms_pg:>7.0f}"
        )

    print("-" * 74)
    n = len(solapes)
    print(f"solapamiento promedio: {sum(solapes)/n:.1f}%")
    print(f"latencia media  chroma: {sum(t_chroma)/n:.0f} ms")
    print(f"latencia media  pg    : {sum(t_pg)/n:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
