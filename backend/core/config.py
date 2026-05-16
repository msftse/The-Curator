"""Application settings.

Reads `.env.local` (12-factor). Production secrets must come from real env
vars / Key Vault, not from this file. See AGENTS.md §8.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for backend + worker.

    All fields default to the docker-compose emulator stack so the
    application boots end-to-end on `docker compose up` with no env vars set.
    """

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Cosmos ----
    cosmos_endpoint: str = "https://localhost:8081"
    cosmos_key: str = (
        "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
    )
    cosmos_db_name: str = "skillhub"
    cosmos_verify_tls: bool = False

    # ---- Blob (Azurite by default) ----
    blob_connection_string: str = (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://localhost:10000/devstoreaccount1;"
    )
    blob_published_container: str = "published"
    blob_archive_container: str = "archive"
    blob_snapshots_container: str = "snapshots"

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- App ----
    auth_mode: Literal["stub", "oidc"] = "stub"
    classifier_provider: Literal["stub", "llm"] = "stub"
    max_bundle_bytes: int = 10 * 1024 * 1024
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ---- Stub auth role allowlists (comma-separated emails) ----
    manager_emails: str = "manager@org"
    admin_emails: str = "admin@org"

    # ---- Worker tuning ----
    classifier_queue_key: str = "queue:classifier"
    classifier_blpop_timeout_seconds: int = 5

    # ---- Cache TTLs (seconds) ----
    cache_list_ttl_seconds: int = 60
    cache_item_ttl_seconds: int = 300
    publish_lock_ttl_seconds: int = 30

    def manager_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.manager_emails.split(",") if e.strip()}

    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — safe to call from anywhere."""
    return Settings()  # type: ignore[call-arg]
