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
        env_file_encoding="utf-8-sig",  # -sig: tolerate a UTF-8 BOM, which
        # Notepad/PowerShell on Windows silently prepend when saving a .env.
        # With plain "utf-8" the BOM becomes part of the FIRST key's name
        # ("U+FEFF TG_API_ID"), so that variable silently goes missing.
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
    # and the public instance blocks the IP for a while). Raised from 3s
    # now that the grid is 30 cities x 36 categories rather than 2 x 14.
    request_delay_seconds: float = 5.0
    request_timeout_seconds: float = 75.0

    # Comma-separated Overpass instances, tried in order. When one keeps
    # answering 429/504 the client moves to the next - these are public
    # mirrors that exist precisely so load can spread across them. This is
    # NOT a way to multiply our quota: the pacing and backoff apply the same
    # to every endpoint, we just stop leaning on one instance that's already
    # telling us it's busy.
    overpass_endpoints: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass.osm.ch/api/interpreter"
    )

    # --- Incremental crawling (see README "Режим постепенного обхода") ---
    # How many (city, category) pairs to process per run. 30x36 = 1080 pairs
    # is far too many for one sitting; this makes a run a bounded slice and
    # lets a schedule (hourly cron) work through the grid over time.
    combos_per_run: int = 50
    # Where the "which pairs are already done" checkpoint lives.
    progress_file: Path = MODULE_DIR / "osm_progress.json"
    # Start the grid over, discarding the checkpoint.
    reset_progress: bool = False

    # --- Data quality ---
    # Comma-separated chain/franchise names. A company whose name contains one
    # of these AS A WHOLE WORD is skipped: national chains already have a site
    # and an in-house IT team, so they're noise for this pipeline, not leads.
    # See chains.py for the matching rules (whole-word, not raw substring -
    # "Магнит" must not filter out "Магнитогорская аптека").
    #
    # Keep entries specific. A needle that is also an ordinary Russian word
    # ("Метро", "Верный", "Бургер") filters real independent businesses, which
    # is a silent loss - the opposite of what this list is for.
    chain_stoplist: str = (
        "Пятёрочка,Пятерочка,Магнит,Магнит Косметик,Перекрёсток,Перекресток,Дикси,"
        "Ашан,Auchan,ВкусВилл,Красное и Белое,Красное Белое,Бристоль,SPAR,"
        "Сбербанк,Сбер,ВТБ,Альфа-Банк,Альфа Банк,Тинькофф,Газпромбанк,Росбанк,"
        "Райффайзен,Россельхозбанк,Почта России,"
        "Ozon,Озон,Wildberries,Вайлдберриз,Яндекс,Yandex,СДЭК,CDEK,Boxberry,"
        "McDonalds,McDonald's,Макдоналдс,Вкусно и точка,KFC,Ростикс,"
        "Burger King,Бургер Кинг,Subway,Сабвэй,Додо Пицца,Dodo Pizza,"
        "Papa Johns,Папа Джонс,Шоколадница,Кофе Хауз,Starbucks,Старбакс,Cofix,"
        "Fix Price,Фикс Прайс,Летуаль,Л'Этуаль,Рив Гош,Улыбка Радуги,Подружка,"
        "Эльдорадо,М.Видео,МВидео,DNS,ДНС,Ситилинк,"
        "Леруа Мерлен,Leroy Merlin,OBI,Петрович,Максидом,Hoff,IKEA,ИКЕА,"
        "МТС,Билайн,Мегафон,Tele2,Теле2,Ростелеком,"
        "Ригла,Горздрав,Асна,Планета Здоровья,Аптечная сеть 36.6,"
        "Спортмастер,Decathlon,Декатлон,Adidas,Nike,Zara,H&M,Uniqlo,Gloria Jeans"
    )

    cities_file: Path = MODULE_DIR / "cities.yml"
    categories_file: Path = MODULE_DIR / "categories.yml"

    @property
    def endpoints(self) -> list[str]:
        """overpass_endpoints parsed into a clean, de-duplicated list."""
        seen, result = set(), []
        for raw in self.overpass_endpoints.split(","):
            url = raw.strip()
            if url and url not in seen:
                seen.add(url)
                result.append(url)
        return result

    @property
    def stoplist(self) -> list[str]:
        """chain_stoplist parsed into a list of lowercase needles."""
        seen, result = set(), []
        for raw in self.chain_stoplist.split(","):
            needle = raw.strip().lower()
            if needle and needle not in seen:
                seen.add(needle)
                result.append(needle)
        return result


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

        # Range check FIRST, because it's what catches the classic mistake:
        # writing [lon, lat, lon, lat] instead of [lat, lon, lat, lon]. For an
        # eastern city like Владивосток (43.1 N, 131.9 E) a swap puts 131.9
        # in a latitude slot, which is impossible - but the ordering check
        # alone would happily accept it and then silently return zero results
        # for that city forever.
        for label, latitude in (("south", south), ("north", north)):
            if not -90.0 <= latitude <= 90.0:
                raise ValueError(
                    f"{label} latitude {latitude} is outside -90..90 - "
                    "bbox is [south, west, north, east]; lat/lon look swapped"
                )
        for label, longitude in (("west", west), ("east", east)):
            if not -180.0 <= longitude <= 180.0:
                raise ValueError(f"{label} longitude {longitude} is outside -180..180")

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
