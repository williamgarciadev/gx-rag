"""CLI to scrape the GeneXus SAC knowledge base (sac.genexus.com) by article id.

The SAC front-end at sac.genexus.com/frontend.scmain.aspx is server-rendered
(unlike the auth-gated, JS-rendered www.genexus.com/websac). Each article has a
numeric id; the article body lives in `td#CONTENIDO`, which scrape_wiki.scrape()
already extracts and cleans.

Modes:
    # explicit ids
    python -m scripts.scrape_sac ids 61036 61037 --dry-run

    # inclusive range
    python -m scripts.scrape_sac range 61000 61050

    # auto-sweep up AND down from a start id until N consecutive misses/direction
    python -m scripts.scrape_sac sweep 49983 --max-misses 50

--dry-run scrapes and reports WITHOUT writing files or indexing — a preview
gate. Real runs persist .md + .meta.json to data/scraped/ and index idempotently
into ChromaDB (keyed by a unique per-article id, so re-runs never collide).

Empty/login pages (word_count <= --min-words) are skipped; a nonexistent or
login-gated article renders only chrome and never reaches the corpus.
"""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts.scrape_wiki import scrape
from src.ingestion.ingestor import save_article
from src.vectorstore.pg_indexer import reindex_if_changed

log = logging.getLogger("scrape_sac")

DEFAULT_CONCURRENCY = 1  # sequential; >1 fetches in parallel, indexes serially

# Global request spacer: fetches are spaced >= `delay` seconds apart across ALL
# worker threads, so concurrency hides latency without raising the request rate
# beyond 1/delay — courtesy to the third-party SAC server.
_rate_lock = threading.Lock()
_next_ts = [0.0]


def _rate_limit(delay: float) -> None:
    if delay <= 0:
        return
    with _rate_lock:
        now = time.monotonic()
        wait = _next_ts[0] - now
        if wait > 0:
            time.sleep(wait)
        _next_ts[0] = max(now, _next_ts[0]) + delay


# Portal parameter prefix for the SAC main panel; the numeric id follows.
SAC_URL_TEMPLATE = (
    "https://sac.genexus.com/frontend.scmain.aspx"
    "?S%3B178%3B94%3B1264%3B1%3B1%3BO%3B,{id};;"
)

DEFAULT_MIN_WORDS = 70  # login=47, empty=61; real content is well above
DEFAULT_MAX_MISSES = 50  # consecutive empties/errors that end a sweep direction
DEFAULT_DELAY = 1.0  # polite pause between requests (seconds)

# SAC entries are support tickets (Q&A), never syntax reference. Route them all
# to the retrieval collection so a default search_docs finds them — regardless of
# what classify_genre() guessed. gx_syntax is reserved for real GeneXus reference.
SAC_COLLECTION = "gx_docs"

# Outcomes that mean "an article exists here" (a sweep hit).
_HIT_STATUSES = frozenset({"indexed", "reindexed", "skipped"})


def sac_url(article_id: int) -> str:
    return SAC_URL_TEMPLATE.format(id=article_id)


def _fetch(article_id: int, min_words: int, delay: float):
    """Network + filter stage (safe to run in parallel).

    Returns (status, article_or_None): status is "empty"/"failed" with no
    article, or "ok" with the scraped article ready to index.
    """
    _rate_limit(delay)
    try:
        article = scrape(sac_url(article_id))
    except Exception as exc:  # network/HTTP error → treat as a miss
        log.warning("[%s] failed: %s", article_id, exc)
        return "failed", None
    if article.metadata.word_count <= min_words:
        return "empty", article
    return "ok", article


def _index(article_id: int, article) -> str:
    """Index stage — MUST run serially (single embedder + single SQLite writer)."""
    meta = article.metadata
    # Force SAC into the retrieval collection (see SAC_COLLECTION). Reflect it in
    # the saved metadata so .meta.json stays consistent with where it's indexed.
    meta.destination = SAC_COLLECTION
    save_article(article, name=f"sac_{article_id}")
    return reindex_if_changed(
        article_id=f"sac:{article_id}",
        title=meta.title,
        content=article.markdown,
        source=sac_url(article_id),
        genre=meta.genre,
        url=sac_url(article_id),
        content_hash=meta.content_hash,
        collection=SAC_COLLECTION,
    )


def process_one(
    article_id: int, min_words: int, dry_run: bool, delay: float = 0.0
) -> str:
    """Scrape, filter, and idempotently ingest a single SAC article (sequential).

    Returns a status: "indexed" | "reindexed" | "skipped" | "empty" | "failed".
    """
    status, article = _fetch(article_id, min_words, delay)
    if status == "failed":
        return "failed"
    meta = article.metadata
    if status == "empty":
        log.info("[%s] empty/login (%d words) — skip", article_id, meta.word_count)
        return "empty"
    if dry_run:
        log.info("[%s] OK (%d words) — %s", article_id, meta.word_count, meta.title)
        return "indexed"  # would-index; not persisted
    result = _index(article_id, article)
    log.info("[%s] %s (%d words) — %s", article_id, result, meta.word_count, meta.title)
    return result


