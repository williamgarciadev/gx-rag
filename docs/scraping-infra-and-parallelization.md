# SAC Scraping — Infra, Resources & Parallelization

Full picture of what the scraping/ingestion actually costs, where it should run
(Mac vs VPS), and how to parallelize it safely — so we don't improvise per side.

## Does it use an LLM?

**No generative LLM.** gx-rag is retrieval-only (no own LLM). The pipeline has
two model-free stages and one model stage:

| Stage | Uses a model? | Cost |
|-------|---------------|------|
| Scrape (httpx + BeautifulSoup) | No | Network I/O only |
| Clean / classify / hash | No | Trivial CPU |
| **Index (embeddings)** | **Yes** — `intfloat/multilingual-e5-large` (a sentence-transformer, ~560M params) | Runs local; GPU (MPS) on Mac, CPU on VPS |

The embedding model is NOT a chat LLM — it turns each chunk into a vector. It
only runs on a **real** ingest (not `--dry-run`).

## Measured bottleneck

The scraper process sits at **~1.5% CPU** — it is almost entirely idle, waiting
on the network and the deliberate politeness `--delay`. **Neither machine is
CPU/RAM bound.** Per article ≈ fetch latency + delay:

- Mac → SAC latency: **~0.65 s**
- VPS host → SAC latency: **~1.18 s** (reachable now; an earlier note said the
  VPS host was blocked — no longer true, or was container-specific)
- Plus `--delay` (0.4 s used for the backfill)

So serial throughput ≈ 1 article/second. 660 articles ≈ 11 min. The limit is
**latency + our chosen politeness throttle**, not hardware.

## Resource comparison — Mac vs VPS

| | **Mac (workstation)** | **VPS (Contabo)** |
|---|---|---|
| Cores | 18 | 6 |
| RAM | 36 GB | 11 GB |
| GPU / MPS | Yes (fast embeddings) | No (embeddings on CPU — much slower) |
| SAC latency | ~0.65 s | ~1.18 s |
| Load (idle) | ~2.6 | ~0.2–0.5 |
| Role | Dev; corpus built here then pushed | Serves the corpus to the team via public MCP |

**Conclusion:** the Mac is the better scraping+embedding machine — 3× cores, 3×
RAM, a GPU for e5-large, and ~2× lower SAC latency. The VPS has no GPU, so its
embedding step is the slow part there. Current flow (build clean corpus on Mac →
push to VPS) is the right one.

## Parallelization design (safe)

The win is **concurrent fetching**, but with one hard rule:

> **Fetch in parallel, index serially.** The e5 model is one in-process instance
> and ChromaDB is a single-writer SQLite file. Concurrent embedders/writers =
> lock contention and possible corruption. Never run two real ingests against the
> same `data/chroma` at once.

Recommended shape (a follow-up to the current sequential `run_sac`):

```
N async/thread fetchers  ──►  bounded queue  ──►  1 serial indexer (embed + write)
   (network I/O bound)                              (MPS, single SQLite writer)
```

- With **N = 4–8** fetchers, network stops being the bottleneck; the serial
  embedder (fast on MPS) becomes the limit. 660 articles: ~11 min → ~2–3 min.
- Keep a **global rate cap** (e.g. ≤ ~8 req/s) and small jitter — politeness to
  `sac.genexus.com` (third-party) is the real constraint, not our hardware.
- The current CLI already caps concurrency implicitly at 1 (sequential). Adding
  a `--concurrency N` fetch pool + serial indexer is the clean upgrade.

## Operational rules (don't improvise)

1. **One real ingest at a time** against a given `data/chroma`. A `--dry-run`
   sweep can run alongside (it never writes).
2. **Scrape on the Mac** (GPU + low latency), then push local→VPS with the sync
   runbook. Don't scrape-with-embeddings on the VPS host (no GPU → slow).
3. **Politeness first.** Concurrency is bounded by courtesy to GeneXus, not by
   cores. Cap it; add jitter; back off on errors.
4. Background a long run **without** a trailing `&` when using the harness's
   background mode — the `&` detaches inside the wrapper shell and the parent
   exits immediately (looks "done" while the child keeps or dies). Use one or the
   other, not both.
