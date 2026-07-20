"""
Configuración de la aplicación via pydantic-settings.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    model_config = SettingsConfigDict(env_prefix="GX_", env_file=".env")

    vectorstore: VectorStoreConfig = VectorStoreConfig()
    syntax_db: SyntaxDBConfig = SyntaxDBConfig()
    collections: CollectionsConfig = CollectionsConfig()

    server_name: str = "gx-bt-rag-mcp"
    server_transport: str = "stdio"
    default_version: str = "gx18"


config = Config()
