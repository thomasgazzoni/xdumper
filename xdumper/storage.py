"""SQLite storage for tweets with BLOB raw data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .twitter.backends.base import InternalTweet


def _datetime_to_iso(obj: Any) -> Any:
    """Convert datetime objects to ISO format for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class TweetStore:
    """SQLite-based storage for tweets with caching support."""

    def __init__(self, db_path: str) -> None:
        """
        Initialize the tweet store.

        Args:
            db_path: Path to SQLite database file
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._conn() as conn:
            conn.executescript("""
                -- Timelines table tracks scrape history per URL/key
                CREATE TABLE IF NOT EXISTS timelines (
                    key TEXT PRIMARY KEY,          -- e.g. "list:123" or "user:elonmusk"
                    url TEXT NOT NULL,
                    type TEXT NOT NULL,            -- "list" or "user"
                    first_scraped_at TEXT NOT NULL,
                    last_scraped_at TEXT NOT NULL,
                    newest_tweet_id TEXT,          -- Most recent tweet ID seen
                    oldest_tweet_id TEXT           -- Oldest tweet ID seen (for pagination)
                );

                -- Tweets table with indexed columns and raw BLOB
                CREATE TABLE IF NOT EXISTS tweets (
                    id TEXT PRIMARY KEY,
                    timeline_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    screen_name TEXT NOT NULL,
                    conversation_id TEXT,
                    in_reply_to_id TEXT,
                    is_retweet INTEGER NOT NULL DEFAULT 0,
                    is_quote INTEGER NOT NULL DEFAULT 0,
                    has_media INTEGER NOT NULL DEFAULT 0,
                    text TEXT NOT NULL,
                    raw BLOB NOT NULL,             -- JSON blob of full tweet data
                    stored_at TEXT NOT NULL,       -- When we stored this tweet
                    FOREIGN KEY (timeline_key) REFERENCES timelines(key)
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_tweets_timeline_key ON tweets(timeline_key);
                CREATE INDEX IF NOT EXISTS idx_tweets_created_at ON tweets(created_at);
                CREATE INDEX IF NOT EXISTS idx_tweets_user_id ON tweets(user_id);
                CREATE INDEX IF NOT EXISTS idx_tweets_conversation_id ON tweets(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_tweets_in_reply_to_id ON tweets(in_reply_to_id);
            """)
            conn.commit()

    def get_timeline_info(self, key: str) -> dict[str, Any] | None:
        """
        Get timeline scrape info.

        Args:
            key: Timeline key (e.g. "list:123" or "user:elonmusk")

        Returns:
            Dict with timeline info or None if not found
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM timelines WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def update_timeline_info(
        self,
        key: str,
        url: str,
        timeline_type: str,
        newest_tweet_id: str | None = None,
        oldest_tweet_id: str | None = None,
    ) -> None:
        """
        Update or insert timeline scrape info.

        Args:
            key: Timeline key
            url: Original URL
            timeline_type: "list" or "user"
            newest_tweet_id: Most recent tweet ID seen
            oldest_tweet_id: Oldest tweet ID seen
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT newest_tweet_id, oldest_tweet_id FROM timelines WHERE key = ?",
                (key,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO timelines (key, url, type, first_scraped_at, last_scraped_at, newest_tweet_id, oldest_tweet_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, url, timeline_type, now, now, newest_tweet_id, oldest_tweet_id),
                )
            else:
                # Update newest if provided and actually newer
                new_newest = newest_tweet_id
                if existing["newest_tweet_id"] and newest_tweet_id:
                    if int(newest_tweet_id) > int(existing["newest_tweet_id"]):
                        new_newest = newest_tweet_id
                    else:
                        new_newest = existing["newest_tweet_id"]
                elif existing["newest_tweet_id"]:
                    new_newest = existing["newest_tweet_id"]

                # Update oldest if provided and actually older
                new_oldest = oldest_tweet_id
                if existing["oldest_tweet_id"] and oldest_tweet_id:
                    if int(oldest_tweet_id) < int(existing["oldest_tweet_id"]):
                        new_oldest = oldest_tweet_id
                    else:
                        new_oldest = existing["oldest_tweet_id"]
                elif existing["oldest_tweet_id"]:
                    new_oldest = existing["oldest_tweet_id"]

                conn.execute(
                    """
                    UPDATE timelines
                    SET last_scraped_at = ?, newest_tweet_id = ?, oldest_tweet_id = ?
                    WHERE key = ?
                    """,
                    (now, new_newest, new_oldest, key),
                )
            conn.commit()

    def has_tweet(self, tweet_id: str) -> bool:
        """Check if a tweet exists in the store."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM tweets WHERE id = ?",
                (tweet_id,),
            ).fetchone()
            return row is not None

    def store_tweet(self, tweet: InternalTweet, timeline_key: str) -> bool:
        """
        Store a tweet in the database.

        Args:
            tweet: InternalTweet to store
            timeline_key: Timeline key this tweet belongs to

        Returns:
            True if tweet was inserted, False if it already existed
        """
        if self.has_tweet(tweet.id):
            return False

        now = datetime.now(timezone.utc).isoformat()
        raw_blob = json.dumps(tweet.raw, default=_datetime_to_iso).encode("utf-8")

        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tweets
                (id, timeline_key, created_at, user_id, screen_name, conversation_id,
                 in_reply_to_id, is_retweet, is_quote, has_media, text, raw, stored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tweet.id,
                    timeline_key,
                    tweet.created_at.isoformat(),
                    tweet.user_id,
                    tweet.screen_name,
                    tweet.conversation_id,
                    tweet.in_reply_to_id,
                    1 if tweet.is_retweet else 0,
                    1 if tweet.is_quote else 0,
                    1 if tweet.has_media else 0,
                    tweet.text,
                    raw_blob,
                    now,
                ),
            )
            conn.commit()
            return conn.total_changes > 0

    def get_tweets_for_timeline(
        self,
        timeline_key: str,
        limit: int | None = None,
        order: str = "DESC",
    ) -> list[dict[str, Any]]:
        """
        Get stored tweets for a timeline.

        Args:
            timeline_key: Timeline key to query
            limit: Maximum number of tweets to return
            order: "DESC" for newest first, "ASC" for oldest first

        Returns:
            List of tweet dicts with parsed raw data
        """
        with self._conn() as conn:
            query = f"""
                SELECT id, timeline_key, created_at, user_id, screen_name,
                       conversation_id, in_reply_to_id, is_retweet, is_quote,
                       has_media, text, raw, stored_at
                FROM tweets
                WHERE timeline_key = ?
                ORDER BY created_at {order}
            """
            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, (timeline_key,)).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                # Parse raw BLOB back to dict
                d["raw"] = json.loads(d["raw"].decode("utf-8"))
                # Convert boolean integers
                d["is_retweet"] = bool(d["is_retweet"])
                d["is_quote"] = bool(d["is_quote"])
                d["has_media"] = bool(d["has_media"])
                result.append(d)
            return result

    def get_newest_tweet_id(self, timeline_key: str) -> str | None:
        """Get the newest tweet ID for a timeline."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM tweets WHERE timeline_key = ? ORDER BY created_at DESC LIMIT 1",
                (timeline_key,),
            ).fetchone()
            return row["id"] if row else None

    def get_oldest_tweet_id(self, timeline_key: str) -> str | None:
        """Get the oldest tweet ID for a timeline."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM tweets WHERE timeline_key = ? ORDER BY created_at ASC LIMIT 1",
                (timeline_key,),
            ).fetchone()
            return row["id"] if row else None

    def get_tweet_count(self, timeline_key: str) -> int:
        """Get the number of tweets stored for a timeline."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tweets WHERE timeline_key = ?",
                (timeline_key,),
            ).fetchone()
            return row["cnt"]

    def get_thread(self, conversation_id: str) -> list[dict[str, Any]]:
        """
        Get all tweets in a conversation thread.

        Args:
            conversation_id: The conversation/thread ID

        Returns:
            List of tweets in the thread, ordered by creation time
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, timeline_key, created_at, user_id, screen_name,
                       conversation_id, in_reply_to_id, is_retweet, is_quote,
                       has_media, text, raw, stored_at
                FROM tweets
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["raw"] = json.loads(d["raw"].decode("utf-8"))
                d["is_retweet"] = bool(d["is_retweet"])
                d["is_quote"] = bool(d["is_quote"])
                d["has_media"] = bool(d["has_media"])
                result.append(d)
            return result
