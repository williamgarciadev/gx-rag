"""
Hybrid retriever — dense (ChromaDB) + BM25, fused with Reciprocal Rank Fusion.

Cross-encoder reranking is intentionally NOT wired yet: the configured
`cross-encoder/ms-marco-MiniLM-L-6-v2` is English-trained while the corpus is
Spanish, so it would likely hurt ranking. Reranking is a separate decision.
"""

import logging
import re
import time
from collections import Counter

from rank_bm25 import BM25Okapi

from src.paths import CHROMA_PATH
from src.vectorstore import embeddings as emb
from src.vectorstore.chroma_client import get_client

log = logging.getLogger(__name__)

RRF_K = 60  # standard Reciprocal Rank Fusion damping constant
CANDIDATE_POOL = 30  # candidates pulled from each retriever before fusion
# Page size for full-collection scans. ChromaDB's backend binds SQL variables
# per row, so a single unbounded col.get() over a large collection overflows
# SQLite's variable limit ("too many SQL variables"). Fetch in pages instead.
_GET_PAGE_SIZE = 5000
# Chunks kept per source article. A manual chapter runs to dozens of chunks, and
# without a cap they take the whole result set: "cómo se calcula el interés de
# mora" returned MDI-10201 §1.10.1.1 three times at top_k=3, burying every other
# manual that had an answer. Two leaves room for an answer split across a chunk
# boundary while still surfacing other sources; an agent that wants the rest of
# the chapter calls get_document with the doc_id.
_MAX_CHUNKS_PER_ARTICLE = 2

# Cache: collection name -> (cache_key, bm25_index, ids, docs, metas).
# Rebuilt when the key changes — see _cache_key on why the document count alone
# is not a sufficient signal.
_bm25_cache: dict[str, tuple] = {}


def _tokenize(text: str) -> list[str]:
    """Lowercase word-token split — good enough for BM25 lexical matching."""
    return re.findall(r"\w+", text.lower())


def _get_all(col) -> tuple[list[str], list[str], list[dict]]:
    """Fetch a whole collection's ids/documents/metadatas via paged reads.

    An unbounded col.get() overflows the backend's SQL variable limit once the
    collection grows large, so scan it in fixed-size pages and concatenate.
    """
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    offset = 0
    while True:
        page = col.get(
            include=["documents", "metadatas"],
            limit=_GET_PAGE_SIZE,
            offset=offset,
        )
        page_ids = page.get("ids") or []
        if not page_ids:
            break
        ids.extend(page_ids)
        docs.extend(page.get("documents") or [])
        metas.extend(page.get("metadatas") or [])
        offset += _GET_PAGE_SIZE
    return ids, docs, metas


# Marker whose contents change on every corpus write. Lives beside the store so
# it survives a restart and is visible to OTHER processes — locally, ingestion
# runs as a separate CLI while the MCP server holds its cache in memory.
_VERSION_FILE = CHROMA_PATH / ".corpus-version"


def bump_corpus_version() -> None:
    """Announce that the corpus changed. Called by the indexer after any write.

    The WRITER announces because only the writer can do it cheaply. Having the
    reader detect changes instead means scanning the collection on every query:
    a first version of this hashed each chunk's (id, content_hash) and cost
    3.5s per query on gx_docs against a 0.22s warm budget — a detection that
    costs more than the rebuild it avoids is not a cache, it is a tax.
    """
    try:
        _VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VERSION_FILE.write_text(str(time.time_ns()), encoding="utf-8")
    except OSError as exc:
        # Never let a write fail because the marker could not be updated. The
        # cost of missing it is a stale BM25 index until the next count change.
        log.warning("Could not stamp the corpus version: %s", exc)


