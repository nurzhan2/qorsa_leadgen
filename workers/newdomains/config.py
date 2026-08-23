"""Configuration: .env-backed runtime settings. No YAML config files here
(unlike the other workers) - this worker's only real input is whichever
paid provider is configured.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/newdomains/.env. The
    env file path is anchored to this module's directory (not the process
    CWD) so `python -m workers.newdomains.main` finds it regardless of
    where it's invoked from.

    `domains_provider`/`domains_api_key`/`domains_api_url` are all
    deliberately OPTIONAL (not required fields) so main.py can check for
    them at runtime and log a friendly "not configured, exiting" message
    instead of pydantic raising a ValidationError with a stack trace -
    this worker must not crash just because the paid feed isn't set up
    yet.
    """

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    domains_provider: str | None = None
    domains_api_key: str | None = None
    domains_api_url: str | None = None
    # Comma-separated, e.g. "ru,com" - kept as a plain string (not a list)
    # since pydantic-settings' list parsing from a .env value needs
    # JSON-ish syntax, which is an awkward thing to ask a user to type.
    domains_tlds: str = "ru,com"

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    target_per_day: int = 300
    run_once: bool = True
    loop_interval_hours: int = 24

    site_check_timeout_seconds: float = 8.0

    def tlds(self) -> list[str]:
        return [t.strip() for t in self.domains_tlds.split(",") if t.strip()]
