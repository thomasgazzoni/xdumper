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
    _THREAD_ENDPOINTS = ("TweetDetail",)
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

    async def iter_thread(
        self,
        tweet_id: str,
        limit: int | None = None,
    ) -> AsyncIterator[InternalTweet]:
        """
        Yield tweets from a conversation thread, oldest first.

        Navigates to the tweet URL and intercepts the TweetDetail response
        to get the full conversation.

        Args:
            tweet_id: The tweet ID to fetch the thread for
            limit: Maximum tweets to yield

        Yields:
            InternalTweet objects in chronological order
        """
        context = await self._ensure_browser()
        page = await context.new_page()

        tweets: list[InternalTweet] = []

        async def handle_response(response: Response) -> None:
            """Intercept TweetDetail response."""
            if "/i/api/graphql/" not in response.url:
                return

            match = re.search(r"/graphql/[^/]+/([^?]+)", response.url)
            if not match:
                return

            endpoint = match.group(1)
            if endpoint not in self._THREAD_ENDPOINTS:
                return

            _debug(f"Matched thread endpoint: {endpoint}")

            try:
                if not response.ok:
                    return

                data = await response.json()
                thread_tweets = self._extract_thread_from_response(data)
                _debug(f"Extracted {len(thread_tweets)} tweets from thread")
                tweets.extend(thread_tweets)
            except Exception as e:
                _debug(f"Error processing thread response: {e}")

        page.on("response", handle_response)

        try:
            # Navigate to tweet URL - any user works, we just need the tweet_id
            url = f"https://x.com/i/status/{tweet_id}"
            _debug(f"Navigating to {url}")

            async with page.expect_response(
                lambda r: "/i/api/graphql/" in r.url and "TweetDetail" in r.url,
                timeout=30000,
            ):
                await page.goto(url, wait_until="domcontentloaded")

            # Wait for response processing
            await asyncio.sleep(_random_delay(2.0, 3.0))

            if not tweets:
                return

            # Get the original author (first tweet's author)
            # All tweets should share the same conversation_id as the requested tweet
            original_author = tweets[0].user_id

            # Filter to only include tweets from the original author (the actual thread)
            thread_tweets = [t for t in tweets if t.user_id == original_author]

            # Sort by created_at (oldest first for threads)
            thread_tweets.sort(key=lambda t: t.created_at)

            _debug(f"Filtered to {len(thread_tweets)} tweets from original author")

            # Yield tweets
            yielded = 0
            for tweet in thread_tweets:
                yield tweet
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

        finally:
            await page.close()

    def _extract_thread_from_response(
        self,
        data: dict[str, Any],
    ) -> list[InternalTweet]:
        """
        Extract tweets from TweetDetail response.

        The TweetDetail response contains the conversation in
        'threaded_conversation_with_injections_v2' with instructions and entries.
        """
        tweets: list[InternalTweet] = []

        try:
            # Get instructions from threaded_conversation_with_injections_v2
            instructions = (
                data.get("data", {})
                .get("threaded_conversation_with_injections_v2", {})
                .get("instructions", [])
            )

            _debug(f"Found {len(instructions)} instructions in thread response")

            for instruction in instructions:
                entries = instruction.get("entries", [])

                for entry in entries:
                    entry_id = entry.get("entryId", "")

                    # Skip cursor entries
                    if entry_id.startswith("cursor-"):
                        continue

                    content = entry.get("content", {})
                    content_type = content.get("entryType") or content.get("__typename", "")

                    # Handle TimelineTimelineItem (single tweet)
                    if content_type == "TimelineTimelineItem":
                        item_content = content.get("itemContent", {})
                        tweet = self._extract_tweet_from_item_content(item_content)
                        if tweet and not any(t.id == tweet.id for t in tweets):
                            tweets.append(tweet)

                    # Handle TimelineTimelineModule (conversation group)
                    elif content_type == "TimelineTimelineModule":
                        items = content.get("items", [])
                        for item in items:
                            item_content = item.get("item", {}).get("itemContent", {})
                            if not item_content:
                                item_content = item.get("itemContent", {})

                            tweet = self._extract_tweet_from_item_content(item_content)
                            if tweet and not any(t.id == tweet.id for t in tweets):
                                tweets.append(tweet)

        except Exception as e:
            _debug(f"Exception in _extract_thread_from_response: {e}")

        return tweets

    def _extract_tweet_from_item_content(
        self,
        item_content: dict[str, Any],
    ) -> InternalTweet | None:
        """Extract a tweet from itemContent dict."""
        try:
            item_type = item_content.get("itemType") or item_content.get("__typename", "")

            if item_type != "TimelineTweet":
                return None

            tweet_results = item_content.get("tweet_results", {})
            result = tweet_results.get("result", {})

            typename = result.get("__typename", "")
            if typename == "TweetWithVisibilityResults":
                result = result.get("tweet", {})
                typename = result.get("__typename", "")

            if typename == "TweetTombstone":
                return None

            if result and typename == "Tweet":
                return self._convert_graphql_tweet(result)

        except Exception as e:
            _debug(f"Error extracting tweet from item content: {e}")

        return None

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
        tweet_queue: asyncio.Queue[InternalTweet | None] = asyncio.Queue()
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

                if not tweets:
                    # Signal end of timeline - API returned 0 tweets
                    _debug("API returned 0 tweets, signaling end of timeline")
                    await tweet_queue.put(None)
                else:
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
            max_consecutive_empty = 5  # Allow more retries for slow-loading content
            end_of_timeline = False

            while True:
                # Drain queue and yield tweets
                processed_any = False

                while not tweet_queue.empty():
                    item = tweet_queue.get_nowait()

                    # Check for end-of-timeline signal
                    if item is None:
                        _debug("Received end-of-timeline signal from API")
                        end_of_timeline = True
                        continue

                    processed_any = True
                    yielded_count += 1
                    yield item

                    if limit is not None and yielded_count >= limit:
                        _debug(f"Reached limit of {limit}")
                        return

                # Stop if API indicated end of timeline
                if end_of_timeline:
                    _debug("End of timeline reached (API returned 0 tweets)")
                    return

                if processed_any:
                    consecutive_empty = 0
                    _debug(f"Yielded tweets, total: {yielded_count}")
                else:
                    consecutive_empty += 1
                    _debug(f"No tweets in queue, consecutive_empty: {consecutive_empty}")

                # Stop if no new content after multiple scrolls (fallback)
                if consecutive_empty >= max_consecutive_empty:
                    _debug("Max consecutive empty reached, stopping")
                    return

                # Scroll to bottom of timeline to trigger infinite scroll
                # Twitter loads more content when you reach near the bottom
                _debug("Scrolling to bottom of timeline...")
                scroll_script = """
                    () => {
                        // Find the timeline container and scroll within it, or use window
                        const timeline = document.querySelector('[data-testid="primaryColumn"]');
                        if (timeline) {
                            // Scroll the main timeline to bottom
                            window.scrollTo(0, document.body.scrollHeight);
                        } else {
                            window.scrollTo(0, document.body.scrollHeight);
                        }
                        return document.body.scrollHeight;
                    }
                """
                scroll_height = await page.evaluate(scroll_script)
                _debug(f"Scrolled to height: {scroll_height}")

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
                    entry_id = entry.get("entryId", "")
                    _debug(f"  Entry: {entry_id[:50]}")
                    extracted = self._extract_tweets_from_entry(entry)
                    tweets.extend(extracted)

        except Exception as e:
            _debug(f"Exception in _extract_tweets_from_response: {e}")

        return tweets

    def _extract_tweets_from_entry(self, entry: dict[str, Any]) -> list[InternalTweet]:
        """
        Extract tweets from timeline entry.

        For TimelineTimelineModule entries (thread conversations), extracts ALL tweets
        and marks them as thread starter/continuation appropriately.

        Args:
            entry: Timeline entry dict

        Returns:
            List of InternalTweet objects (may be empty)
        """
        try:
            entry_id = entry.get("entryId", "")

            # Skip cursor entries
            if entry_id.startswith("cursor-"):
                return []

            content = entry.get("content", {})
            content_type = content.get("entryType") or content.get("__typename", "")

            # Handle TimelineTimelineModule (for profile with conversation threads)
            if content_type == "TimelineTimelineModule":
                return self._extract_tweets_from_module(content)

            # Handle single tweet entry (TimelineTimelineItem)
            item_content = content.get("itemContent", {})
            item_type = item_content.get("itemType") or item_content.get("__typename", "")

            _debug(f"    content_type={content_type}, item_type={item_type}")

            if item_type != "TimelineTweet":
                return []

            tweet_results = item_content.get("tweet_results", {})
            result = tweet_results.get("result", {})

            # Skip tombstone (deleted) tweets
            typename = result.get("__typename")
            if typename == "TweetTombstone":
                _debug(f"    Skipping tombstone tweet")
                return []

            # Handle TweetWithVisibilityResults wrapper
            if typename == "TweetWithVisibilityResults":
                result = result.get("tweet", {})

            tweet = self._convert_graphql_tweet(result)
            return [tweet] if tweet else []

        except Exception as e:
            _debug(f"    Exception extracting tweet: {e}")
            return []

    def _extract_tweets_from_module(
        self, content: dict[str, Any]
    ) -> list[InternalTweet]:
        """
        Extract all tweets from a TimelineTimelineModule (conversation thread).

        Marks the first tweet as thread_starter if there are multiple tweets
        from the same user in the conversation.

        Args:
            content: The module content dict

        Returns:
            List of tweets with thread flags set appropriately
        """
        tweets: list[InternalTweet] = []
        items = content.get("items", [])

        if not items:
            return []

        # Extract all tweets from the module
        for item in items:
            item_content = item.get("item", {}).get("itemContent", {})
            if not item_content:
                item_content = item.get("itemContent", {})

            tweet = self._extract_tweet_from_item_content(item_content)
            if tweet:
                tweets.append(tweet)

        # If we have multiple tweets from the same user, mark as thread
        if len(tweets) > 1:
            # Get the first tweet's user
            first_user_id = tweets[0].user_id
            first_conv_id = tweets[0].conversation_id

            # Check if this is a self-thread (multiple tweets from same user)
            same_user_tweets = [t for t in tweets if t.user_id == first_user_id]

            if len(same_user_tweets) > 1:
                # Mark the first tweet (thread starter)
                for tweet in tweets:
                    if tweet.id == first_conv_id and tweet.user_id == first_user_id:
                        tweet.is_thread_starter = True
                        _debug(f"    Marked {tweet.id} as thread_starter")

        return tweets

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

        # Get user ID
        user_id = user_results.get("rest_id", legacy.get("user_id_str", ""))

        # Detect self-thread: tweet is a reply to the user's own tweet
        in_reply_to_user_id = legacy.get("in_reply_to_user_id_str")
        is_self_thread = bool(in_reply_to_user_id and in_reply_to_user_id == user_id)

        # Get full text - use note_tweet for long tweets ("show more"), fall back to legacy
        note_tweet = tweet_data.get("note_tweet", {})
        note_tweet_text = (
            note_tweet.get("note_tweet_results", {})
            .get("result", {})
            .get("text")
        )
        text = note_tweet_text or legacy.get("full_text", "")

        return InternalTweet(
            id=legacy.get("id_str", tweet_data.get("rest_id", "")),
            created_at=created_at,
            user_id=user_id,
            screen_name=screen_name,
            text=text,
            conversation_id=legacy.get("conversation_id_str"),
            in_reply_to_id=legacy.get("in_reply_to_status_id_str"),
            is_retweet=is_retweet,
            is_quote=is_quote,
            has_media=has_media,
            is_self_thread=is_self_thread,
            raw=tweet_data,
        )
