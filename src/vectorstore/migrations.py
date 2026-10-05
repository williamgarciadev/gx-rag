"""Split an existing collection by chunk provenance, reusing its vectors.

Retrieval quality depends on not mixing sources of different authority: an
official wiki article and a reported-but-unresolved support ticket must not
compete for the same top-k. Separating them after the fact would normally mean
re-embedding, which costs hours of CPU — so this carries the stored embeddings
across instead.

Chunks are copied to the target before being dropped from the source, and the
match is re-evaluated on every run, so an interrupted migration converges when
re-run rather than losing or duplicating chunks.
"""

import logging

log = logging.getLogger(__name__)

# Smaller than retriever's read-only paging on purpose: each page is also
# deleted by id, and Chroma binds one SQLite variable per id — a large page
# trips "too many SQL variables" on the delete, not on the read.
_PAGE = 500

_INCLUDE = ["embeddings", "documents", "metadatas"]


def _matches(meta: dict | None, field: str, contains: str) -> bool:
    return contains in str((meta or {}).get(field, ""))


def _scan_matching(col, *, field: str, contains: str, include: list[str], handle) -> int:
    """Page through `col`, handing each page's matching row indices to `handle`.

    Returns the number of matched rows. `handle(page, picked)` must return True
    when it removed those rows from `col`: a delete shifts the remaining rows
    left, so the *same* offset has to be re-read instead of advanced. Getting
    that backwards silently skips rows — which is exactly why the three callers
    share this loop instead of each keeping a copy of it.
    """
    total = offset = 0
    while True:
        page = col.get(limit=_PAGE, offset=offset, include=include)
        ids = page.get("ids") or []
        if not ids:
            return total

        metas = page.get("metadatas") or []
        picked = [i for i, m in enumerate(metas) if _matches(m, field, contains)]
        total += len(picked)

        if not (picked and handle(page, picked)):
            # Nothing was removed, so the next page really is ahead.
            offset += len(ids)


def count_matching_by_metadata(client, source: str, *, field: str, contains: str) -> int:
    """Count chunks that a move or delete would touch, changing nothing.

    The preview gate the project requires before any bulk corpus operation.
    """
    return _scan_matching(
        client.get_or_create_collection(source),
        field=field,
        contains=contains,
        include=["metadatas"],
        handle=lambda page, picked: False,
    )


def move_matching_by_metadata(
    client,
    source: str,
    target: str,
    *,
    field: str,
    contains: str,
    on_progress=None,
) -> int:
    """Move chunks whose `metadata[field]` contains `contains` into `target`.

    Returns the number of chunks moved. Re-running is a no-op once the source
    holds no more matches.
    """
    src = client.get_or_create_collection(source)
    dst = client.get_or_create_collection(target)
    moved = 0

    def _handle(page, picked) -> bool:
        nonlocal moved
        ids = page["ids"]
        embeddings = page.get("embeddings")
        documents = page.get("documents") or []
        metas = page.get("metadatas") or []
        dst.add(
            ids=[ids[i] for i in picked],
            embeddings=[list(embeddings[i]) for i in picked],
            documents=[documents[i] for i in picked],
            metadatas=[metas[i] for i in picked],
        )
        src.delete(ids=[ids[i] for i in picked])
        moved += len(picked)
        if on_progress is not None:
            on_progress(moved)
        return True

    total = _scan_matching(
        src, field=field, contains=contains, include=_INCLUDE, handle=_handle
    )
    log.info("Moved %d chunks from %s to %s", total, source, target)
    return total


def delete_matching_by_metadata(
    client,
    source: str,
    *,
    field: str,
    contains: str,
    on_progress=None,
) -> int:
    """Delete chunks whose `metadata[field]` contains `contains`. No target.

    Some corpus removals have no destination: a manual whose content the
    bantotal-dev MCP already answers from the live schema is not worth another
    collection, it is worth dropping — it only competes for the same top-k.

    Always preview with `count_matching_by_metadata()` first; this is
    irreversible short of re-ingesting the source. Re-running is a no-op.
    """
    src = client.get_or_create_collection(source)
    deleted = 0

    def _handle(page, picked) -> bool:
        nonlocal deleted
        ids = page["ids"]
        src.delete(ids=[ids[i] for i in picked])
        deleted += len(picked)
        if on_progress is not None:
            on_progress(deleted)
        return True

    total = _scan_matching(
        src, field=field, contains=contains, include=["metadatas"], handle=_handle
    )
    log.info("Deleted %d chunks from %s", total, source)
    return total
