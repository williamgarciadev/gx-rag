"""In-memory registry + background runner for SAC scraping jobs.

Single-process, admin-tool scope: jobs live in a module-level dict and run on
daemon threads. Not durable across restarts — a running sweep is lost if the
API restarts, which is acceptable for this manual curation workflow.

Used by the FastAPI endpoints in src/api.py to let the admin UI launch and
monitor SAC scraping without blocking a request.
"""

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scripts.scrape_sac import run_sac

log = logging.getLogger(__name__)

_MAX_LOG = 200  # per-job rolling log lines kept for the UI


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SacJob:
    id: str
    params: dict
    status: str = "running"  # running | done | error | cancelled
    processed: int = 0
    current_id: int | None = None
    counts: dict = field(default_factory=dict)
    error: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=_MAX_LOG))
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        """Thread-safe-enough view for the API (dataclass fields are replaced,
        not mutated in place, so a plain read is consistent per field)."""
        return {
            "id": self.id,
            "params": self.params,
            "status": self.status,
            "processed": self.processed,
            "current_id": self.current_id,
            "counts": dict(self.counts),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": list(self.log),
        }


_jobs: dict[str, SacJob] = {}
_lock = threading.Lock()


def start_job(params: dict) -> str:
    """Register a job and run it on a daemon thread. `params` are the keyword
    arguments for scrape_sac.run_sac (mode, ids/start/end, min_words, ...)."""
    job_id = uuid.uuid4().hex[:12]
    job = SacJob(id=job_id, params=params)
    with _lock:
        _jobs[job_id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    log.info("SAC job %s started: %s", job_id, params)
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
    return job.snapshot() if job else None


def list_jobs() -> list[dict]:
    with _lock:
        jobs = list(_jobs.values())
    return [
        j.snapshot() for j in sorted(jobs, key=lambda j: j.started_at, reverse=True)
    ]


def cancel_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return False
    job.cancel_event.set()
    return True


def _run(job: SacJob) -> None:
    def on_progress(*, status, article_id, processed, counts) -> None:
        job.current_id = article_id
        job.processed = processed
        job.counts = counts
        job.log.append(f"[{article_id}] {status}")

    try:
        run_sac(
            **job.params,
            on_progress=on_progress,
            should_cancel=job.cancel_event.is_set,
        )
        job.status = "cancelled" if job.cancel_event.is_set() else "done"
    except Exception as exc:  # surface to the UI instead of dying silently
        job.status = "error"
        job.error = str(exc)
        log.error("SAC job %s failed: %s", job.id, exc)
    finally:
        job.finished_at = _now()
        log.info("SAC job %s %s: %s", job.id, job.status, dict(job.counts))
