"""
ChromaDB client — wrapper singleton sobre chromadb.
"""

import chromadb
from chromadb.config import Settings as ChromaSettings
from pathlib import Path
import logging

log = logging.getLogger(__name__)

_client = None


def get_client(persist_path: str | Path = "data/chroma") -> chromadb.PersistentClient:
    global _client
    if _client is None:
        path = Path(persist_path)
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
