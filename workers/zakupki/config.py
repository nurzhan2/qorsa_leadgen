"""Configuration: .env-backed runtime settings plus the keywords.yml loader.
Pure data/IO, no network - trivial to unit test and to extend without
touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/zakupki/.env. The env
    file path is anchored to this module's directory (not the process CWD)
    so `python -m workers.zakupki.main` finds it regardless of where it's
    invoked from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: tolerate a UTF-8 BOM, which
        # Notepad/PowerShell on Windows silently prepend when saving a .env.
        # With plain "utf-8" the BOM becomes part of the FIRST key's name
        # ("U+FEFF TG_API_ID"), so that variable silently goes missing.
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    target_per_day: int = 300
    run_once: bool = True
    loop_interval_hours: int = 24

    results_per_page: int = 10
    max_pages_per_keyword: int = 3
    request_delay_seconds: float = 2.0

    # Visiting each purchase's own detail/card page (for budget/subject/
    # deadline/phone) is a SECOND request per purchase, on top of the
    # search page - see README.md "Два уровня запросов". FETCH_DETAILS
    # lets you turn that off entirely; MAX_DETAILS caps how many detail
    # pages get fetched in one run regardless (None = same as
    # TARGET_PER_DAY, since there's never a reason to fetch more detail
    # pages than leads we could possibly send).
    fetch_details: bool = True
    max_details: int | None = None

    # zakupki.gov.ru's TLS certificate is issued by a Russian CA
    # ("Минцифры России"/"Russian Trusted CA") that isn't in most default
    # OS/browser trust stores outside Russia, so verification fails with a
    # generic "untrusted root" error on many machines even though the
    # connection itself is fine. Set to false ONLY if you've hit that and
    # understand the tradeoff (see README.md) - the honest fix is
    # installing the Russian Trusted Root CA, not disabling verification.
    verify_ssl: bool = True

    keywords_file: Path = MODULE_DIR / "keywords.yml"

    def max_details_or_default(self) -> int:
        return self.max_details if self.max_details is not None else self.target_per_day


class KeywordsConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)


def load_keywords(path: Path) -> KeywordsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return KeywordsConfig.model_validate(data)
