"""twscrape backend implementation with cookie-based auth."""

from __future__ import annotations

from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from twscrape import API
from twscrape.logger import set_log_level

from .base import InternalTweet, TimelineBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TwscrapeBackend(TimelineBackend):
    """Backend using twscrape library for Twitter data fetching."""

    def __init__(
        self,
        db_path: str,
        log_level: str = "WARNING",
        proxy: str | None = None,
    ) -> None:
        """
        Initialize twscrape backend.

        Args:
            db_path: Path to twscrape accounts database
            log_level: Logging level for twscrape
            proxy: Optional proxy URL (e.g., socks5://127.0.0.1:1080)
        """
        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._api = API(db_path, proxy=proxy)
        self._user_id_cache: dict[str, str] = {}
        set_log_level(log_level)

    async def get_user_id(self, screen_name: str) -> str:
        """
        Resolve a screen name to a numeric user ID.

        Args:
            screen_name: Twitter/X username (without @)

        Returns:
            Numeric user ID as string
        """
        key = screen_name.lower()
        if key in self._user_id_cache:
            return self._user_id_cache[key]

        user = await self._api.user_by_login(screen_name)
        if user is None:
            raise ValueError(f"User not found: {screen_name}")

        user_id = str(user.id)
        self._user_id_cache[key] = user_id
        return user_id

    async def iter_list_timeline(
        self,
        list_id: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Fetch tweets from a list timeline.

        Args:
            list_id: The numeric list ID to fetch
            limit: Maximum tweets to return

        Yields:
            InternalTweet objects
        """
        count = 0
        async with aclosing(self._api.list_timeline(int(list_id))) as gen:
            async for tweet in gen:
                yield self._convert_tweet(tweet)
                count += 1
                if limit is not None and count >= limit:
                    break

    async def iter_user_timeline(
        self,
        screen_name: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Fetch tweets from a user's timeline.

        Args:
            screen_name: Twitter/X username (without @)
            limit: Maximum tweets to return

        Yields:
            InternalTweet objects
        """
        user_id = await self.get_user_id(screen_name)
        count = 0
        async with aclosing(self._api.user_tweets(int(user_id))) as gen:
            async for tweet in gen:
                yield self._convert_tweet(tweet)
                count += 1
                if limit is not None and count >= limit:
                    break

    def _convert_tweet(self, tweet: Any) -> InternalTweet:
        """
        Map twscrape Tweet -> InternalTweet.

        twscrape Tweet is a SNScrape-like model with attrs:
          tweet.id, tweet.date, tweet.user.id, tweet.user.username,
          tweet.rawContent, tweet.inReplyToTweetId, tweet.conversationId,
          tweet.media, tweet.retweetedTweet, tweet.quotedTweet, etc.
        """
        created_at: datetime = tweet.date

        is_retweet = getattr(tweet, "retweetedTweet", None) is not None
        is_quote = getattr(tweet, "quotedTweet", None) is not None

        media = getattr(tweet, "media", None)
        has_media = bool(media)

        in_reply_to_id = getattr(tweet, "inReplyToTweetId", None)
        conversation_id = getattr(tweet, "conversationId", None)

        raw_dict = tweet.dict()

        return InternalTweet(
            id=str(tweet.id),
            created_at=created_at,
            user_id=str(tweet.user.id),
            screen_name=tweet.user.username,
            text=tweet.rawContent,
            conversation_id=str(conversation_id) if conversation_id else None,
            in_reply_to_id=str(in_reply_to_id) if in_reply_to_id else None,
            is_retweet=is_retweet,
            is_quote=is_quote,
            has_media=has_media,
            raw=raw_dict,
        )
