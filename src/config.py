"""
Configuración de la aplicación via pydantic-settings.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum length for a public bearer API key (entropy floor).
MIN_API_KEY_LENGTH = 32


class VectorStoreConfig(BaseSettings):
    path: Path = Path("data/chroma")
    embedding_model: str = "intfloat/multilingual-e5-large"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_size: int = 1000
    chunk_overlap: int = 200


class SyntaxDBConfig(BaseSettings):
    path: Path = Path("data/syntax.db")


class CollectionsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GX_")
    gx_docs: str = "gx_docs"
    bt_docs: str = "bt_docs"
    gx_syntax: str = "gx_syntax"
    bt_apis: str = "bt_apis"
    cross_ref: str = "cross_ref"


class Config(BaseSettings):
    # extra="ignore": el .env es de toda la app, no solo de Config. Ahi viven
    # GXRAG_DSN (lo leen pg_client y syntax_db via os.environ) y HF_HUB_DISABLE_XET
    # (lo lee huggingface_hub). Con el "forbid" que trae BaseSettings por defecto,
    # cualquier clave que no sea un campo de esta clase rompe el import entero.
    model_config = SettingsConfigDict(
        env_prefix="GX_", env_file=".env", extra="ignore"
    )

    vectorstore: VectorStoreConfig = VectorStoreConfig()
    syntax_db: SyntaxDBConfig = SyntaxDBConfig()
    collections: CollectionsConfig = CollectionsConfig()

    server_name: str = "gx-bt-rag-mcp"
    server_transport: str = "stdio"
    default_version: str = "gx18"

    # Public HTTP transport (src/http_server.py). Comma-separated bearer API keys
    # (env GX_PUBLIC_API_KEYS). Empty → the public endpoint fails closed (401 all).
    public_api_keys: str = ""
    # Fixed-window rate limit per API key, requests per minute.
    rate_limit_per_minute: int = 60
    # Max failed-auth attempts per client IP per minute before throttling (429).
    max_auth_failures_per_minute: int = 10
    # Comma-separated Host allowlist for the public transport (TrustedHost). "*"
    # is permissive — set to the real hostname(s) in production.
    allowed_hosts: str = "*"

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        return hosts or ["*"]

    @property
    def public_api_key_set(self) -> frozenset[str]:
        keys = frozenset(
            k.strip() for k in self.public_api_keys.split(",") if k.strip()
        )
        weak = [k for k in keys if len(k) < MIN_API_KEY_LENGTH]
        if weak:
            # Report the count only — never echo the key material.
            raise ValueError(
                f"public API keys must be >= {MIN_API_KEY_LENGTH} chars; "
                f"{len(weak)} configured key(s) are too short."
            )
        return keys


config = Config()
