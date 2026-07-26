# SAC Scraping Pipeline (GeneXus Support Knowledge Base)

Status: **done & verified** — ported from the VPS into this repo and improved.
Entry point: `python -m scripts.scrape_sac`. **No new dependencies** (uses the
existing httpx-based `scrape_wiki`; the `websac` dead end that would have needed
Playwright was abandoned).

## What SAC is

`sac.genexus.com` is the GeneXus support knowledge base (SUGERENCIAS / errores / tickets).
Each entry has a numeric `article_id`. This is a valuable, Spanish-language corpus for the
anti-hallucination RAG goal: real reported issues, workarounds, and product notes.

## The URL trap (important)

There are TWO different SAC front-ends. Only one is scrapeable without a browser:

| URL | Verdict |
|-----|---------|
| `https://www.genexus.com/pt/developers/websac?data=<id>` | ❌ Dead end. Auth-gated + AJAX-rendered (`gx.evt.execEvt`). Plain HTTP returns only the nav shell; a headless browser still gets nothing without a GeneXus login. `data=1` redirects to `accounts.genexus.com` login. |
| `https://sac.genexus.com/frontend.scmain.aspx?S%3B178%3B94%3B1264%3B1%3B1%3BO%3B,<id>;;` | ✅ **Use this.** Server-rendered HTML. The article body lives in `td#CONTENIDO`. Verified reachable from a local machine (HTTP 200, real content). |

The verbose `S;178;94;1264;1;1;O;` prefix is the GeneXus portal parameter; `<id>` is the
`article_id`, followed by `;;` (URL-encoded as `%3B%3B` / literal `;;`).

## Content characteristics

- Real articles: **93+ words**. Fields include a `SUGERENCIA SAC #NNNNN:` header, report date,
  category, description, and sometimes a workaround.
- **word_count == 47** → the article requires a GeneXus login (skip it).
- **word_count == 61** → another empty/placeholder variant (skip it).
- Rule of thumb: **keep only `word_count > 70`**.
- Boilerplate to strip: the `"Recuerda que ingresando con tu CUENTA GeneXus™…"` header and the
  breadcrumb nav (`PRIMERA / ANTERIOR / SIGUIENTE / LAST / VOLVER A LA LISTA`).
  The old scraper wrongly used `"Recuerda que"` as the title because of this.

## Prior art (VPS, to be superseded by the local port)

Location on VPS (`gga@173.249.49.68`, sudo): `/home/admin/.openclaw/workspace/finna/`

- `finn-sac.sh` — bash loop. Commands: `range <start> <end>`, `ids <id...>`, `list <file>`, `status`.
  Tracks processed URLs in `data/finn.db` (SQLite) to avoid re-scraping. `sleep 1` between requests.
- `ingest-batch.py` — ingests scraped `.md` into the container's ChromaDB. **Uses `index_article`
  (fixed chunk ids → collides on re-run).**
- Already scraped: ranges across **60000–70000** (~493 articles in 61000–62000).
- The VPS `scrape_wiki.py` is a SAC-modified fork of this repo's `scripts/scrape_wiki.py`.
  The only differences: SAC branch in `extract_main_content()` (`td#CONTENIDO`, drop
  `SacsContainerDiv`) and `article_id` extraction from the URL in `main()`.

## Local port + improvements (this repo)

Bring it here, reusing existing infrastructure, and fix the known defects.

1. **Port SAC extraction** into `scripts/scrape_wiki.py` (surgical):
   - `extract_main_content()`: prefer `td#CONTENIDO`; keep the wiki fallbacks.
   - Extract numeric `article_id` (`O:<digits>`) for the output filename and metadata.
2. **Fix data quality**:
   - Derive the title from the `SUGERENCIA SAC #NNNNN:` line, not the boilerplate.
   - Strip the "Recuerda que…" header and breadcrumb nav from the content.
3. **Login/empty detection**: reject `word_count <= 70` (login=47, empty=61) before ingest.
4. **Idempotent ingest**: use `src/vectorstore/indexer.py::reindex_if_changed()`
   (content-hash aware), NOT `index_article` — mirrors `src/ingestion/bulk_loader.py`.
