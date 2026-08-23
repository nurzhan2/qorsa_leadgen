"""Configuration: .env-backed runtime settings plus loaders for the two
human-editable YAML files (cities.yml, categories.yml). Pure data/IO, no
network - trivial to unit test and to extend without touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/google_places/.env. The
    env file path is anchored to this module's directory (not the process
    CWD) so `python -m workers.google_places.main` finds it regardless of
    where it's invoked from.

    `google_places_api_key` is deliberately OPTIONAL here (not a required
    field) so main.py can check for it at runtime and log a friendly
    "not set, exiting" message instead of pydantic raising a
    ValidationError with a stack trace - this worker must not crash just
    because billing/the key isn't set up yet.
    """

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_places_api_key: str | None = None
    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # Ceiling on how many companies to ingest in one run - Places API (New)
    # bills per request, this is a cost/quota guard as much as a rate limit.
    target_per_day: int = 300
    # True = one pass and exit (e.g. for a daily cron job). False = loop,
    # sleeping loop_interval_hours between passes.
    run_once: bool = True
    loop_interval_hours: int = 24

    max_pages_per_query: int = 3
    request_delay_seconds: float = 1.0
    # Text Search's nextPageToken isn't valid immediately - Google's own
    # docs note a short delay is needed before using it.
    page_token_delay_seconds: float = 2.0

    cities_file: Path = MODULE_DIR / "cities.yml"
    categories_file: Path = MODULE_DIR / "categories.yml"


class City(BaseModel):
    name: str


class CitiesConfig(BaseModel):
    cities: list[City] = Field(default_factory=list)


class Category(BaseModel):
    """One business category to search for. `query` is the free-text
    search term combined with a city name (e.g. "кафе" + "Казань" ->
    "кафе Казань")."""

    name: str
    query: str


class CategoriesConfig(BaseModel):
    categories: list[Category] = Field(default_factory=list)


def load_cities(path: Path) -> CitiesConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return CitiesConfig.model_validate(data)


def load_categories(path: Path) -> CategoriesConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return CategoriesConfig.model_validate(data)
