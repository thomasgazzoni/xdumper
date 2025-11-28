"""Configuration for xdumper."""

import os
from pathlib import Path


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:
        # Database path for twscrape accounts
        default_db = str(Path.home() / ".xdumper" / "accounts.db")
        self.db_path: str = os.getenv("XDUMPER_DB", default_db)

        # Database path for tweet storage
        default_store = str(Path.home() / ".xdumper" / "tweets.db")
        self.store_path: str = os.getenv("XDUMPER_STORE", default_store)

        # Log level
        self.log_level: str = os.getenv("XDUMPER_LOG_LEVEL", "WARNING")

        # Proxy URL (e.g., socks5://127.0.0.1:1080 or http://127.0.0.1:8080)
        self.proxy: str | None = os.getenv("XDUMPER_PROXY")


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config()
