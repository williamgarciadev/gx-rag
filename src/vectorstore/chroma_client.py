"""
ChromaDB client — wrapper singleton sobre chromadb.
"""

import chromadb
from chromadb.config import Settings as ChromaSettings
from pathlib import Path
import logging

from src.paths import CHROMA_PATH

log = logging.getLogger(__name__)

_client = None


def default_persist_path() -> Path:
    """Absolute path to the store, independent of the caller's cwd."""
    return CHROMA_PATH


def get_client(persist_path: str | Path | None = None) -> chromadb.PersistentClient:
    global _client
    if _client is None:
        path = Path(persist_path) if persist_path is not None else default_persist_path()
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
        log.info(f"ChromaDB client initialized at {path}")
    return _client
