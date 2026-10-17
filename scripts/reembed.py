#!/usr/bin/env python
"""Re-embeddea una coleccion a otro perfil de geometria, por lotes y reanudable.

POR QUE NO PASA POR EL PIPELINE DE INGESTA. `reindex_if_changed` compara
`content_hash`, que NO cambia cuando cambia el modelo o el perfil de embedding: la
ingesta devolveria "skipped" en las 51.211 filas y no haria nada. Esto tiene que ser
una migracion aparte.

DE DONDE SALE EL TEXTO. De `chunks.content`, que es `text NOT NULL`: el corpus es su
propia fuente. Eso importa porque las fuentes originales de gx_docs NO estan en disco
(`data/scraped/` tiene 11 articulos; `data-corpus.tar.gz` trae el store Chroma viejo,
no los dumps), asi que re-scrapear no es una opcion.

EL RIESGO QUE ADMINISTRA: la ventana de geometria mixta. Mientras el re-embed corre, la
coleccion tiene parte en `raw` y parte en el perfil nuevo, y toda consulta en esa
ventana compara un vector contra una mezcla. Por eso:

  * se migra UNA coleccion por vez, y `hybrid_search` SIEMPRE filtra por coleccion,
    asi que las otras no se contaminan;
  * `emb_profile` se actualiza en la MISMA transaccion que el embedding, asi que el
    estado en la base no puede mentir;
  * `--check` reporta si alguna coleccion quedo mixta, que es la unica pregunta que
    importa antes de creerle a una medicion.

NO TOCA `content` ni `content_hash`: la idempotencia de la ingesta queda intacta.

USO:
    # que hay hoy
    .venv/Scripts/python.exe scripts/reembed.py --check

    # migrar bt_docs (la mas chica) al perfil e5v1
    GXRAG_EMB_PROFILE=e5v1 .venv/Scripts/python.exe scripts/reembed.py \
        --collection bt_docs --to e5v1

El perfil de la env var y el --to tienen que coincidir: el prefijo lo aplica
embeddings.py segun la env var, y --to es lo que se escribe en la columna. Si
difirieran, la columna mentiria sobre la geometria real — se verifica y se aborta.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import dotenv  # noqa: E402

dotenv.load_dotenv(".env", override=False)

import psycopg  # noqa: E402

from src.collection_policy import add_collection_argument  # noqa: E402
from src.vectorstore import embeddings as emb  # noqa: E402

LOTE = 64


def _dsn() -> str:
    return os.environ["GXRAG_DSN"]


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if payload.get("ok", True) else 1


def check() -> int:
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "SELECT collection, emb_profile, count(*) FROM chunks "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )
        filas = cur.fetchall()
    por_coleccion: dict[str, dict[str, int]] = {}
    for col, perfil, n in filas:
        por_coleccion.setdefault(col, {})[perfil] = n
    mixtas = [c for c, p in por_coleccion.items() if len(p) > 1]
    return _emit({
        "ok": not mixtas,
        "command": "check",
        "by_collection": por_coleccion,
        "mixed": mixtas,
        "hint": (
            "Una coleccion con mas de un perfil esta A MEDIO MIGRAR: sus resultados "
            "densos comparan vectores de geometrias distintas y NO son "
            "interpretables. Termina la migracion antes de medir nada."
            if mixtas else
            "Ninguna coleccion mixta: cada una tiene una sola geometria."
        ),
    })


def migrar(coleccion: str, destino: str) -> int:
    if emb.EMB_PROFILE != destino:
        return _emit({
            "ok": False, "command": "reembed",
            "error": f"GXRAG_EMB_PROFILE={emb.EMB_PROFILE!r} pero --to={destino!r}",
            "hint": "El prefijo lo aplica embeddings.py segun la env var, y --to es lo "
                    "que se escribe en la columna. Si difieren, la columna mentiria "
                    "sobre la geometria real de las filas. Pon los dos iguales.",
        })

    t0 = time.perf_counter()
    hechos = 0
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM chunks WHERE collection = %s AND emb_profile <> %s",
                (coleccion, destino),
            )
            pendientes = cur.fetchone()[0]
        if not pendientes:
            return _emit({
                "ok": True, "command": "reembed", "collection": coleccion,
                "to": destino, "pending": 0, "reembedded": 0,
                "next": "python scripts/reembed.py --check",
            })
        print(f"pendientes={pendientes} coleccion={coleccion} -> {destino}",
              file=sys.stderr, flush=True)

        ultimo = ""
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chunk_id, content FROM chunks "
                    "WHERE collection = %s AND emb_profile <> %s AND chunk_id > %s "
                    "ORDER BY chunk_id LIMIT %s",
                    (coleccion, destino, ultimo, LOTE),
                )
                lote = cur.fetchall()
            if not lote:
                break

            textos = tuple(t for _, t in lote)
            vectores = emb.embed_passage(textos)

            # El UPDATE del embedding y la marca del perfil van en la MISMA
            # transaccion: si el proceso muere aca, ninguna fila queda con el vector
            # nuevo y el perfil viejo (ni al reves).
            with conn.transaction(), conn.cursor() as cur:
                for (cid, _), vec in zip(lote, vectores):
                    literal = "[" + ",".join(f"{float(v):.7g}" for v in vec) + "]"
                    cur.execute(
                        "UPDATE chunks SET embedding = %s::vector, emb_profile = %s "
                        "WHERE chunk_id = %s",
                        (literal, destino, cid),
                    )
            hechos += len(lote)
            ultimo = lote[-1][0]
            transcurrido = time.perf_counter() - t0
            resta = (pendientes - hechos) * (transcurrido / hechos)
            print(f"[{hechos}/{pendientes}] {transcurrido/hechos*1000:.0f} ms/chunk "
                  f"resta~{resta/60:.1f} min", file=sys.stderr, flush=True)

    return _emit({
        "ok": True, "command": "reembed", "collection": coleccion, "to": destino,
        "reembedded": hechos, "elapsed_s": round(time.perf_counter() - t0, 1),
        "next": "python scripts/reembed.py --check",
        "hint": "Las tuplas muertas del UPDATE inflan la tabla: correr "
                "VACUUM ANALYZE chunks despues. Y para medir el efecto, "
                "scripts/eval_corpus.py con la MISMA env var de perfil.",
    })


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="reportar perfiles por coleccion")
    add_collection_argument(ap, default=None)
    ap.add_argument("--to", choices=emb.PERFILES)
    args = ap.parse_args(argv)

    if args.check:
        return check()
    if not args.collection or not args.to:
        return _emit({
            "ok": False, "command": "reembed",
            "error": "faltan --collection y --to",
            "hint": "Empieza por --check para ver que hay.",
        })
    return migrar(args.collection, args.to)


if __name__ == "__main__":
    raise SystemExit(main())
