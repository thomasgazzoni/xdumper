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

    Environment variable XDUMPER_BACKEND controls which backend:
    - "twscrape" (default): Uses twscrape library with cookie auth
    - "patchright": Uses Patchright browser automation

    Returns:
        Configured TimelineBackend instance
    """
    cfg = load_config()

    if cfg.backend == "patchright":
        from .patchright_backend import PatchrightBackend

        return PatchrightBackend(
            chrome_profile=cfg.chrome_profile,
            headless=cfg.headless,
            proxy=cfg.proxy,
        )

    return TwscrapeBackend(
        db_path=cfg.db_path,
        log_level=cfg.log_level,
        proxy=cfg.proxy,
    )