5. **Loop CLI** `scripts/scrape_sac_range.py` (Python, matches `scripts/ingest_dump.py` style):
   - `--start <id>` and auto-sweep **up and down** until **N consecutive misses** per direction
     (`--max-misses`, default 50) — this covers the user's "todos hacia arriba y hacia abajo".
   - Also supports explicit `--range a b` and `--ids`.
   - `--dry-run`, `--limit`, polite delay, progress every N.
   - Runs locally (local machine reaches `sac.genexus.com`; the VPS host could not, which is why
     the old pipeline proxied through the container).

## Usage

```bash
# Preview a range (no writes, no index) — the gate before a real run
python -m scripts.scrape_sac range 61000 61050 --dry-run

# Explicit ids
python -m scripts.scrape_sac ids 61036 61037

# Auto-sweep up AND down from a start id until 50 consecutive misses/direction
python -m scripts.scrape_sac sweep 49983 --max-misses 50

# Common flags: --dry-run --limit N --min-words 70 --delay 1.0 --max-misses 50
```

Output: `data/scraped/sac_<id>.md` + `.meta.json`; indexed into the `gx_docs`
collection keyed by `sac:<id>` (unique per article — safe, idempotent re-runs).

## Admin UI panel (launch + monitor)

The Vue admin UI has an **"Ingesta SAC — Barrido"** panel to launch and watch a
sweep/range/ids job without the CLI. It polls the backend for live progress.

- Backend job model: `src/sac_jobs.py` — an in-memory registry running each job
  on a daemon thread (single-process, admin scope; a run is lost on API restart).
- Endpoints (`src/api.py`): `POST /sac/scrape` (start, validated bounds),
  `GET /sac/jobs`, `GET /sac/jobs/{id}` (progress snapshot: status, processed,
  current_id, counts, log tail), `POST /sac/jobs/{id}/cancel`.
- Frontend: `frontend/src/components/SacPanel.vue` + `useApi.js` (`sacScrape`,
  `sacJob`, `sacCancel`), wired into `App.vue`.
- Run it: `./dev.sh` → UI on http://localhost:3050 (backend 8050). Vite proxies
  `/api/*` to the backend, so the panel's `/api/sac/*` calls just work.

Safety bounds on `POST /sac/scrape`: ≤500 ids, range span ≤5000, ids 1..10M,
delay 0.1..10s — the UI can't launch an abusive crawl.

## What was implemented

- `scripts/scrape_wiki.py`: SAC branch in `extract_main_content()` (`td#CONTENIDO`
  + `_clean_sac_content()` stripping login nudge / pager / back-to-list / hidden
  error divs); SAC title from `*SACTIT`.
- `src/ingestion/ingestor.py`: `save_article(article, name=...)` filename override.
- `scripts/scrape_sac.py`: the `ids` / `range` / `sweep` loop, login/empty skip,
  idempotent ingest via `reindex_if_changed` keyed by `sac:<id>`.

**Bug fixed vs. the VPS pipeline:** the old ingest keyed ChromaDB by the SAC
`source_key`, which is a **constant** for every article — so all SAC articles
collided under one id. Keying by `sac:<id>` makes each article distinct.

## Verification (done 2026-07-26)

- Dry-run `range 61034 61040`: `empty=3, indexed=4`. Login pages (47 words) and a
  placeholder (16 words) correctly skipped; real articles got proper `SAC #NNNNN:`
  titles.
- Real ingest `ids 61036 61037`: indexed (3 chunks each, unique `sac:` ids).
- Idempotency: immediate re-run → `skipped=2` (no re-embed, no collision).
- Retrieval: `search_docs("Microsoft.Data.SqlClient driver .NET")` returns
  **SAC #61037** as the top hit.

## Follow-ups (not done)

- Backfill the already-scraped VPS ranges (60000–70000) and sweep down toward
  the user's 49983 and below.
- Decide whether to sync this local corpus to the VPS MCP (as done previously).
- The venv has a leftover Playwright + Chromium from the abandoned websac spike;
  uninstall if you want the space back (`pip uninstall playwright`).
