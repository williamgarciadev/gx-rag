"""Load the verified GeneXus corpus catalogs into gx_syntax.

Source of truth is `~/.claude/genexus/corpus/*.tsv`, the curated name lists the
grounding rules already treat as authoritative. Measured shape:

    api.tsv         3 cols — name, type (command|function|method), url
    properties.tsv  2 cols — name, url
    datatypes.tsv   2 cols — name, url
    events.tsv      2 cols — name, url

No header, CRLF endings, and — the fact that shaped this whole workstream — no
version column. Every row therefore lands with `version_since = NULL`, which
`verify_syntax` answers as PISTA when asked about a version older than the
catalog's own. Loading these BEFORE that verdict existed would have produced
false VÁLIDOs at scale against the GX16 Bantotal KBs.

Known limitation, stated rather than hidden: multi-word names ("Accent Color"
— 80% of properties) are hit by the exact-match path only when the snippet IS
the bare name. Inside code, `parse_snippet` reads identifiers, members and
calls, so they are never formed from a statement. `list_methods` enumerates
them and the near-miss lookup suggests them.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from src.syntax_db.models import GxSyntax, get_session

log = logging.getLogger(__name__)

#: Which object_type to assign to each 2-column file. api.tsv is absent because
#: it carries its own type per row.
DEFAULT_OBJECT_TYPES = {
    "properties.tsv": "property",
    "datatypes.tsv": "data type",
    "events.tsv": "event",
}


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    object_type: str
    source_doc_id: str
    source: str = "corpus"


@dataclass
class LoadReport:
    inserted: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.skipped


def parse_catalog_tsv(
    path: str | Path, object_type: str | None = None
) -> Iterator[CatalogEntry]:
    """Yield a CatalogEntry per data row.

    A 3-column file carries its own type per row; a 2-column file needs
    `object_type`. Requiring it explicitly instead of defaulting is deliberate:
    a wrong default would scatter rows under the wrong object_type and make
    list_methods quietly lie.
    """
    path = Path(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        # splitlines() handles \n; \r survives only if the file mixes endings.
        fields = [f.strip() for f in line.strip().split("\t")]
        if len(fields) == 3:
            name, row_type, url = fields
        elif len(fields) == 2:
            if object_type is None:
                raise ValueError(
                    f"{path}: 2-column file needs an explicit object_type"
                )
            name, url = fields
            row_type = object_type
        else:
            continue  # blank or malformed
        if not name or not url:
            continue
        yield CatalogEntry(name=name, object_type=row_type, source_doc_id=url)


def load_entries(entries) -> LoadReport:
    """Insert entries into gx_syntax, keyed on (name, object_type).

    Idempotent: re-running skips what is already there rather than duplicating.
    The key includes object_type because names collide across files — "Call" is
    a command in api.tsv and a property in properties.tsv, and collapsing them
    would drop one meaning.
    """
    report = LoadReport()
    session = get_session()
    try:
        existing = {
            (name, object_type)
            for name, object_type in session.query(
                GxSyntax.name, GxSyntax.object_type
            ).all()
        }
        for entry in entries:
            key = (entry.name, entry.object_type)
            if key in existing:
                report.skipped += 1
                continue
            session.add(
                GxSyntax(
                    name=entry.name,
                    object_type=entry.object_type,
                    source=entry.source,
                    source_doc_id=entry.source_doc_id,
                    kb_scope="global",
                    # version_since stays NULL — the sources carry no version.
                    # verify_syntax turns that into PISTA, not VÁLIDO.
                )
            )
            existing.add(key)
            report.inserted += 1
        session.commit()
    finally:
        session.close()
    log.info("Catalog load: %d inserted, %d skipped", report.inserted, report.skipped)
    return report
