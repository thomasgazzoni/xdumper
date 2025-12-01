"""Patchright browser backend for Twitter/X scraping with bot detection evasion."""

from __future__ import annotations

import asyncio
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from patchright.async_api import async_playwright

from .base import InternalTweet, TimelineBackend

_DEBUG_ENABLED = os.getenv("XDUMPER_LOG_LEVEL", "WARNING").upper() == "DEBUG"


def _debug(msg: str) -> None:
    """Print debug message to stderr if debug logging is enabled."""
    if _DEBUG_ENABLED:
        print(f"[patchright] {msg}", file=sys.stderr)


def _random_delay(min_sec: float = 1.5, max_sec: float = 4.0) -> float:
    """Generate random delay for human-like behavior."""
    return random.uniform(min_sec, max_sec)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from patchright.async_api import BrowserContext, Page, Playwright, Response


class PatchrightBackend(TimelineBackend):
    """
    Backend using Patchright (stealth Playwright) for browser-based scraping.

    Uses persistent Chrome profile to maintain logged-in session state.
    Intercepts GraphQL API responses to extract tweet data.
    """

    # GraphQL endpoints we care about
    _LIST_ENDPOINTS = ("ListLatestTweetsTimeline", "ListTimeline")
    _USER_ENDPOINTS = ("UserTweets", "UserTweetsAndReplies")
    _USER_BY_SCREEN_NAME = "UserByScreenName"

    def __init__(
        self,
        chrome_profile: str,
        headless: bool = False,
        proxy: str | None = None,
    ) -> None:
        """
        Initialize Patchright backend.

        Args:
            chrome_profile: Path to Chrome user data directory for persistent sessions
            headless: Run browser in headless mode (default False for stealth)
            proxy: Optional proxy URL
        """
        self._chrome_profile = chrome_profile
        self._headless = headless
        self._proxy = proxy

        # Ensure profile directory exists
        Path(chrome_profile).mkdir(parents=True, exist_ok=True)

        # Lazy-initialized browser state
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

        # User ID cache (same pattern as TwscrapeBackend)
        self._user_id_cache: dict[str, str] = {}

    async def _ensure_browser(self) -> BrowserContext:
        """
        Lazily initialize and return browser context.

        Browser stays alive for the lifetime of this backend instance.
        """
        if self._context is not None:
            return self._context

        self._playwright = await async_playwright().start()

        # Build launch options
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": self._chrome_profile,
            "channel": "chrome",
            "headless": self._headless,
            "no_viewport": True,
        }
        if self._proxy:
            launch_kwargs["proxy"] = {"server": self._proxy}

        self._context = await self._playwright.chromium.launch_persistent_context(
            **launch_kwargs
        )
        return self._context

    async def _close_browser(self) -> None:
        """Close browser and cleanup resources."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def iter_list_timeline(
        self,
        list_id: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Yield tweets from a Twitter list.

        Args:
            list_id: Numeric list ID
            limit: Maximum tweets to yield

        Yields:
            InternalTweet objects
        """
        url = f"https://x.com/i/lists/{list_id}"
        async for tweet in self._scrape_timeline(url, self._LIST_ENDPOINTS, limit):
            yield tweet

    async def iter_user_timeline(
        self,
        screen_name: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Yield tweets from a user's profile.

        Args:
            screen_name: Twitter/X username (without @)
            limit: Maximum tweets to yield

        Yields:
            InternalTweet objects
        """
        url = f"https://x.com/{screen_name}"
        async for tweet in self._scrape_timeline(url, self._USER_ENDPOINTS, limit):
            yield tweet

    async def get_user_id(self, screen_name: str) -> str:
        """
        Resolve screen_name to numeric user ID.

        Args:
            screen_name: Twitter/X username (without @)

        Returns:
            Numeric user ID as string
        """
        key = screen_name.lower()
        if key in self._user_id_cache:
            return self._user_id_cache[key]

        context = await self._ensure_browser()
        page = await context.new_page()

        user_id: str | None = None

        async def handle_response(response: Response) -> None:
            nonlocal user_id
            if self._USER_BY_SCREEN_NAME not in response.url:
                return
            try:
                data = await response.json()
                user_id = data["data"]["user"]["result"]["rest_id"]
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            await page.goto(f"https://x.com/{screen_name}", wait_until="domcontentloaded")
            # Wait for API response
            await asyncio.sleep(3)
        finally:
            await page.close()

        if user_id is None:
            raise ValueError(f"Could not resolve user ID for: {screen_name}")

        self._user_id_cache[key] = user_id
        return user_id

    async def _scrape_timeline(
        self,
        url: str,
        expected_endpoints: tuple[str, ...],
        limit: int | None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Core scraping logic: navigate, intercept GraphQL, yield tweets.

        Args:
            url: Timeline URL to navigate to
            expected_endpoints: GraphQL endpoint names to capture
            limit: Maximum tweets to yield

        Yields:
            InternalTweet objects
        """
        context = await self._ensure_browser()
        page = await context.new_page()

        # Queue for tweets from response handler
        tweet_queue: asyncio.Queue[InternalTweet] = asyncio.Queue()
        seen_ids: set[str] = set()

        async def handle_response(response: Response) -> None:
            """Intercept GraphQL responses and extract tweets."""
            # Only process GraphQL API responses
            if "/i/api/graphql/" not in response.url:
                return

            # Extract endpoint name from URL
            match = re.search(r"/graphql/[^/]+/([^?]+)", response.url)
            if not match:
                return

            endpoint = match.group(1)
            _debug(f"GraphQL endpoint: {endpoint}")

            if endpoint not in expected_endpoints:
                return

            _debug(f"Matched endpoint: {endpoint}, status: {response.status}")

            try:
                if not response.ok:
                    _debug(f"Response not OK: {response.status}")
                    return

                data = await response.json()
                _debug(f"Got JSON response, extracting tweets...")
                tweets = self._extract_tweets_from_response(data, endpoint)
                _debug(f"Extracted {len(tweets)} tweets from response")

                for tweet in tweets:
                    if tweet.id not in seen_ids:
                        seen_ids.add(tweet.id)
                        await tweet_queue.put(tweet)
            except Exception as e:
                _debug(f"Error processing response: {e}")

        page.on("response", handle_response)

        # Build pattern to match expected endpoints
        endpoint_pattern = "|".join(expected_endpoints)

        try:
            # Navigate to timeline and wait for first API response
            _debug(f"Navigating to {url}")
            _debug(f"Waiting for endpoints matching: {endpoint_pattern}")

            # Wait for both navigation and the first relevant GraphQL response
            async with page.expect_response(
                lambda r: "/i/api/graphql/" in r.url and any(ep in r.url for ep in expected_endpoints),
                timeout=30000,
            ) as response_info:
                await page.goto(url, wait_until="domcontentloaded")

            response = await response_info.value
            _debug(f"Got initial API response: {response.url[:100]}...")

            # Wait a bit for any remaining API calls to complete
            await asyncio.sleep(_random_delay(2.0, 4.0))

            # Check for login requirement - look for signs we're NOT logged in
            if await self._check_login_required(page):
                raise RuntimeError(
                    f"Twitter login required. Please log in manually using the browser "
                    f"profile at: {self._chrome_profile}"
                )

            _debug(f"Login check passed, queue size: {tweet_queue.qsize()}")

            yielded_count = 0
            consecutive_empty = 0
            max_consecutive_empty = 5

            while True:
                # Drain queue and yield tweets
                processed_any = False

                while not tweet_queue.empty():
                    tweet = tweet_queue.get_nowait()
                    processed_any = True
                    yielded_count += 1
                    yield tweet

                    if limit is not None and yielded_count >= limit:
                        _debug(f"Reached limit of {limit}")
                        return

                if processed_any:
                    consecutive_empty = 0
                    _debug(f"Yielded tweets, total: {yielded_count}")
                else:
                    consecutive_empty += 1
                    _debug(f"No tweets in queue, consecutive_empty: {consecutive_empty}")

                # Stop if no new content after multiple scrolls
                if consecutive_empty >= max_consecutive_empty:
                    _debug("Max consecutive empty reached, stopping")
                    return

                # Human-like scroll with random amount
                scroll_amount = random.uniform(0.6, 0.9)
                _debug(f"Scrolling ({scroll_amount:.0%} of viewport)...")
                await page.evaluate(f"window.scrollBy(0, window.innerHeight * {scroll_amount})")

                # Human-like delay to wait for content to load
                delay = _random_delay(2.5, 4.5)
                _debug(f"Waiting {delay:.1f}s for responses...")
                await asyncio.sleep(delay)

        finally:
            await page.close()

    async def _check_login_required(self, page: Page) -> bool:
        """Check if Twitter requires login by looking for logged-in indicators."""
        try:
            # First, check for positive signs we ARE logged in
            # Look for: compose tweet button, account switcher, or nav with home/notifications
            logged_in_selectors = [
                '[data-testid="SideNav_NewTweet_Button"]',  # Compose button
                '[data-testid="AppTabBar_Profile_Link"]',  # Profile link in nav
                '[aria-label="Account menu"]',  # Account menu
            ]

            for selector in logged_in_selectors:
                element = await page.query_selector(selector)
                if element:
                    _debug(f"Found logged-in indicator: {selector}")
                    return False  # We ARE logged in

            # Check for the login wall/modal that blocks content
            login_wall = await page.query_selector(
                '[data-testid="sheetDialog"], '  # Login modal
                '[data-testid="loginButton"]'  # Prominent login button
            )

            if login_wall:
                _debug("Found login wall/button")
                return True

            # If we can't determine, assume logged in and let it fail naturally
            _debug("Could not determine login status, proceeding...")
            return False

        except Exception as e:
            _debug(f"Login check error: {e}")
            return False

    def _extract_tweets_from_response(
        self,
        data: dict[str, Any],
        endpoint: str,
    ) -> list[InternalTweet]:
        """
        Extract tweets from GraphQL response JSON.

        Args:
            data: Parsed GraphQL response
            endpoint: Endpoint name (for structure detection)

        Returns:
            List of InternalTweet objects
        """
        tweets: list[InternalTweet] = []

        try:
            # Debug: show top-level keys
            _debug(f"Response top-level keys: {list(data.keys())}")
            if "data" in data:
                _debug(f"data keys: {list(data['data'].keys())}")

            # Try list timeline structure
            instructions = (
                data.get("data", {})
                .get("list", {})
                .get("tweets_timeline", {})
                .get("timeline", {})
                .get("instructions", [])
            )

            # Try user timeline structure if list didn't work
            if not instructions:
                user_data = data.get("data", {}).get("user", {})
                _debug(f"user keys: {list(user_data.keys()) if user_data else 'none'}")
                result = user_data.get("result", {})
                _debug(f"result keys: {list(result.keys()) if result else 'none'}")

                # Try timeline_v2 first (older API)
                timeline_v2 = result.get("timeline_v2", {})
                if timeline_v2:
                    instructions = timeline_v2.get("timeline", {}).get("instructions", [])

                # Try direct timeline (newer API) - can be nested as timeline.timeline
                if not instructions:
                    timeline = result.get("timeline", {})
                    _debug(f"timeline keys: {list(timeline.keys()) if timeline else 'none'}")
                    # Check for nested timeline.timeline structure
                    if "timeline" in timeline:
                        timeline = timeline.get("timeline", {})
                        _debug(f"nested timeline keys: {list(timeline.keys()) if timeline else 'none'}")
                    instructions = timeline.get("instructions", [])

            _debug(f"Found {len(instructions)} instructions")

            for instruction in instructions:
                inst_type = instruction.get("type", "unknown")
                _debug(f"Instruction type: {inst_type}")

                # Handle both "TimelineAddEntries" type and direct entries
                entries = instruction.get("entries", [])
                _debug(f"Found {len(entries)} entries in instruction")

                for entry in entries:
                    tweet = self._extract_tweet_from_entry(entry)
                    if tweet:
                        tweets.append(tweet)

        except Exception as e:
            _debug(f"Exception in _extract_tweets_from_response: {e}")

        return tweets

    def _extract_tweet_from_entry(self, entry: dict[str, Any]) -> InternalTweet | None:
        """
        Extract single tweet from timeline entry.

        Args:
            entry: Timeline entry dict

        Returns:
            InternalTweet or None if not a valid tweet
        """
        try:
            entry_id = entry.get("entryId", "")

            # Skip cursor entries
            if entry_id.startswith("cursor-"):
                return None

            # Navigate to tweet result
            item_content = entry.get("content", {}).get("itemContent", {})

            if item_content.get("itemType") != "TimelineTweet":
                return None

            tweet_results = item_content.get("tweet_results", {})
            result = tweet_results.get("result", {})

            # Skip tombstone (deleted) tweets
            typename = result.get("__typename")
            if typename == "TweetTombstone":
                return None

            # Handle TweetWithVisibilityResults wrapper
            if typename == "TweetWithVisibilityResults":
                result = result.get("tweet", {})

            return self._convert_graphql_tweet(result)

        except Exception:
            return None

    def _convert_graphql_tweet(self, tweet_data: dict[str, Any]) -> InternalTweet:
        """
        Convert GraphQL tweet object to InternalTweet.

        Args:
            tweet_data: Raw GraphQL tweet result

        Returns:
            InternalTweet
        """
        legacy = tweet_data.get("legacy", {})
        core = tweet_data.get("core", {})
        user_results = core.get("user_results", {}).get("result", {})
        user_core = user_results.get("core", {})
        user_legacy = user_results.get("legacy", {})

        # Parse created_at timestamp
        # Format: "Fri Nov 22 20:08:47 +0000 2024"
        created_at_str = legacy.get("created_at", "")
        try:
            created_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            created_at = datetime.now(timezone.utc)

        # Determine tweet properties
        is_retweet = "retweeted_status_result" in legacy
        is_quote = legacy.get("is_quote_status", False)

        media = legacy.get("extended_entities", {}).get("media", [])
        has_media = len(media) > 0

        # Get screen_name from user_core (preferred) or user_legacy (fallback)
        screen_name = user_core.get("screen_name") or user_legacy.get("screen_name", "")

        return InternalTweet(
            id=legacy.get("id_str", tweet_data.get("rest_id", "")),
            created_at=created_at,
            user_id=user_results.get("rest_id", legacy.get("user_id_str", "")),
            screen_name=screen_name,
            text=legacy.get("full_text", ""),
            conversation_id=legacy.get("conversation_id_str"),
            in_reply_to_id=legacy.get("in_reply_to_status_id_str"),
            is_retweet=is_retweet,
            is_quote=is_quote,
            has_media=has_media,
            raw=tweet_data,
        )
