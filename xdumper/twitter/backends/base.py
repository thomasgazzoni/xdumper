"""Base interfaces and models for Twitter backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class InternalTweet:
    """Normalized tweet representation across all backends."""

    id: str
    created_at: datetime
    user_id: str
    screen_name: str
    text: str
    conversation_id: str | None
    in_reply_to_id: str | None
    is_retweet: bool
    is_quote: bool
    has_media: bool
    raw: dict[str, Any] = field(default_factory=dict)


class TimelineBackend(ABC):
    """
    Abstract backend that can fetch tweets from timelines.

    Implementations must provide methods for fetching list and user timelines.
    """

    @abstractmethod
    def iter_list_timeline(
        self,
        list_id: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Yield tweets from a list timeline, newest first.

        Args:
            list_id: Numeric list id, e.g. '1409181262510690310'
            limit: Maximum number of tweets to yield (None = no explicit limit)

        Yields:
            InternalTweet objects normalized from the backend's format
        """
        ...

    @abstractmethod
    def iter_user_timeline(
        self,
        screen_name: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Yield tweets from a user's timeline, newest first.

        Args:
            screen_name: Twitter/X username (without @)
            limit: Maximum number of tweets to yield (None = no explicit limit)

        Yields:
            InternalTweet objects normalized from the backend's format
        """
        ...

    @abstractmethod
    async def get_user_id(self, screen_name: str) -> str:
        """
        Resolve a screen name to a numeric user ID.

        Args:
            screen_name: Twitter/X username (without @)

        Returns:
            Numeric user ID as string
        """
        ...