def run_sac(
    *,
    mode: str,
    ids: list[int] | None = None,
    start: int | None = None,
    end: int | None = None,
    min_words: int = DEFAULT_MIN_WORDS,
    delay: float = DEFAULT_DELAY,
    max_misses: int = DEFAULT_MAX_MISSES,
    limit: int = 0,
    dry_run: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress=None,
    should_cancel=None,
) -> dict[str, int]:
    """Core loop shared by the CLI and the API background runner.

    mode: "ids" (needs `ids`), "range" (needs `start`+`end`), or "sweep"
    (needs `start`; walks up then down until `max_misses` consecutive misses
    per direction). `on_progress(status, article_id, processed, counts)` is
    called after each article; `should_cancel()` is polled to stop early.
    Returns the status→count tally.
    """
    counts: dict[str, int] = {}
    processed = 0

    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def _record(status: str, article_id: int) -> None:
        nonlocal processed
        counts[status] = counts.get(status, 0) + 1
        processed += 1
        if on_progress:
            on_progress(
                status=status,
                article_id=article_id,
                processed=processed,
                counts=dict(counts),
            )

    if mode in ("ids", "range"):
        seq = list(ids or []) if mode == "ids" else list(range(start, end + 1))
        if limit:
            seq = seq[:limit]
        if concurrency > 1:
            # Parallel fetch (network) + serial index (single embedder/SQLite writer).
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {ex.submit(_fetch, aid, min_words, delay): aid for aid in seq}
                for fut in as_completed(futures):
                    if _cancelled():
                        break
                    aid = futures[fut]
                    status, article = fut.result()
                    if status == "empty":
                        log.info(
                            "[%s] empty/login (%d words) — skip",
                            aid,
                            article.metadata.word_count,
                        )
                    elif status == "ok":
                        if dry_run:
                            log.info(
                                "[%s] OK (%d words) — %s",
                                aid,
                                article.metadata.word_count,
                                article.metadata.title,
                            )
                            status = "indexed"
                        else:
                            status = _index(aid, article)  # serial (main thread)
                            log.info(
                                "[%s] %s — %s", aid, status, article.metadata.title
                            )
                    _record(status, aid)
        else:
            for article_id in seq:
                if _cancelled():
                    break
                _record(process_one(article_id, min_words, dry_run, delay), article_id)
    elif mode == "sweep":
        # Sweep stays sequential: the miss-counter stop condition needs ordered results.
        for direction_start, step in ((start, +1), (start - 1, -1)):
            misses = 0
            article_id = direction_start
            while misses < max_misses and article_id >= 1:
                if _cancelled() or (limit and processed >= limit):
                    break
                status = process_one(article_id, min_words, dry_run, delay)
                _record(status, article_id)
                misses = 0 if status in _HIT_STATUSES else misses + 1
                article_id += step
            if _cancelled() or (limit and processed >= limit):
                break
    else:
        raise ValueError(f"unknown mode {mode!r}")

    return counts


def _summary(counts: dict[str, int]) -> None:
    total = sum(counts.values())
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    log.info("=" * 60)
    log.info("Done: %d processed | %s", total, parts or "nothing")
    log.info("=" * 60)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # Shared flags live on a parent parser so they work AFTER the subcommand
    # (e.g. `range 61000 61050 --dry-run`), which is what users expect.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dry-run", action="store_true", help="Scrape+report, no writes/index"
    )
    common.add_argument(
        "--limit", type=int, default=0, help="Max articles to process (0 = no cap)"
    )
    common.add_argument(
        "--min-words",
        type=int,
        default=DEFAULT_MIN_WORDS,
        help=f"Skip articles at/below this word count (default {DEFAULT_MIN_WORDS})",
    )
    common.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between requests (default {DEFAULT_DELAY})",
    )
    common.add_argument(
        "--max-misses",
        type=int,
        default=DEFAULT_MAX_MISSES,
        help=f"Consecutive misses that end a sweep direction (default {DEFAULT_MAX_MISSES})",
    )
    common.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Parallel fetchers for ids/range (default {DEFAULT_CONCURRENCY}; index stays serial)",
    )

    parser = argparse.ArgumentParser(
        description="Scrape the GeneXus SAC knowledge base.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    p_ids = sub.add_parser("ids", parents=[common], help="Scrape explicit article ids")
    p_ids.add_argument("ids", nargs="+", type=int)
    p_range = sub.add_parser(
        "range", parents=[common], help="Scrape an inclusive id range"
    )
    p_range.add_argument("start", type=int)
    p_range.add_argument("end", type=int)
    p_sweep = sub.add_parser(
        "sweep", parents=[common], help="Auto-sweep up and down from a start id"
    )
    p_sweep.add_argument("start", type=int)
    p_list = sub.add_parser(
        "list", parents=[common], help="Scrape ids read from a file (one per line)"
    )
    p_list.add_argument("file", help="Text file with one numeric article id per line")

    args = parser.parse_args()

    if args.dry_run:
        log.info("DRY RUN — no files written, nothing indexed")

    # `list` is `ids` sourced from a file.
    mode = args.mode
    ids = getattr(args, "ids", None)
    if mode == "list":
        with open(args.file, encoding="utf-8") as fh:
            ids = [int(line) for line in fh if line.strip().isdigit()]
        log.info("Loaded %d ids from %s", len(ids), args.file)
        mode = "ids"

    counts = run_sac(
        mode=mode,
        ids=ids,
        start=getattr(args, "start", None),
        end=getattr(args, "end", None),
        min_words=args.min_words,
        delay=args.delay,
        max_misses=args.max_misses,
        limit=args.limit,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
    )

    _summary(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
