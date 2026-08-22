"""Configuration: .env-backed runtime settings plus loaders for the two
human-editable YAML files (cities.yml, rubrics.yml). Pure data/IO, no
network - trivial to unit test and to extend without touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/twogis/.env. The env
    file path is anchored to this module's directory (not the process CWD)
    so `python -m workers.twogis.main` finds it regardless of where it's
    invoked from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    twogis_api_key: str
    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # Ceiling on how many companies to ingest in one run, so this doesn't
    # hammer 2GIS's (rate-limited, free-tier) API indefinitely.
    target_per_day: int = 300
    # True = one pass and exit (e.g. for a daily cron job). False = loop,
    # sleeping loop_interval_hours between passes.
    run_once: bool = True
    loop_interval_hours: int = 24

    page_size: int = 20
    max_pages_per_combo: int = 3
    request_delay_seconds: float = 0.34
    max_concurrent_requests: int = 2

    cities_file: Path = MODULE_DIR / "cities.yml"
    rubrics_file: Path = MODULE_DIR / "rubrics.yml"


class CitiesConfig(BaseModel):
    cities: list[str] = Field(default_factory=list)


class Rubric(BaseModel):
    """One business category to search for. `query` is the free-text term
    combined with a city name to search 2GIS (e.g. "кафе Казань")."""

    name: str
    query: str


class RubricsConfig(BaseModel):
    rubrics: list[Rubric] = Field(default_factory=list)


def load_cities(path: Path) -> CitiesConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return CitiesConfig.model_validate(data)


def load_rubrics(path: Path) -> RubricsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RubricsConfig.model_validate(data)
