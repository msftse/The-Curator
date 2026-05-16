"""Application settings.

Reads `.env.local` (12-factor). Production secrets must come from real env
vars / Key Vault, not from this file. See AGENTS.md §8.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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
    # Two auth modes:
    #   1. Connection string (Azurite / shared-key) — set BLOB_CONNECTION_STRING,
    #      leave BLOB_ACCOUNT_URL empty.
    #   2. Managed Identity / az login — set BLOB_ACCOUNT_URL
    #      (e.g. https://<account>.blob.core.windows.net). DefaultAzureCredential
    #      is used, which picks up `az login`, env vars, managed identity, etc.
    #      In this mode signed downloads use user-delegation SAS (no account key).
    blob_connection_string: str = (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://localhost:10000/devstoreaccount1;"
    )
    blob_account_url: str = ""
    blob_published_container: str = "published"
    blob_archive_container: str = "archive"
    blob_snapshots_container: str = "snapshots"

    def use_blob_identity(self) -> bool:
        """True when the blob client should authenticate via DefaultAzureCredential."""
        return bool(self.blob_account_url)

    # ---- Redis ----
    # Two auth modes:
    #   1. URL-based (default): credentials embedded in REDIS_URL.
    #      - Local:  redis://localhost:6379/0
    #      - Azure access key:  rediss://:<KEY>@<name>.redis.cache.windows.net:6380/0
    #   2. Entra ID: set REDIS_USE_ENTRA=true and REDIS_HOST. The backend mints
    #      a short-lived AAD token via DefaultAzureCredential (picks up
    #      `az login` locally, Managed Identity in Azure) and uses it as the
    #      Redis password. REDIS_ENTRA_USERNAME should be the object id (oid)
    #      of the principal (user / managed identity / service principal) that
    #      has the "Data Owner"/"Data Contributor" Redis access policy.
    redis_url: str = "redis://localhost:6379/0"
    redis_use_entra: bool = False
    redis_host: str = ""
    redis_port: int = 6380
    redis_db: int = 0
    redis_ssl: bool = True
    redis_entra_username: str = ""
    # AAD scope for Azure Cache for Redis.
    redis_entra_scope: str = "https://redis.azure.com/.default"

    # ---- App ----
    # `stub` is only legal when `local_dev=True` (or unit tests). `fake_oidc`
    # remains for local-dev OIDC exercise without a real tenant. Production
    # MUST run `oidc`.
    auth_mode: Literal["stub", "fake_oidc", "oidc", "saml"] = "stub"
    # When true, relaxes prod safety checks (e.g. allows AUTH_MODE=stub /
    # fake_oidc). Set in .env.local; never set in cloud env.
    local_dev: bool = False
    classifier_provider: Literal["stub", "llm"] = "stub"
    max_bundle_bytes: int = 10 * 1024 * 1024
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ---- Stub auth role allowlists (comma-separated emails) ----
    manager_emails: str = "manager@org"
    admin_emails: str = "admin@org"

    # ---- OIDC / Entra (M1) ----
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_group_id_admin: str = ""
    # Optional override for the issuer (defaults to login.microsoftonline.com/{tenant}/v2.0).
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_jwks_cache_ttl_seconds: int = 3600

    # ---- API keys (M1) ----
    apikey_pepper: str = "dev-pepper-do-not-use-in-prod"
    apikey_prefix: str = "sh_live_"
    apikey_cache_ttl_seconds: int = 60

    # ---- Telemetry (M1) ----
    appinsights_connection_string: str = ""
    otel_service_role: str = "api"

    # ---- Worker tuning ----
    classifier_queue_key: str = "queue:classifier"
    classifier_blpop_timeout_seconds: int = 5

    # ---- Cache TTLs (seconds) ----
    cache_list_ttl_seconds: int = 60
    cache_item_ttl_seconds: int = 300
    publish_lock_ttl_seconds: int = 30

    # ---- Curator (M2) ----
    curator_stale_days: int = 30
    curator_archive_days: int = 90
    curator_lock_ttl_seconds: int = 1800
    curator_snapshot_retention: int = 5
    curator_schedule_cron: str = "0 3 * * *"
    curator_runs_container_prefix: str = "runs"
    curator_snapshots_retired_prefix: str = "_retired"
    curator_reports_container: str = "curator"
    usage_loaders_30d_window_days: int = 30
    janitor_classifier_stale_multiplier: int = 5

    # ---- Aux model: curator review (M3) ----
    # Provider toggle (only "foundry" or test-only "fake" supported).
    curator_review_provider: Literal["foundry", "fake"] = "foundry"

    # Azure AI Foundry endpoint config.
    foundry_endpoint: str = ""  # e.g. "https://my-foundry.services.ai.azure.com/models"
    foundry_deployment: str = ""  # deployment name or model id
    foundry_api_version: str = "2024-08-01-preview"

    # Auth: prefer Managed Identity in Azure; fall back to API key for local dev only.
    azure_ai_foundry_api_key: str = ""

    # Per-call token caps (passed to the Foundry SDK; truncation happens at the model).
    curator_review_max_input_tokens: int = 6000
    curator_review_max_output_tokens: int = 1500

    # Per-run hard caps. Breach => abort + record aborted_reason="cost_cap".
    curator_review_max_skills_per_run: int = 50
    curator_review_max_total_tokens_per_run: int = 400_000

    # Candidate filter knobs.
    curator_review_agent_uploader_prefix: str = "agent:"
    curator_review_consolidation_min_cosine: float = 0.75
    curator_review_consolidation_max_pairs: int = 20

    # Schedule for the optional second cron job.
    curator_review_schedule_cron: str = "30 3 * * *"
    curator_review_enabled: bool = False  # off by default; enable per-env.

    # Subfolder under {curator_reports_container} for review reports.
    curator_reviews_prefix: str = "reviews"

    def manager_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.manager_emails.split(",") if e.strip()}

    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolved_oidc_issuer(self) -> str:
        if self.oidc_issuer:
            return self.oidc_issuer
        if self.entra_tenant_id:
            return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"
        return ""

    def resolved_oidc_jwks_url(self) -> str:
        if self.oidc_jwks_url:
            return self.oidc_jwks_url
        if self.entra_tenant_id:
            return f"https://login.microsoftonline.com/{self.entra_tenant_id}/discovery/v2.0/keys"
        return ""

    @model_validator(mode="after")
    def _validate_oidc(self) -> Settings:
        if self.auth_mode == "oidc":
            missing = [
                n
                for n, v in {
                    "entra_tenant_id": self.entra_tenant_id,
                    "entra_client_id": self.entra_client_id,
                    "entra_group_id_admin": self.entra_group_id_admin,
                }.items()
                if not v
            ]
            if missing:
                raise ValueError(
                    f"AUTH_MODE=oidc requires the following settings to be non-empty: "
                    f"{', '.join(missing)}"
                )
        return self

    def enforce_production_safety(self) -> None:
        """Refuse insecure auth modes outside local dev.

        Called from `app.py` startup. Unit tests instantiate `Settings`
        directly and never reach this code path, so test fixtures still
        work with `auth_mode="stub"` and `local_dev=False`. Production
        boot will fail loudly if someone sets `AUTH_MODE=stub` without
        also opting into `LOCAL_DEV=1`.
        """
        if self.auth_mode in {"stub", "fake_oidc"} and not self.local_dev:
            raise RuntimeError(
                f"AUTH_MODE={self.auth_mode!r} is only permitted when "
                f"LOCAL_DEV=1. Refusing to start in an insecure auth mode. "
                f"Set AUTH_MODE=oidc for production."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — safe to call from anywhere."""
    return Settings()  # type: ignore[call-arg]
