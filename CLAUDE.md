# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

RAG support system for GeneXus/Bantotal documentation in a banking context. **Pure retrieval — no own LLM.** It is consumed by external AI agents (e.g. Claude Code) via MCP, and its core design goal is reducing hallucination. Fully local: zero cloud, zero API keys.

Current state: **Phase 1** (corpus characterization + wiki ingestion). See `FASE1_ALISTAMIENTO.md` for live progress and `docs/README.md` for the planning-doc reading order. `docs/CLAUDE.md` is a *planning spec*, not current reality — it prescribes tooling that doesn't exist yet (`pyproject.toml`, ruff, mypy, pytest, `ingest_*.py` scripts).

## Commands

```bash
# Full stack (requires external network: docker network create infra_gateway)
docker compose up
# Services: gx-rag-api (FastAPI, host port 8000), gx-rag-ui (nginx :80 internal, no host port)

# Backend locally — ALWAYS use a venv (see note below)
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn src.api:app --reload --port 8000   # HTTP API (canonical port 8000, same as Dockerfile CMD)
.venv/bin/python -m src.server                        # MCP server (stdio) — separate entrypoint, NOT run by compose

# Scraper CLI
.venv/bin/python scripts/scrape_wiki.py <url> [--output-dir <dir>]

# Frontend (frontend/) — Vite dev server, default port 5173
npm install
npm run dev                                           # UI on http://localhost:5173
# For local dev outside Docker, point the proxy at the local API (default target is the
# Docker hostname gx-rag-api:8000, which does NOT resolve locally):
VITE_API_TARGET=http://127.0.0.1:8000 npm run dev
npm run build
```

**Ports**: backend API `8000`, frontend dev `5173` (Vite auto-increments to 5174+ if taken). The frontend calls `/api/*`, which Vite proxies to `VITE_API_TARGET` (default `http://gx-rag-api:8000` for Docker). If `8000` is busy locally, run uvicorn on another port and set `VITE_API_TARGET` to match.

**Local dev launcher** (`./dev.sh`): starts backend + frontend together and defaults to **8050** (backend) / **3050** (frontend) to avoid colliding with other local projects on this machine (`gx-docs-v1` runs on `:8000`). Override with `BACKEND_PORT` / `FRONTEND_PORT`. Docker and the canonical backend port remain `8000`.

**Must run in a venv, not the global interpreter.** A stray global package (`notebooklm_skill`) ships a top-level `scripts` package that shadows this repo's `scripts/` (which has no `__init__.py`, so it's only an implicit namespace package) — that breaks `from scripts import scrape_wiki` in `src/api.py`. A venv isolates the project and avoids the collision. Docker is unaffected (clean base image).

**Tests**: `tests/` contains only `__init__.py` — there are no tests or pytest config yet. Do not claim test coverage exists.

## Architecture

Two entrypoints share one ingestion/retrieval core:

- `src/server.py` — MCP server (official `mcp` SDK, stdio). Tools: `search_docs`, `get_document`, `verify_syntax`, `list_methods`, `add_from_url`. (`ingest_batch` is declared but returns "not implemented".)
- `src/api.py` — FastAPI "Ingestor API" that also mirrors the MCP tools as HTTP endpoints (`/scrape`, `/status`, `/articles`, `/search`, `/documents`, `/verify`, `/methods`, `/health`) for the admin UI.

Data flow:

1. **Scrape** (`scripts/scrape_wiki.py`): fetches a GeneXus wiki page, converts to Markdown, computes SHA256 `content_hash` for idempotency, extracts `version_hint`.
2. **Classify** (`classify_genre()` in the same file): heuristic keyword scoring decides `genre` (reference vs guide) and therefore the destination collection — reference → `gx_syntax` catalog, guide → `gx_docs` retrieval.
3. **Ingest** (`src/ingestion/ingestor.py`): writes `.md` + `.meta.json` to `data/scraped/` AND indexes into ChromaDB.
4. **Index** (`src/vectorstore/indexer.py`): chunks with `langchain-text-splitters`, embeds with `intfloat/multilingual-e5-large`, stores in ChromaDB at `data/chroma/`.
5. **Retrieve** (`src/vectorstore/retriever.py::hybrid_search`): **dense (e5) + BM25 (`rank-bm25`) fused with Reciprocal Rank Fusion** (RRF_K=60). BM25 index is cached per-collection and rebuilt when the doc count changes. The reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2` in config) is **intentionally not wired** — it is English-trained and the corpus is Spanish; deferred pending a multilingual model decision.
6. **Validate** (`src/syntax_db/`): SQLite catalog at `data/syntax.db` (SQLAlchemy). `gx_repository.verify_syntax()` gives a binary verdict — exact match → VÁLIDO, fuzzy-only match → INVÁLIDO, nothing similar → NO_VERIFICABLE. Every verdict is logged to `validation_log`; near-misses go to `missing_methods` for human curation.

**Two-layer anti-hallucination principle (core design rule — do not mix the layers):** retrieval (ChromaDB, fuzzy, scored) *mitigates* hallucination; validation (syntax.db, closed catalog, binary verdict) *cuts* it.

Frontend (`frontend/src/`): Vue 3 + Vite admin UI for corpus management/curation only — explicitly NOT a RAG chat interface (querying happens through MCP clients). `src/tools/` and `src/prompts/` are empty placeholders; tool logic currently lives inline in `server.py` and `api.py`.

## Configuration

`src/config.py` uses pydantic-settings (env prefix `GX_`, reads `.env` — no `.env` file exists; everything runs on hardcoded defaults). docker-compose declares no env vars.

Data directories (bind-mounted into the API container): `data/chroma/` (ChromaDB), `data/syntax.db` (SQLite catalog), `data/scraped/` (raw articles).

## Hard Rules (from project planning docs)

- Use the official `mcp` SDK, NOT standalone FastMCP.
- Never invent GeneXus/Bantotal methods, properties, or signatures — `syntax.db` is the source of truth.
- Never invent a PyPI version number from memory — check PyPI first.
- No LangChain/LlamaIndex as framework base — only `langchain-text-splitters` for chunking.
- Corpus growth requires a human gate: automatic *discovery* of candidate content is fine, automatic *ingestion* is not.
- A phase is done when behavior is functionally verified, not when code compiles.

## Known Gotchas

- Chunk-size mismatch: `config.py` declares 1000/200 but `indexer.py` hardcodes 800/100 — the config values are not actually used by the indexer.
- Retrieval `score` is now the Reciprocal Rank Fusion score (rank-based), not a calibrated similarity — higher is better, but the absolute value is not a probability.
- Nested env-var overrides in `config.py` are likely non-functional (nested pydantic-settings classes don't inherit the `GX_` prefix consistently).
- `docs/gx-bt-rag-mcp-stack.md` specs richer tool signatures (e.g. `min_score`, `snippet_mode`) and a `cross_ref` collection that are not implemented.
