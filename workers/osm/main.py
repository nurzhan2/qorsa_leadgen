"""Entry point: `python -m workers.osm.main` (run from the repo root, with
the Java core already up). No API key needed - Overpass is free/public."""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings, load_categories, load_cities
from .core_client import CoreClient
from .overpass_client import OverpassClient
from .runner import OsmRunner


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

    cities = load_cities(settings.cities_file).cities
    categories = load_categories(settings.categories_file).categories
    log.info(
        "osm.starting",
        core_url=settings.core_url,
        cities=len(cities),
        categories=len(categories),
        target_per_day=settings.target_per_day,
        run_once=settings.run_once,
    )

    # Generous overall timeout - the per-request timeout is set explicitly
    # per Overpass call in OverpassClient (Overpass itself is slow).
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds + 15) as http_client:
        client = OverpassClient(
            http_client,
            user_agent=settings.osm_user_agent,
            request_delay_seconds=settings.request_delay_seconds,
            request_timeout_seconds=settings.request_timeout_seconds,
            page_size=settings.page_size,
        )
        core = CoreClient(settings.core_url)
        runner = OsmRunner(settings, cities, categories, client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("osm.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("osm.sleeping_until_next_run", hours=settings.loop_interval_hours)
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
