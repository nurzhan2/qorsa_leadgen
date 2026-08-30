"""Entry point: `python -m workers.hh.main` (run from the repo root, with
the Java core already up).

Needs HH_TOKEN - see README.md "Доступ к API: нужен токен".
"""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings, load_areas, load_keywords
from .core_client import CoreClient
from .hh_client import HhClient
from .runner import HhRunner


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
    areas = load_areas(settings.areas_file).areas

    log.info(
        "hh.starting",
        core_url=settings.core_url,
        keywords=len(keywords),
        areas=len(areas),
        queries=len(keywords) * len(areas),
        stale_days=settings.stale_days,
        target_per_day=settings.target_per_day,
        fetch_employer_details=settings.fetch_employer_details,
        has_token=bool(settings.hh_token.strip()),
    )

    if not settings.hh_token.strip():
        # Not fatal - if HH ever serves this network anonymously again, or
        # you're on a network where it does, the run should still be allowed
        # to try. But it will almost certainly 403, so say why up front
        # instead of leaving a wall of failures to interpret.
        log.warning(
            "hh.no_token",
            hint="HH_TOKEN is empty. HH's docs: без токена после первого запроса "
                 "предлагается капча; на практике /vacancies отвечает 403. "
                 "Register an app at https://dev.hh.ru/admin - see README.md.",
        )

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds + 10) as http_client:
        client = HhClient(
            http_client,
            user_agent=settings.hh_user_agent,
            token=settings.hh_token,
            base_url=settings.hh_api_base,
            request_delay_seconds=settings.request_delay_seconds,
            request_timeout_seconds=settings.request_timeout_seconds,
            per_page=settings.per_page,
        )
        core = CoreClient(settings.core_url)
        runner = HhRunner(settings, keywords, areas, client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("hh.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("hh.sleeping_until_next_run", hours=settings.loop_interval_hours)
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
