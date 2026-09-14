"""Entry point: `python -m workers.enrich.main` (run from the repo root, with
the Java core already up on CORE_URL).
"""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings
from .core_client import CoreClient
from .enricher import SiteEnricher
from .fetcher import PoliteFetcher
from .runner import EnrichRunner


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

    log.info(
        "enrich.starting",
        core_url=settings.core_url,
        batch=settings.enrich_batch,
        max_batches=settings.max_batches_per_run,
        concurrency=settings.enrich_concurrency,
        delay_seconds=settings.request_delay_seconds,
        respect_robots=settings.respect_robots,
        user_agent=settings.user_agent,
    )
    if not settings.respect_robots:
        log.warning("enrich.robots_disabled",
                    hint="RESPECT_ROBOTS=false: robots.txt is NOT being checked. Only acceptable "
                         "against sites you own - see README.md 'robots.txt'.")

    limits = httpx.Limits(max_connections=settings.enrich_concurrency * 2,
                          max_keepalive_connections=settings.enrich_concurrency)
    core = CoreClient(settings.core_url)
    try:
        async with httpx.AsyncClient(limits=limits, timeout=settings.request_timeout_seconds) as http_client:
            while True:
                # A fresh fetcher per run: robots.txt is cached for one run only,
                # never across the hours between runs in loop mode.
                fetcher = PoliteFetcher(
                    http_client,
                    user_agent=settings.user_agent,
                    respect_robots=settings.respect_robots,
                    request_delay_seconds=settings.request_delay_seconds,
                    max_crawl_delay_seconds=settings.max_crawl_delay_seconds,
                    request_timeout_seconds=settings.request_timeout_seconds,
                    max_page_bytes=settings.max_page_bytes,
                )
                enricher = SiteEnricher(fetcher, max_contact_pages=settings.max_contact_pages)
                runner = EnrichRunner(settings, core, enricher)
                summary = await runner.run_once()
                log.info("enrich.run_finished", requests_to_sites=fetcher.requests_made, **summary)
                if settings.run_once:
                    break
                log.info("enrich.sleeping_until_next_run", minutes=settings.loop_interval_minutes)
                await asyncio.sleep(settings.loop_interval_minutes * 60)
    finally:
        await core.aclose()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
