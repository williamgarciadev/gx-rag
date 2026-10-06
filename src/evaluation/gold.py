"""The gold set: questions whose right answer is known.

JSONL rather than JSON so a case is one line — appendable from a real session
the moment a bad answer shows up, and readable in a diff. Growing this file is
the cheapest quality work available on this project: every case starts as a
retrieval failure someone actually hit.

Two mandatory fields, both learned from `ingestion/manifest.py`:

- `expected`: article ids (metadata "id"), never chunk ids. Chunk ids change
  every time chunking is retuned, which would expire the gold set against
  exactly the regression it exists to catch.
- `note`: why this is the right answer. A miss six months from now has to be
  diagnosable — is retrieval broken, or was the question badly written?

Optional `filters`: the same object `search_docs` accepts. A case measures the
search an agent following the tool's instructions would run, and in gx_docs
that means `origin=gx_wiki`: SAC tickets are over half the collection, and the
unfiltered search found 2 of 9 gold articles where the filtered one found 8
(measured 2026-10-06).
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldCase:
    question: str
    collection: str
    expected: frozenset[str]
    note: str
    filters: dict | None = None


def load_gold(path: str | Path) -> list[GoldCase]:
    """Parse a JSONL gold set, rejecting entries that cannot be scored."""
    cases: list[GoldCase] = []
    text = Path(path).read_text(encoding="utf-8")

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        if not raw.get("expected"):
            raise ValueError(f"{path}: line {number} has no 'expected' documents")
        if not raw.get("note"):
            raise ValueError(f"{path}: line {number} has no 'note'")
        filters = raw.get("filters")
        if filters is not None and not isinstance(filters, dict):
            raise ValueError(f"{path}: line {number} has 'filters' that is not an object")
        cases.append(
            GoldCase(
                question=raw["question"],
                collection=raw.get("collection", "gx_docs"),
                expected=frozenset(raw["expected"]),
                note=raw["note"],
                filters=filters,
            )
        )
    return cases
