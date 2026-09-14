"""Configuration: .env-backed runtime settings. Pure data, no network."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent

# Name + purpose, no email address, no browser disguise. The first token
# ("qorsa-leadgen-enrich") is what a site's robots.txt would address us by in
# a `User-agent:` line - see fetcher.product_token().
DEFAULT_USER_AGENT = (
    "qorsa-leadgen-enrich/1.0 "
    "(contact enrichment bot; reads public contact pages only; respects robots.txt)"
)


class Settings(BaseSettings):
    """Loaded from environment variables / workers/enrich/.env. The env file
    path is anchored to this module's directory (not the process CWD) so
    `python -m workers.enrich.main` finds it regardless of where it's
    invoked from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: tolerate a UTF-8 BOM, which
        # Notepad/PowerShell on Windows silently prepend when saving a .env.
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # --- volume ---
    # Companies per GET /pending-enrich, and how many such batches one run may
    # take before stopping (the queue usually empties first).
    enrich_batch: int = Field(default=50, ge=1, le=500)
    max_batches_per_run: int = Field(default=10, ge=1)
    # Sites processed at the same time.
    enrich_concurrency: int = Field(default=5, ge=1, le=50)

    # --- politeness ---
    # Pause between two requests to the same site, and between two sites
    # handled by the same concurrency slot. A robots.txt Crawl-delay larger
    # than this wins, up to max_crawl_delay_seconds.
    request_delay_seconds: float = Field(default=2.0, ge=0)
    max_crawl_delay_seconds: float = Field(default=30.0, ge=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    # Leave on. Off is for checking your OWN site - see README "robots.txt".
    respect_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT

    # --- per-site limits ---
    # Contact/about pages followed from the homepage (the homepage itself is
    # always read).
    max_contact_pages: int = Field(default=3, ge=0, le=10)
    # A page is truncated past this many bytes - contacts are never 2 MB deep.
    max_page_bytes: int = Field(default=2_000_000, ge=10_000)

    # True = one run and exit (daily scheduled task). False = loop, sleeping
    # loop_interval_minutes between runs.
    run_once: bool = True
    loop_interval_minutes: int = Field(default=60, ge=1)
