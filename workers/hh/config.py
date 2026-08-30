"""Configuration: .env-backed runtime settings plus loaders for the two
human-editable YAML files (keywords.yml, areas.yml). Pure data/IO, no
network - trivial to unit test and to extend without touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/hh/.env. The env file
    path is anchored to this module's directory (not the process CWD) so
    `python -m workers.hh.main` finds it regardless of where it's invoked
    from."""

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: tolerate a UTF-8 BOM, which
        # Notepad/PowerShell on Windows silently prepend when saving a .env.
        # With plain "utf-8" the BOM becomes part of the FIRST key's name,
        # so that variable silently goes missing.
        extra="ignore",
    )

    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    hh_api_base: str = "https://api.hh.ru"

    # HH requires a User-Agent that identifies the application, and REJECTS
    # some of them outright. Verified live: a UA containing an email address
    # ("MyApp/1.0 (me@example.com)" - the format HH's own older docs
    # suggested) comes back
    #   400 {"errors":[{"value":"blacklisted","type":"bad_user_agent"}]}
    # A plain descriptive string with no email is what actually passes. See
    # README.md "User-Agent" before changing this.
    hh_user_agent: str = "qorsa-leadgen-hh-worker/1.0"

    # OAuth application token. HH's own docs for GET /vacancies state:
    # "Если не передан токен авторизации, то после первого запроса будет
    # предложено пройти капчу." Anonymous access is therefore not viable for
    # a worker - see README.md "Доступ к API: нужен токен". Register an app
    # at https://dev.hh.ru/admin and put the token here. Never hardcoded.
    hh_token: str = ""

    # Ceiling on how many companies to ingest in one run.
    target_per_day: int = 300
    # True = one pass and exit (e.g. for a daily cron job). False = loop,
    # sleeping loop_interval_hours between passes.
    run_once: bool = True
    loop_interval_hours: int = 24

    # --- The point of this worker: stale-vacancy detection ---
    # A vacancy still open after this many days means the employer has been
    # unable to hire. That's the moment they start considering outsourcing -
    # which is exactly when we want to reach them. See staleness.py.
    stale_days: int = 45

    # --- Paging / rate limiting ---
    per_page: int = 100          # HH's documented maximum
    max_pages_per_query: int = 5  # per (keyword, area) pair
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 30.0

    # A second request per employer to read site_url (the search result does
    # NOT carry it - confirmed against HH's OpenAPI spec). Doubles request
    # volume in exchange for `domain` and the employer `type`, which is what
    # lets us drop кадровые агентства using HH's own classification.
    fetch_employer_details: bool = True
    max_employer_details: int = 200

    keywords_file: Path = MODULE_DIR / "keywords.yml"
    areas_file: Path = MODULE_DIR / "areas.yml"

    # --- Filtering ---
    # Recruiting agencies and staffing firms: they hire on someone else's
    # behalf, so the vacancy tells us nothing about THEIR own need for a
    # website. Matched as whole words - see filters.py.
    agency_stoplist: str = (
        "кадровое агентство,кадровый центр,агентство занятости,рекрутинговое агентство,"
        "рекрутинг,recruiting,recruitment,staffing,хедхантинг,headhunting,"
        "аутстаффинг,аутсорсинг персонала,подбор персонала,кадры,персонал-сервис,"
        "ancor,анкор,kelly services,manpower,adecco,randstad,hays,unity,coleman,"
        "brainstorm,флагман персонал,агентство подбора,ратмир,профи-групп"
    )
    # Corporations with their own IT departments. They will never buy a
    # website from us; their vacancies are pure noise.
    giant_stoplist: str = (
        "сбербанк,сбер,сбертех,яндекс,yandex,ozon,озон,wildberries,вайлдберриз,"
        "vk,вконтакте,mail.ru,тинькофф,tinkoff,т-банк,альфа-банк,втб,газпром,"
        "газпромбанк,роснефть,лукойл,ростелеком,мтс,мегафон,билайн,tele2,"
        "росатом,ржд,аэрофлот,магнит,x5,пятёрочка,перекрёсток,лента,"
        "касперский,kaspersky,луксофт,luxoft,epam,эпам,астра,positive technologies,"
        "авито,avito,циан,cian,додо,dodo,самокат,delivery club,деливери,"
        "почта россии,вайлдберис,мвидео,м.видео,dns,днс,эльдорадо,"
        "росбанк,открытие,райффайзен,совкомбанк,россельхозбанк,"
        "северсталь,нлмк,норникель,сибур,татнефть,новатэк,полюс,русал"
    )
    # HH's own employer classification. `agency` = Кадровое агентство,
    # `private_recruiter` = Частный рекрутер (verified live against
    # /dictionaries). Only available when fetch_employer_details is on.
    skip_employer_types: str = "agency,private_recruiter"
    # HH marks IT-accredited companies. An accredited IT company by
    # definition has in-house developers - not a customer for outsourced web
    # work. This flag IS present on the search result itself.
    skip_accredited_it: bool = True

    @property
    def agencies(self) -> list[str]:
        return _split(self.agency_stoplist)

    @property
    def giants(self) -> list[str]:
        return _split(self.giant_stoplist)

    @property
    def skipped_employer_types(self) -> list[str]:
        return _split(self.skip_employer_types)


def _split(raw: str) -> list[str]:
    seen, result = set(), []
    for part in (raw or "").split(","):
        value = part.strip().lower()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


class Area(BaseModel):
    """One HH region. `id` comes from https://api.hh.ru/areas and is a
    STRING in HH's API (e.g. "1" for Москва), not an int - kept as a string
    so it round-trips into the query unchanged."""

    name: str
    id: str

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value) -> str:
        # YAML happily parses `id: 1` as an int; HH wants "1".
        return str(value).strip()


class AreasConfig(BaseModel):
    areas: list[Area] = Field(default_factory=list)


class KeywordsConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)


def load_areas(path: Path) -> AreasConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return AreasConfig.model_validate(data)


def load_keywords(path: Path) -> KeywordsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return KeywordsConfig.model_validate(data)
