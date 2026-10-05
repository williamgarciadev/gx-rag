# Bantotal PDF Pipeline (local manuals → `bt_docs`)

Status: **done & verified** against the 5 real manuals. Entry point:
`python -m scripts.ingest_local --manifest data/manuals.json --dry-run`.
No new dependencies — `pymupdf` was already in `requirements.txt`.

The third ingestion source, beside the wiki scraper (`scrape_wiki.py`) and the
SAC scraper (`scrape_sac.py`). Unlike those two it reads files off disk: the
Bantotal manuals exist only as PDFs and have no wiki page.

## Shape

```
data/manuals.json          declares WHICH manuals and WHY  (the human gate)
  └─ manifest.py           parses it
      └─ local_loader.py   maps chapters onto RawDoc
          └─ bt_pdf_extract.py   PDF → Markdown chapters   (all the hard parts)
              └─ bulk_loader.load_dump → ChromaDB `bt_docs`
```

`bt_pdf_extract` is pure except for `extract_pages`, the only function that
imports `fitz`. Everything from the line IR to Markdown is unit-tested without
PDFs — that is why the table and heading rules can be pinned down at all.

## The unit is a chapter, not a page

Pages were the first design: one `RawDoc` per page, reflowed by joining
overlapping blocks with `" | "`. A page is the tightest possible citation, but
it severs every table and paragraph that crosses a page break and never
recovers the heading structure — a chunk could land mid-table with no header
row in sight.

A chapter keeps both intact and cites as a range (`pp.408-415`), still
checkable against the PDF. Chapters that are too wide to cite are split on
their own sub-headings (`_MAX_CITABLE_PAGES = 10`), recursively: a level-3
piece still over budget is cut again at its level-4 headings.

That split is not cosmetic. MDI-10201 opens a chapter only on `N.0` lines:

| | before the split | after |
|---|---|---|
| docs | 8 | 193 |
| widest span | **440 pages** (`pp.126-565`) | 44 |
| median span | — | 3 pages |

Every chunk carries a breadcrumb in its *text*, not only in metadata, so it
reaches the embedding and the BM25 index too:

```
[MDI-10201 Sistema de Préstamos · 2.0 Esquemas contables · 2.13.1.6 Ordinal 45: Comisión por Mora · pp.420-421]
```

## What the extractor handles, and why it had to

Every rule below exists because the corpus broke a simpler one.

- **Furniture is pattern-based, not frequency-based.** Footers vary per page
  ("Página N de M"), so recurrence never sees them repeat. The "Documento
  Privado" watermark shatters into 1-2 char runs along the bottom edge.
- **Headings are detected by FONT** (FranklinGothic-Demi), not size: in
  MDI-10110 body and headings both sit at 8pt. Docs with no Demi structure
  (ETP) fall back to size-over-body.
- **Index pages are dropped two ways.** Dot leaders catch most. MDU-10115 has
  none — entry and page number are separate lines — so the second rule is a
  column of bare numbers flush right (x/width ≥ 0.75) that accounts for ≥80% of
  the page's bare numbers. The share matters: MDI-10109 p.181 is real content
  with numbers at 0.21, 0.39 *and* 0.85, and position alone deleted it.
- **Tables are rebuilt by region and anchor.** `find_tables` was discarded —
  double-drawn borders make PyMuPDF hallucinate 12-column grids. Columns are
  clustered from the data's own x-starts, capped by the header's cell count:
  MDI-10109's data-structure tables *centre* their cells, so one column's x0
  wandered from 234 to 274 and split into three bands. Header labels map by
  ORDER when the counts agree, because a centred label matched to the nearest
  left-aligned band lands one column off.
- **The whole table is one block.** Blocks join with a blank line, and a blank
  line between rows stops Markdown reading it as a table at all.

## Nothing is dropped

A piece under 25 words is a stub — a heading plus one line. It is **folded into
the preceding piece**, never discarded: the split leaves 85 such pieces in
MDI-10201, 3% of its text, and in a parametrisation manual twenty words is
often a whole fact. A stub that opens a *different* chapter is left alone;
gluing it onto the previous chapter's tail would make its citation lie.

## Current state, measured

| Manual | docs | tables | widest span | over 10 pages |
|---|---|---|---|---|
| MDI-10109 Contrapartes | 124 | 57 | 20 | 2 |
| MDI-10201 Préstamos | 193 | 3 | 44 | 12 |
| MDI-10401 Servicios | 71 | 2 | 11 | 1 |
| MDU-10115 Precios | 38 | 0 | 9 | 0 |

`MDI-10102 Descartes` parses fine (6 docs) but the manifest keeps it out — it
is the *installer* manual. The full manifest run reports **540 documents,
~1406 chunks**, with 5 of 21 declared files excluded and a reason for each.

## Known limits

- **15 docs still span more than 10 pages** (max 44, all in MDI-10201/10401).
  They have no sub-heading left to cut on. A warning is logged per document so
  the curator sees them in the dry-run; the text is real, only the citation is
  coarse.
- **MDI-10201 yields only 3 tables** where MDI-10109 yields 57. Its
  parametrisation tables use a layout the region detector does not open on.
  Not diagnosed.
- **Images are dropped.** Figure captions survive as `*Fig.N …*` — the only
  trace of a screenshot an agent gets.

## Usage

```bash
# The gate: parse and report, index nothing.
python -m scripts.ingest_local --manifest data/manuals.json --dry-run

# Real run.
python -m scripts.ingest_local --manifest data/manuals.json

# Ad-hoc paths (files or directories, swept recursively).
python -m scripts.ingest_local "F:/ProyectosClaude/docs/Bantotal" --collection bt_docs --dry-run
```

A manifest is the only place recording *why* a document is in or out. A
directory sweep cannot express that — and a folder named `_skipped/` turned out
to be indexed anyway.

## Two manifests, one per document root

A manifest has a single `root`, so documents living on another drive need their
own file rather than absolute paths smuggled into `file` entries:

| Manifest | Root | Declared | In |
|---|---|---|---|
| `data/manuals.json` | `F:/ProyectosClaude/docs` | 21 | 16 |
| `data/manuals-onedrive.json` | OneDrive · `Documentación Bantotal` | 31 | 15 |

```bash
python -m scripts.ingest_local --manifest data/manuals-onedrive.json --dry-run
```

The OneDrive set adds the second-tier manuals (Contabilidad, Conciliaciones,
Cash Management, Seguros, Cadena de Cierre), the PRD-99000 functional catalogue,
and 8 ETP/ECP procedure notes — 488 documents, ~1294 chunks. What it leaves out
matters as much: an **older** MDI-10109 (V3R1.25 against the V3R1.28 already
indexed), four files already declared in the other manifest, the NIIF standards,
and four routine sheets whose filenames do not say which of them is current.

**Code collision to know about.** `MDI-10201` is the code of *both* Sistema de
Préstamos and Sistema de Seguros. The chunk text disambiguates them — the
breadcrumb carries `manual_title` — but `meta["manual"]` alone does not, so a
metadata filter on that code returns both manuals. Nothing filters by it today;
it would need a composite key if something ever does.
