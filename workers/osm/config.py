"""Configuration: .env-backed runtime settings plus loaders for the two
human-editable YAML files (cities.yml, categories.yml). Pure data/IO, no
network - trivial to unit test and to extend without touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/osm/.env. The env file
    path is anchored to this module's directory (not the process CWD) so
    `python -m workers.osm.main` finds it regardless of where it's
    invoked from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    # Overpass's usage policy asks for a User-Agent that identifies the
    # tool - but the public instance's own front end (verified live,
    # repeatedly) returns 406 for ANY User-Agent containing "@", a URL
    # scheme ("://"), or an obfuscated-email pattern ("x at y dot z") -
    # exactly the contact-info formats that policy suggests. A plain
    # descriptive string with none of those is what actually works. See
    # README.md "User-Agent: важное ограничение" before changing this.
    osm_user_agent: str = "qorsa-leadgen-osm-worker/1.0"

    # Ceiling on how many companies to ingest in one run.
    target_per_day: int = 300
    # True = one pass and exit (e.g. for a daily cron job). False = loop,
    # sleeping loop_interval_hours between passes.
    run_once: bool = True
    loop_interval_hours: int = 24

    # Overpass has no page/pageSize pagination like a REST API - each
    # (city, category) combo pulls up to this many elements in ONE query
    # (via Overpass QL's `out tags <N>;` limit) and moves on.
    page_size: int = 50
    # Overpass's public instance rate-limits hard: this worker pauses
    # this long between every request, and retries 429/504 with a long
    # exponential backoff (not just this worker being polite - too fast
    # and the public instance blocks the IP for a while).
    request_delay_seconds: float = 3.0
    request_timeout_seconds: float = 75.0

    cities_file: Path = MODULE_DIR / "cities.yml"
    categories_file: Path = MODULE_DIR / "categories.yml"


class City(BaseModel):
    """One searchable city, as a bounding box: [south, west, north, east]
    (latitude/longitude degrees) - the format Overpass QL's bbox filter
    expects directly."""

    name: str
    bbox: list[float]

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(cls, value: list[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("bbox must have exactly 4 numbers: [south, west, north, east]")
        south, west, north, east = value
        if not (south < north and west < east):
            raise ValueError("bbox must satisfy south < north and west < east")
        return value

    @property
    def as_tuple(self) -> tuple[float, float, float, float]:
        return tuple(self.bbox)  # (south, west, north, east)


class CitiesConfig(BaseModel):
    cities: list[City] = Field(default_factory=list)


class Category(BaseModel):
    """One OSM tag to search for. `value=None` matches any value for that
    key (Overpass QL `["shop"]` rather than `["shop"="clothes"]`)."""

    name: str
    key: str
    value: str | None = None


class CategoriesConfig(BaseModel):
    categories: list[Category] = Field(default_factory=list)


def load_cities(path: Path) -> CitiesConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return CitiesConfig.model_validate(data)


def load_categories(path: Path) -> CategoriesConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return CategoriesConfig.model_validate(data)
