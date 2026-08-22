"""Entry point: `python -m workers.twogis.main` (run from the repo root,
with the Java core already up)."""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings, load_cities, load_rubrics
from .core_client import CoreClient
from .runner import TwoGisRunner
from .twogis_client import TwoGisClient


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
    rubrics = load_rubrics(settings.rubrics_file).rubrics
    log.info(
        "twogis.starting",
        core_url=settings.core_url,
        cities=len(cities),
        rubrics=len(rubrics),
        target_per_day=settings.target_per_day,
        run_once=settings.run_once,
    )

    async with httpx.AsyncClient(timeout=20.0) as http_client:
        client = TwoGisClient(
            settings.twogis_api_key,
            http_client,
            request_delay_seconds=settings.request_delay_seconds,
            max_concurrent_requests=settings.max_concurrent_requests,
            page_size=settings.page_size,
            max_pages_per_combo=settings.max_pages_per_combo,
        )
        core = CoreClient(settings.core_url)
        runner = TwoGisRunner(settings, cities, rubrics, client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("twogis.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("twogis.sleeping_until_next_run", hours=settings.loop_interval_hours)
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
