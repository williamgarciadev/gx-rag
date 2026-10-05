"""
Configuración de la aplicación via pydantic-settings.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.paths import CHROMA_PATH, SYNTAX_DB_PATH

# Minimum length for a public bearer API key (entropy floor).
MIN_API_KEY_LENGTH = 32


class VectorStoreConfig(BaseSettings):
    path: Path = CHROMA_PATH
    embedding_model: str = "intfloat/multilingual-e5-large"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_size: int = 1000
    chunk_overlap: int = 200


class SyntaxDBConfig(BaseSettings):
    path: Path = SYNTAX_DB_PATH


class CollectionsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GX_")
    gx_docs: str = "gx_docs"
    bt_docs: str = "bt_docs"
    gx_syntax: str = "gx_syntax"
    bt_apis: str = "bt_apis"
    cross_ref: str = "cross_ref"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GX_", env_file=".env")

    vectorstore: VectorStoreConfig = VectorStoreConfig()
    syntax_db: SyntaxDBConfig = SyntaxDBConfig()
    collections: CollectionsConfig = CollectionsConfig()

    server_name: str = "gx-bt-rag-mcp"
    server_transport: str = "stdio"
    default_version: str = "gx18"

    # Public HTTP transport (src/http_server.py). Comma-separated bearer API keys
    # (env GX_PUBLIC_API_KEYS). Empty → the public endpoint fails closed (401 all).
    public_api_keys: str = ""
    # Bearer API keys for the ADMIN Ingestor API (src/api.py), comma-separated
    # (env GX_ADMIN_API_KEYS). Separate from public_api_keys on purpose: these
    # grant corpus WRITES, so a public read key must never open /scrape.
    # Empty → only loopback callers are served (see src/api.py::require_api_key).
    admin_api_keys: str = ""
    # Fixed-window rate limit per API key, requests per minute.
    rate_limit_per_minute: int = 60
    # Max failed-auth attempts per client IP per minute before throttling (429).
    max_auth_failures_per_minute: int = 10
    # Comma-separated Host allowlist for the public transport (TrustedHost). "*"
    # is permissive — set to the real hostname(s) in production.
    allowed_hosts: str = "*"

    # Comma-separated CORS origin allowlist for the ADMIN API (src/api.py).
    # Never "*": that surface sends credentials, and Starlette answers a
    # wildcard-plus-credentials config by echoing the caller's own Origin —
    # which grants every website the curator visits, the exact opposite of a
    # wildcard's intent. Defaults cover the two local dev frontends
    # (Vite 5173, dev.sh 3050); production sets the real origin.
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3050,http://127.0.0.1:3050"
    )

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        return hosts or ["*"]

    @property
    def allowed_origin_list(self) -> list[str]:
        """CORS origins for the admin API. Empty config → empty list (deny all).

        Fails closed on purpose: an operator who blanks the variable gets no
        browser access, not unrestricted browser access.
        """
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def public_api_key_set(self) -> frozenset[str]:
        return _parse_api_keys(self.public_api_keys, "public")

    @property
    def admin_api_key_set(self) -> frozenset[str]:
        return _parse_api_keys(self.admin_api_keys, "admin")


def _parse_api_keys(raw: str, label: str) -> frozenset[str]:
    """Split a comma-separated key list, rejecting anything under the entropy floor.

    Raising at parse time is deliberate: a key too short to be safe should stop
    the service from starting, not quietly protect nothing.
    """
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip())
    weak = [k for k in keys if len(k) < MIN_API_KEY_LENGTH]
    if weak:
        # Report the count only — never echo the key material.
        raise ValueError(
            f"{label} API keys must be >= {MIN_API_KEY_LENGTH} chars; "
            f"{len(weak)} configured key(s) are too short."
        )
    return keys


config = Config()
