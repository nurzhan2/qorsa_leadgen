"""Entry point: `python -m workers.telegram_monitor.main` (run from the
repo root, with the Java core already up)."""

import asyncio
import logging
import sys

import structlog

from .config import Settings, load_channels, load_keywords
from .monitor import TelegramOrderMonitor


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
    log.info("telegram_monitor.starting", core_url=settings.core_url)

    channels = load_channels(settings.channels_file)
    keywords = load_keywords(settings.keywords_file)

    monitor = TelegramOrderMonitor(settings, channels, keywords)
    try:
        await monitor.start()
    finally:
        await monitor.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
