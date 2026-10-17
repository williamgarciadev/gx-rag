"""Carga de fichas de objetos de Knowledge Base en la coleccion de KB.

Espeja bulk_loader.load_dump, con una diferencia de contrato deliberada: el reporte es
POR FICHA y no solo agregado. El llamador tipico es un LLM que acaba de navegar 12
objetos y necesita saber CUAL de los 12 no entro y por que — un contador de errores no
le sirve para actuar.

DOS CONSUMIDORES, una implementacion:
  scripts/bootstrap_kb.py   -> carga masiva inicial, NO pasa por MCP (importa esto
                               directo, como scripts/ingest_dump.py importa load_dump)
  server.ingest_kb_objects  -> adaptador MCP delgado para el flujo incremental
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from src.collection_policy import KB_COLLECTION
from src.ingestion import kb_fichas as kf

# Tope de fichas por llamada MCP. Derivacion: una ficha de 4 secciones ~ 2,5 KB de
# JSON, asi que 25 fichas ~ 62 KB de argumentos, que ya es mucho para que un LLM emita
# en un turno; y del otro lado son ~75 chunks a embeddear, del orden de 5-10 s con
# e5-large en CPU antes de que la llamada le parezca colgada.
#
# El tope NO aplica al bootstrap, que no pasa por MCP: ahi el limite es el lote que
# elija el script.
KB_MAX_INLINE = 25


@dataclass
class FichaResult:
    """Resultado de UNA ficha. `article_id` es None si no se pudo derivar."""

    index: int
    article_id: str | None
    status: str  # indexed | reindexed | skipped | invalid | error
    chunks: int = 0
    kinds: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class KbLoadReport:
    """Resultado de una corrida. Agregados + detalle por ficha."""

    results: list[FichaResult] = field(default_factory=list)
    dry_run: bool = True
    kb_name: str = ""
    # Fichas sin kb_hash: su idempotencia queda colgada de que el generador de prosa
    # sea deterministico. Se cuentan para poder AVISARLO en vez de dejarlo silencioso.
    sin_kb_hash: int = 0

    @property
    def total(self) -> int:
        return len(self.results)

    def contar(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def hubo_errores(self) -> bool:
        return any(r.status in ("invalid", "error") for r in self.results)


def load_fichas(
    fichas: Iterable[dict],
    *,
    kb_name: str,
    dry_run: bool = True,
    on_invalid: str = "stop",
    index_fn: Callable[..., str] | None = None,
    resolve_fn: Callable[[list[str]], dict[str, str]] | None = None,
    on_progress: Callable[[int, str, str], None] | None = None,
) -> KbLoadReport:
    """Indexa fichas de forma idempotente. Devuelve el detalle por ficha.

    dry_run=True (default) NO es un peaje: es una CONSULTA. Resuelve, con un solo
    SELECT y sin embeddear nada, cuales de las fichas son realmente nuevas. En el flujo
    incremental el agente acaba de navegar N objetos y tipicamente la mayoria no
    cambio; el dry run se lo dice en milisegundos en vez de re-embeddear al voleo.

    on_invalid='stop' (default) no escribe NADA si alguna ficha no valida. En un lote,
    seguir de largo deja la coleccion con agujeros que despues nadie distingue de
    objetos que la KB no tiene.
    """
    if index_fn is None:
        from src.vectorstore.pg_indexer import reindex_if_changed

        index_fn = reindex_if_changed
    if resolve_fn is None:
        resolve_fn = _hashes_indexados

    fichas = list(fichas)
    reporte = KbLoadReport(dry_run=dry_run, kb_name=kb_name)

    # ── 1. Validar TODO antes de escribir nada ────────────────────────────────
    validadas: list[tuple[int, dict]] = []
    for i, ficha in enumerate(fichas):
        problemas = kf.validate_ficha(ficha)
        if problemas:
            reporte.results.append(
                FichaResult(
                    index=i,
                    # Sin obj_name/obj_type no hay article_id que mostrar, y por eso el
                    # llamador identifica la ficha por su INDICE en el array: es lo
                    # unico que puede mapear a lo que mando.
                    article_id=_article_id_seguro(ficha, kb_name),
                    status="invalid",
                    reason="; ".join(problemas),
                )
            )
            continue
        validadas.append((i, ficha))

    if on_invalid == "stop" and reporte.hubo_errores:
        return reporte

    if not validadas:
        return reporte

    # ── 2. Un solo SELECT para saber que hay indexado ─────────────────────────
    ids = [kf.ficha_article_id(f, kb_name=kb_name) for _, f in validadas]
    try:
        hashes = resolve_fn(ids)
    except Exception:
        # Si no se puede consultar el store, el dry run no puede prometer nada. Se
        # sigue sin la optimizacion: reindex_if_changed vuelve a comparar por su
        # cuenta, asi que la correctitud no depende de esto.
        hashes = {}

    # ── 3. Resolver o escribir, ficha por ficha ───────────────────────────────
    for i, ficha in validadas:
        article_id = kf.ficha_article_id(ficha, kb_name=kb_name)
        content_hash = kf.ficha_content_hash(ficha, kb_name=kb_name)
        if not ficha.get("kb_hash"):
            reporte.sin_kb_hash += 1

        previo = hashes.get(article_id)
        if previo == content_hash:
            reporte.results.append(
                FichaResult(index=i, article_id=article_id, status="skipped")
            )
            if on_progress:
                on_progress(i, article_id, "skipped")
            continue

        chunks = kf.ficha_to_chunks(ficha, kb_name=kb_name)
        kinds = tuple(dict.fromkeys(k for k, _ in chunks))

        if dry_run:
            status = "would_reindex" if previo is not None else "would_index"
            reporte.results.append(
                FichaResult(i, article_id, status, len(chunks), kinds)
            )
            if on_progress:
                on_progress(i, article_id, status)
            continue

        try:
            status = index_fn(
                article_id,
                kf.ficha_title(ficha),
                # `content` no se usa cuando se pasan chunks ya partidos, pero
                # reindex_if_changed lo recibe posicionalmente: se manda el texto
                # concatenado para que un futuro cambio que vuelva al splitter no
                # indexe vacio.
                "\n\n".join(t for _, t in chunks),
                "",  # source
                "guide",  # genre
                "",  # url: un objeto de KB no tiene URL citable
                content_hash,
                KB_COLLECTION,
                meta=kf.ficha_meta(ficha, kb_name=kb_name),
                chunks=chunks,
            )
            reporte.results.append(
                FichaResult(i, article_id, status, len(chunks), kinds)
            )
        except Exception as exc:  # una ficha que falla no aborta el resto
            reporte.results.append(
                FichaResult(i, article_id, "error", reason=f"{type(exc).__name__}: {exc}")
            )
        if on_progress:
            on_progress(i, article_id, reporte.results[-1].status)

    return reporte


def _article_id_seguro(ficha: object, kb_name: str) -> str | None:
    """article_id si se puede derivar; None si la ficha no tiene identidad."""
    if not isinstance(ficha, dict):
        return None
    if not ficha.get("obj_name") or not ficha.get("obj_type"):
        return None
    return kf.ficha_article_id(ficha, kb_name=kb_name)


def _hashes_indexados(article_ids: list[str]) -> dict[str, str]:
    """{article_id: content_hash} de lo ya indexado. UN solo SELECT, sin embeddear.

    Es lo que convierte el dry run en una consulta util. DISTINCT porque el hash es
    identico en todos los chunks de un articulo (invariante documentada en
    reindex_if_changed) — si alguna vez dejara de serlo, esto devolveria dos filas por
    articulo y habria que revisar aquella invariante, no este SELECT.
    """
    from src.vectorstore.pg_client import get_pool

    if not article_ids:
        return {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT article_id, content_hash FROM chunks "
            "WHERE collection = %s AND article_id = ANY(%s)",
            (KB_COLLECTION, article_ids),
        )
        return {r[0]: r[1] for r in cur.fetchall()}
