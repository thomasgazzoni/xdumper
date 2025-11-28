"""Backend factory and exports."""

from __future__ import annotations

from xdumper.config import load_config

from .base import InternalTweet, TimelineBackend
from .twscrape_backend import TwscrapeBackend

__all__ = [
    "InternalTweet",
    "TimelineBackend",
    "TwscrapeBackend",
    "create_backend",
]


def create_backend() -> TimelineBackend:
    """
    Create a backend based on configuration.

    Returns:
        Configured TimelineBackend instance
    """
    cfg = load_config()
    return TwscrapeBackend(
        db_path=cfg.db_path,
        log_level=cfg.log_level,
        proxy=cfg.proxy,
    )
