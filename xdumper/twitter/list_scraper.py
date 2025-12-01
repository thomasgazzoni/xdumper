"""Timeline scraping orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .backends import create_backend
from .url_parser import TimelineTarget, TimelineType, parse_timeline_url

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .backends.base import InternalTweet


async def scrape_url(
    url: str,
    limit: int | None = None,
) -> AsyncIterator[InternalTweet]:
    """
    Scrape tweets from a Twitter/X timeline URL.

    Supports both list and user profile URLs.

    Args:
        url: X/Twitter URL (list or user profile)
        limit: Maximum number of tweets to scrape (None = no limit)

    Yields:
        InternalTweet objects

    Raises:
        ValueError: If URL is not a supported timeline URL
    """
    target = parse_timeline_url(url)
    async for tweet in scrape_target(target, limit=limit):
        yield tweet


async def scrape_target(
    target: TimelineTarget,
    limit: int | None = None,
) -> AsyncIterator[InternalTweet]:
    """
    Scrape tweets from a parsed timeline target.

    Args:
        target: Parsed TimelineTarget
        limit: Maximum number of tweets to scrape (None = no limit)

    Yields:
        InternalTweet objects
    """
    backend = create_backend()

    if target.type is TimelineType.LIST:
        if not target.list_id:
            raise ValueError("List target missing list_id")
        async for tweet in backend.iter_list_timeline(target.list_id, limit=limit):
            yield tweet
    elif target.type is TimelineType.USER:
        if not target.screen_name:
            raise ValueError("User target missing screen_name")
        async for tweet in backend.iter_user_timeline(target.screen_name, limit=limit):
            yield tweet
    elif target.type is TimelineType.THREAD:
        if not target.tweet_id:
            raise ValueError("Thread target missing tweet_id")
        async for tweet in backend.iter_thread(target.tweet_id, limit=limit):
            yield tweet
    else:
        raise ValueError(f"Unsupported timeline type: {target.type}")
