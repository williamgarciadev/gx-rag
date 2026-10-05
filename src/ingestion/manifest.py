"""The record of which local documents belong in the corpus, and why.

A directory sweep cannot express a decision. MDU-99000 lived in a folder named
`_skipped/` and was indexed anyway, while a manual in the main folder was not,
and nothing recorded either choice — so the next sweep would silently re-add
what had just been removed. The manifest is that record: one entry per file,
an explicit `ingest` flag, and a mandatory `reason`.

Metadata is *not* repeated here. The filenames already carry it
("MDI-10201-GL-V3R1.20 - Sistema de Préstamos.pdf"), and local_loader parses it.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManifestEntry:
    file: str
    ingest: bool
    reason: str


@dataclass(frozen=True)
class Manifest:
    root: Path
    collection: str
    documents: tuple[ManifestEntry, ...]


def load_manifest(path: str | Path) -> Manifest:
    """Parse and validate a manifest file.

    Every entry must state a `reason`: an entry without one records no decision,
    which is the situation the manifest exists to end.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = []
    for i, doc in enumerate(raw.get("documents", [])):
        if not doc.get("reason"):
            raise ValueError(
                f"{path}: documents[{i}] ({doc.get('file')}) has no 'reason'"
            )
        entries.append(
            ManifestEntry(
                file=doc["file"], ingest=bool(doc.get("ingest")), reason=doc["reason"]
            )
        )
    return Manifest(
        root=Path(raw.get("root", ".")),
        collection=raw.get("collection", "bt_docs"),
        documents=tuple(entries),
    )


def iter_manifest_paths(
    path: str | Path, *, root: str | Path | None = None
) -> Iterator[Path]:
    """Yield the resolved path of every document flagged for ingestion.

    `root` overrides the manifest's own root — the documents live outside the
    repository, so the committed default is machine-specific by nature.

    A declared file that does not exist raises: skipping it quietly would
    report a successful ingestion of nothing.
    """
    manifest = load_manifest(path)
    base = Path(root) if root is not None else manifest.root
    for entry in manifest.documents:
        if not entry.ingest:
            continue
        resolved = base / entry.file
        if not resolved.is_file():
            raise FileNotFoundError(f"{path}: declared but missing — {resolved}")
        yield resolved
