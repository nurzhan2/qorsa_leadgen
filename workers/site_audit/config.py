"""Configuration: .env-backed runtime settings. Pure data, no network."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent

DEFAULT_USER_AGENT = (
    "qorsa-leadgen-audit/1.0 "
    "(site quality probe; reads the homepage only; respects robots.txt)"
)


class Settings(BaseSettings):
    """Loaded from environment variables / workers/site_audit/.env. The env
    file path is anchored to this module's directory (not the process CWD) so
    `python -m workers.site_audit.main` finds it regardless of where it's
    invoked from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # tolerate the BOM Notepad/PowerShell add
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # --- PageSpeed Insights ---
    # Empty = skip PSI entirely and report only the local probe. PSI is free
    # and needs no billing, unlike the Places API - see README.
    psi_api_key: str = ""
    # "mobile" is the one that matters for these leads: a Russian small
    # business gets most of its traffic from a phone, and mobile scores are
    # far harsher than desktop ones.
    psi_strategy: str = "mobile"
    psi_timeout_seconds: float = Field(default=90.0, gt=0)
    # PSI runs a real Lighthouse pass per URL and is slow; this is a hard cap
    # per run so a scheduled job can't stall for hours.
    psi_max_per_run: int = Field(default=200, ge=0)

    # --- volume ---
    audit_batch: int = Field(default=50, ge=1, le=500)
    max_batches_per_run: int = Field(default=10, ge=1)
    # Kept low on purpose: PSI is rate-limited per key, and the local probe
    # hits a different site each time anyway.
    audit_concurrency: int = Field(default=3, ge=1, le=20)

    # --- politeness (local probe) ---
    request_delay_seconds: float = Field(default=1.0, ge=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    respect_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    max_page_bytes: int = Field(default=3_000_000, ge=10_000)

    # --- local probe thresholds ---
    # Response time above this counts as a failed check.
    slow_response_seconds: float = Field(default=3.0, gt=0)
    # Homepage transfer size above this counts as a failed check. 2 MB for a
    # small-business homepage is already heavy on mobile data.
    heavy_page_bytes: int = Field(default=2_000_000, ge=100_000)

    run_once: bool = True
    loop_interval_minutes: int = Field(default=180, ge=1)


def load_settings() -> Settings:
    return Settings()