def _corpus_version() -> str:
    """Current corpus version, or "" when nothing has been written yet."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _cache_key(col) -> tuple[int, str]:
    """Signal for whether the cached BM25 index is still valid.

    Two cheap parts, and both are needed:
      - `count` catches adds and deletes even from a writer that bypasses the
        indexer, and it is already read on every search.
      - `corpus_version` catches a REWRITE, which is the case `count` misses:
        reindex_if_changed deletes an article's chunks and re-adds them under
        the same deterministic ids, leaving the count identical while the text
        underneath changed completely.
    """
    return col.count(), _corpus_version()


def _get_bm25(col):
    """Build (and cache) a BM25 index over the whole collection."""
    name = col.name
    key = _cache_key(col)
    cached = _bm25_cache.get(name)
    if cached and cached[0] == key:
        return cached[1], cached[2], cached[3], cached[4]

    ids, docs, metas = _get_all(col)
    bm25 = BM25Okapi([_tokenize(d) for d in docs]) if docs else None

    _bm25_cache[name] = (key, bm25, ids, docs, metas)
    return bm25, ids, docs, metas


def _dense_ranking(col, query: str) -> list[str]:
    """Return candidate document ids ranked by dense similarity (best first)."""
    query_embedding = emb.embed(query)
    results = col.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=CANDIDATE_POOL,
        include=["documents", "metadatas", "distances"],
    )
    return (results.get("ids") or [[]])[0]


def _bm25_ranking(col, query: str) -> tuple[list[str], dict[str, tuple]]:
    """Return candidate ids ranked by BM25 plus an id -> (doc, meta) lookup."""
    bm25, ids, docs, metas = _get_bm25(col)
    lookup = {i: (d, m) for i, d, m in zip(ids, docs, metas)}
    if bm25 is None:
        return [], lookup

    scores = bm25.get_scores(_tokenize(query))
    ranked = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
    return [ids[i] for i in ranked[:CANDIDATE_POOL]], lookup


def _reciprocal_rank_fusion(*rankings: list[str]) -> dict[str, float]:
    """Fuse ranked id lists into a single id -> RRF score map."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    return fused


def hybrid_search(
    query: str,
    collection: str = "gx_docs",
    top_k: int = 5,
    min_score: float | None = None,
) -> list[dict]:
    """
    Hybrid search: dense embeddings + BM25, combined with Reciprocal Rank Fusion.

    RRF is rank-based, so it fuses the two retrievers without needing to
    reconcile their incompatible score scales (L2 distance vs BM25).

    min_score, if given, drops results whose fused score is below it. NOTE: the
    RRF score is rank-based and NOT calibrated (not a probability); use min_score
    as a coarse rank floor, not a confidence threshold.
    """
    client = get_client()
    col = client.get_or_create_collection(name=collection)

    if col.count() == 0:
        return []

    dense_ids = _dense_ranking(col, query)
    bm25_ids, lookup = _bm25_ranking(col, query)

    fused = _reciprocal_rank_fusion(dense_ids, bm25_ids)
    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

    output = []
    per_article: Counter[str] = Counter()
    for doc_id, score in ordered:
        if len(output) >= top_k:
            break
        if min_score is not None and score < min_score:
            break  # scores descend, so nothing further can qualify either
        doc, meta = lookup.get(doc_id, (None, None))
        meta = meta or {}
        # Walk past extra chunks of an article already represented, rather than
        # stopping: the next distinct source may be further down the ranking.
        article = str(meta.get("id", doc_id))
        if per_article[article] >= _MAX_CHUNKS_PER_ARTICLE:
            continue
        per_article[article] += 1
        output.append(
            {
                # doc_id is the ChromaDB chunk id — the key get_document() accepts.
                "doc_id": doc_id,
                # id is the article id from metadata, for grouping/provenance.
                "id": meta.get("id", doc_id),
                "title": meta.get("title", ""),
                "content": doc,
                "source": meta.get("source", ""),
                # url + content_hash let a consuming agent cite without a 2nd call.
                "url": meta.get("url", ""),
                "content_hash": meta.get("content_hash", ""),
                # Manual provenance ("MDI-10201 p.121, §1.16.1.1"). Empty for
                # wiki chunks, which have no manual or page to cite.
                "manual": meta.get("manual", ""),
                "page": meta.get("page", ""),
                "section": meta.get("section", ""),
                "score": round(score, 6),
            }
        )
    return output
