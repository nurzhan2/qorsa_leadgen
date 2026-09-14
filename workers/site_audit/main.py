"""Entry point: `python -m workers.site_audit.main` (run from the repo root,
with the Java core already up on CORE_URL).
"""

import asyncio
import logging
import sys

import httpx
import structlog

from .config import Settings
from .core_client import CoreClient
from .psi_client import PageSpeedClient
from .runner import AuditRunner


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

    psi_enabled = bool(settings.psi_api_key)
    log.info(
        "audit.starting",
        core_url=settings.core_url,
        batch=settings.audit_batch,
        max_batches=settings.max_batches_per_run,
        concurrency=settings.audit_concurrency,
        psi_enabled=psi_enabled,
        psi_strategy=settings.psi_strategy if psi_enabled else None,
        psi_max_per_run=settings.psi_max_per_run if psi_enabled else None,
    )
    if not psi_enabled:
        log.warning("audit.psi_disabled",
                    hint="PSI_API_KEY is empty: only the local probe runs, so auditFails is "
                         "reported and pagespeed is not. PSI is free and needs no billing - "
                         "see README.md 'PageSpeed Insights'.")

    limits = httpx.Limits(max_connections=settings.audit_concurrency * 2,
                          max_keepalive_connections=settings.audit_concurrency)
    core = CoreClient(settings.core_url)
    psi = PageSpeedClient(settings.psi_api_key, settings.psi_strategy, settings.psi_timeout_seconds)
    headers = {"User-Agent": settings.user_agent}
    try:
        async with httpx.AsyncClient(limits=limits, headers=headers,
                                     timeout=settings.request_timeout_seconds,
                                     follow_redirects=True) as http_client:
            while True:
                runner = AuditRunner(settings, core, psi, http_client)
                summary = await runner.run_once()
                log.info("audit.run_finished", **summary)
                if settings.run_once:
                    break
                log.info("audit.sleeping_until_next_run", minutes=settings.loop_interval_minutes)
                await asyncio.sleep(settings.loop_interval_minutes * 60)
    finally:
        await psi.aclose()
        await core.aclose()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
