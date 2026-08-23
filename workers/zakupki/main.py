"""Entry point: `python -m workers.zakupki.main` (run from the repo root,
with the Java core already up). No API key needed - zakupki.gov.ru's
search is public."""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings, load_keywords
from .core_client import CoreClient
from .runner import ZakupkiRunner
from .zakupki_client import ZakupkiClient


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

    keywords = load_keywords(settings.keywords_file).keywords
    log.info(
        "zakupki.starting",
        core_url=settings.core_url,
        keywords=len(keywords),
        target_per_day=settings.target_per_day,
        run_once=settings.run_once,
        verify_ssl=settings.verify_ssl,
    )
    if not settings.verify_ssl:
        log.warning("zakupki.tls_verification_disabled", hint="see README.md before using this in production")

    async with httpx.AsyncClient(
        timeout=30.0,
        verify=settings.verify_ssl,
        headers={"User-Agent": "qorsa-leadgen-zakupki-worker/1.0"},
    ) as http_client:
        client = ZakupkiClient(
            http_client,
            request_delay_seconds=settings.request_delay_seconds,
            results_per_page=settings.results_per_page,
        )
        core = CoreClient(settings.core_url)
        runner = ZakupkiRunner(settings, keywords, client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("zakupki.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("zakupki.sleeping_until_next_run", hours=settings.loop_interval_hours)
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
