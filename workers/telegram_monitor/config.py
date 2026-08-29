"""Configuration: .env-backed runtime settings plus loaders for the two
human-editable YAML files (channels.yml, keywords.yml). Nothing here talks
to Telegram or the core - it only turns files into validated pydantic
models, so it's trivial to unit test and to extend without touching code.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODULE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Loaded from environment variables / workers/telegram_monitor/.env.

    The env file path is anchored to this module's directory (not the
    process CWD) so `python -m workers.telegram_monitor.main` finds it
    regardless of where it's invoked from.
    """

    model_config = SettingsConfigDict(
        env_file=str(MODULE_DIR / ".env"),
        env_file_encoding="utf-8-sig",  # -sig: tolerate a UTF-8 BOM, which
        # Notepad/PowerShell on Windows silently prepend when saving a .env.
        # With plain "utf-8" the BOM becomes part of the FIRST key's name
        # ("U+FEFF TG_API_ID"), so that variable silently goes missing.
        extra="ignore",
    )

    tg_api_id: int
    tg_api_hash: str
    tg_session: str = "leadgen_session"
    core_url: str = "http://localhost:8081"
    log_level: str = "INFO"

    channels_file: Path = MODULE_DIR / "channels.yml"
    keywords_file: Path = MODULE_DIR / "keywords.yml"
    channel_cache_file: Path = MODULE_DIR / "channel_cache.json"

    # channel_rater.py only: how many recent posts to sample per channel,
    # and how long to pause between channels (a separate, lighter-weight
    # pacing knob than monitor.py's live POST_SEND_DELAY_SECONDS, since
    # rating reads history in bulk rather than reacting to live messages).
    rate_sample: int = 50
    rate_channel_delay_seconds: float = 2.0

    # --- Channel resolution / FloodWait policy (see resolver.py) ---
    # Pause between consecutive ResolveUsername calls. Only applies to
    # channels the cache couldn't answer for, so on a warm start it never
    # comes into play at all.
    resolve_delay_seconds: float = 2.0
    # How long a cached resolve stays good. A channel's id/access_hash don't
    # change, so this is long by default; it mainly exists so membership and
    # renames eventually get re-checked on their own.
    cache_ttl_days: float = 30.0
    # If Telegram asks for a longer wait than this, stop resolving for this
    # run rather than sitting blocked - we keep whatever was resolved and
    # monitor those channels. We never ignore or shorten the wait itself.
    max_flood_wait_seconds: float = 300.0
    # Re-resolve everything, ignoring the cache. Needed after joining new
    # channels, since a cached "not_joined" is otherwise trusted until TTL.
    force_resolve: bool = False


class ChannelEntry(BaseModel):
    """One monitored channel/chat. Either `username` or `id` must be set -
    Telethon accepts both when resolving an entity."""

    username: str | None = None
    id: int | None = None

    @model_validator(mode="after")
    def _require_identifier(self) -> "ChannelEntry":
        if self.username is None and self.id is None:
            raise ValueError("channel entry needs either 'username' or 'id'")
        if self.username is not None:
            self.username = self.username.lstrip("@").strip()
        return self

    @property
    def target(self) -> str | int:
        """Whatever Telethon's get_entity() should be called with."""
        return self.username if self.username is not None else self.id


class ChannelsConfig(BaseModel):
    channels: list[ChannelEntry] = Field(default_factory=list)


class KeywordCategory(BaseModel):
    weight: int
    keywords: list[str]


class KeywordsConfig(BaseModel):
    intent: KeywordCategory
    domain: KeywordCategory
    budget: KeywordCategory
    # Plain keyword lists (no weight) - used by Matcher for the
    # accept/reject decision, not for scoring. See matcher.py and
    # keywords.yml for how they interact.
    anti_hiring: list[str] = Field(default_factory=list)
    order_signals: list[str] = Field(default_factory=list)


def load_channels(path: Path) -> ChannelsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ChannelsConfig.model_validate(data)


def load_keywords(path: Path) -> KeywordsConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return KeywordsConfig.model_validate(data)
