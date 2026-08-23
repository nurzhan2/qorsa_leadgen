"""Entry point: `python -m workers.google_places.main` (run from the repo
root, with the Java core already up).

Requires GOOGLE_PLACES_API_KEY with an active Google Cloud billing
account (Places API (New) has no meaningful free tier - even the trial
requires a billing account on file). If the key isn't set, this exits
cleanly with a friendly log message instead of crashing - the user's
billing may simply not be set up yet, and that shouldn't look like a bug.
"""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings, load_categories, load_cities
from .core_client import CoreClient
from .places_client import GooglePlacesClient
from .runner import GooglePlacesRunner


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
    )


async def _run() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)
    log = structlog.get_logger(__name__)

    if not settings.google_places_api_key:
        log.warning(
            "google_places.not_configured",
            reason="GOOGLE_PLACES_API_KEY not set, exiting",
            hint="set GOOGLE_PLACES_API_KEY in workers/google_places/.env - "
            "requires an active Google Cloud billing account, see README.md",
        )
        return

    cities = load_cities(settings.cities_file).cities
    categories = load_categories(settings.categories_file).categories
    log.info(
        "google_places.starting",
        core_url=settings.core_url,
        cities=len(cities),
        categories=len(categories),
        target_per_day=settings.target_per_day,
        run_once=settings.run_once,
    )

    async with httpx.AsyncClient(timeout=20.0) as http_client:
        client = GooglePlacesClient(
            settings.google_places_api_key,
            http_client,
            request_delay_seconds=settings.request_delay_seconds,
            page_token_delay_seconds=settings.page_token_delay_seconds,
            max_pages_per_query=settings.max_pages_per_query,
        )
        core = CoreClient(settings.core_url)
        runner = GooglePlacesRunner(settings, cities, categories, client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("google_places.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("google_places.sleeping_until_next_run", hours=settings.loop_interval_hours)
                await asyncio.sleep(settings.loop_interval_hours * 3600)
        finally:
            await core.aclose()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
