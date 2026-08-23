"""Entry point: `python -m workers.newdomains.main` (run from the repo
root, with the Java core already up).

Requires a paid newly-registered-domains feed (DOMAINS_PROVIDER +
DOMAINS_API_KEY). If not configured, this exits cleanly with a friendly
log message instead of crashing - the user simply hasn't paid for/wired
up a provider yet, and that shouldn't look like a bug. See README.md.
"""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings
from .core_client import CoreClient
from .providers import create_provider
from .runner import NewDomainsRunner


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

    provider = create_provider(settings)
    if provider is None:
        log.warning(
            "newdomains.not_configured",
            reason="NEWDOMAINS provider not configured, exiting",
            hint="set DOMAINS_PROVIDER + DOMAINS_API_KEY in workers/newdomains/.env - "
            "requires a paid feed, see README.md",
        )
        return

    log.info(
        "newdomains.starting",
        core_url=settings.core_url,
        provider=settings.domains_provider,
        target_per_day=settings.target_per_day,
        run_once=settings.run_once,
    )

    async with httpx.AsyncClient(timeout=20.0) as http_client:
        core = CoreClient(settings.core_url)
        runner = NewDomainsRunner(settings, provider, http_client, core)
        try:
            while True:
                summary = await runner.run_once()
                log.info("newdomains.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("newdomains.sleeping_until_next_run", hours=settings.loop_interval_hours)
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
