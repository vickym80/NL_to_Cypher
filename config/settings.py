"""Application settings loaded from environment variables.

All secrets and configuration are read from the environment or a .env file.
Never hardcode secrets in source code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM Provider ──────────────────────────────────────────────────────────
    llm_provider: Literal["openai", "azure_openai", "claude"] = "openai"

    # OpenAI
    openai_api_key: Optional[SecretStr] = None
    openai_model: str = "gpt-4o"

    # Azure OpenAI
    azure_openai_api_key: Optional[SecretStr] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_version: Optional[str] = None
    azure_openai_deployment: Optional[str] = None

    # Anthropic / Claude
    anthropic_api_key: Optional[SecretStr] = None
    anthropic_model: str = "claude-sonnet-4-6"

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("neo4j")
    neo4j_database: str = "neo4j"
    neo4j_max_pool_size: int = 50

    # ── NL2Cypher schema introspection ───────────────────────────────────────
    schema_cache_ttl_seconds: int = 900
    schema_enum_max_cardinality: int = 10

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    redis_feature_ttl_seconds: int = 900

    # ── ChromaDB ──────────────────────────────────────────────────────────────
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_collection_ontology: str = "ontology_nodes"

    # ── Databricks ────────────────────────────────────────────────────────────
    databricks_host: Optional[str] = None
    databricks_http_path: Optional[str] = None
    databricks_token: Optional[SecretStr] = None
    databricks_catalog: str = "supply_chain"
    databricks_schema: str = "gold"

    # ── Security ──────────────────────────────────────────────────────────────
    secret_key: SecretStr = SecretStr("change-me-to-a-random-secret-at-least-32-chars")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    allowed_origins: List[str] = ["http://localhost:3000", "http://localhost:3001"]

    # ── Application ───────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    ontology_dir: str = "ontology"
    ontology_version: str = "2.0.0"

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, v: object) -> object:
        if isinstance(v, str):
            normalized = v.strip().lower().replace("-", "_")
            if normalized in {"azureopenai", "azure_openai"}:
                return "azure_openai"
            return normalized
        return v

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    def get_ontology_path(self) -> Path:
        return Path(self.ontology_dir)

    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
