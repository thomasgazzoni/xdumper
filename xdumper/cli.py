"""CLI for xdumper."""

import asyncio
import json
import re
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from .config import load_config


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _parse_duration(duration: str) -> timedelta:
    """
    Parse a duration string like '7d', '24h', '30m' into a timedelta.

    Supports:
        - Xd: X days
        - Xh: X hours
        - Xm: X minutes
    """
    match = re.match(r"^(\d+)([dhm])$", duration.lower())
    if not match:
        raise ValueError(f"Invalid duration format: {duration}. Use format like '7d', '24h', '30m'")

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "d":
        return timedelta(days=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "m":
        return timedelta(minutes=value)
    else:
        raise ValueError(f"Unknown duration unit: {unit}")

app = typer.Typer(
    name="xdumper",
    help="X/Twitter list and user profile scraper",
    no_args_is_help=True,
)


@app.command("add-account")
def add_account(
    username: str = typer.Option(..., "--username", "-u", prompt=True, help="X/Twitter username"),
    cookies: str = typer.Option(
        ...,
        "--cookies",
        "-c",
        prompt="Cookies JSON (paste auth_token and ct0)",
        help='Cookies as JSON: {"auth_token": "xxx", "ct0": "yyy"}',
    ),
) -> None:
    """
    Add an X/Twitter account using browser cookies.

    Get cookies from your browser after logging into X:
    1. Open browser DevTools (F12) -> Application -> Cookies -> x.com
    2. Copy 'auth_token' and 'ct0' values
    3. Pass as JSON: {"auth_token": "xxx", "ct0": "yyy"}
    """
    from twscrape import AccountsPool

    cfg = load_config()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)

    # Validate cookies JSON
    try:
        cookies_dict = json.loads(cookies)
        if "auth_token" not in cookies_dict or "ct0" not in cookies_dict:
            typer.echo("Error: Cookies must contain 'auth_token' and 'ct0' keys", err=True)
            raise typer.Exit(code=1)
    except json.JSONDecodeError as e:
        typer.echo(f"Error: Invalid JSON - {e}", err=True)
        raise typer.Exit(code=1)

    async def do_add() -> None:
        pool = AccountsPool(cfg.db_path)
        await pool.add_account(
            username=username,
            password="cookie_auth",  # dummy - not used with cookies
            email="cookie@auth.local",  # dummy - not used with cookies
            email_password="cookie_auth",  # dummy - not used with cookies
            cookies=cookies,
        )
        typer.echo(f"Account '{username}' added successfully!")

    try:
        asyncio.run(do_add())
    except Exception as e:
        typer.echo(f"Error adding account: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def accounts() -> None:
    """List all configured accounts and their status."""
    from twscrape import AccountsPool

    cfg = load_config()

    if not Path(cfg.db_path).exists():
        typer.echo("No accounts configured. Run 'xdumper add-account' first.")
        raise typer.Exit(code=0)

    async def do_list() -> None:
        pool = AccountsPool(cfg.db_path)
        stats = await pool.stats()

        if stats["total"] == 0:
            typer.echo("No accounts configured. Run 'xdumper add-account' first.")
            return

        typer.echo(f"Total: {stats['total']} | Active: {stats['active']} | Inactive: {stats['inactive']}")
        typer.echo("")

        all_accounts = await pool.get_all()
        for acc in all_accounts:
            status = "active" if acc.active else f"inactive ({acc.error_msg or 'unknown'})"
            typer.echo(f"  @{acc.username}: {status}")

    asyncio.run(do_list())


@app.command()
def scrape(
    url: str = typer.Argument(..., help="X/Twitter timeline URL (list or user profile)"),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        help="Maximum number of tweets to scrape",
    ),
    old: Optional[str] = typer.Option(
        None,
        "--old",
        help="Fetch older tweets up to this duration (e.g., '7d', '24h')",
    ),
    expand_threads: bool = typer.Option(
        False,
        "--expand-threads",
        "-e",
        help="Auto-fetch full threads when detecting self-thread tweets",
    ),
    pretty: bool = typer.Option(
        False,
        "--pretty",
        "-p",
        help="Pretty-print JSON output",
    ),
    no_store: bool = typer.Option(
        False,
        "--no-store",
        help="Don't store tweets to database (output only)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress progress messages (only output JSON)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress for each tweet",
    ),
) -> None:
    """
    Scrape tweets from an X/Twitter timeline URL, store to database, and output as JSON.

    Tweets are cached locally to avoid re-scraping. On subsequent runs:
    - New tweets are fetched and stored
    - Scraping stops when encountering already-stored tweets (unless --old is used)

    Without --limit or --old, scraping continues until reaching cached tweets or end of timeline.

    Examples:
        xdumper scrape "https://x.com/elonmusk"
        xdumper scrape "https://x.com/elonmusk" --limit 100
        xdumper scrape "https://x.com/elonmusk" --old 7d
        xdumper scrape "https://x.com/elonmusk" --expand-threads
    """
    from .storage import TweetStore
    from .twitter.list_scraper import scrape_url
    from .twitter.url_parser import parse_timeline_url

    cfg = load_config()

    # Parse old duration if provided
    old_cutoff: datetime | None = None
    if old:
        try:
            duration = _parse_duration(old)
            old_cutoff = datetime.now(timezone.utc) - duration
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    # Parse URL to get timeline key
    try:
        target = parse_timeline_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    # Initialize store
    store = TweetStore(cfg.store_path) if not no_store else None

    # Verbose startup info
    if verbose:
        typer.echo(f"Target: {target.key}", err=True)
        typer.echo(f"Store: {cfg.store_path if not no_store else 'disabled'}", err=True)
        if old_cutoff:
            typer.echo(f"Fetching tweets until: {old_cutoff.isoformat()}", err=True)
        typer.echo("Starting scrape...", err=True)

    async def runner() -> None:
        from .twitter.backends import create_backend
        from .twitter.backends.base import InternalTweet

        new_count = 0
        skipped_count = 0
        total_count = 0
        thread_count = 0
        # Use explicit --limit if provided, otherwise no limit (scroll until end)
        max_tweets = limit
        newest_id: str | None = None
        oldest_id: str | None = None
        oldest_date: datetime | None = None

        # For thread expansion - collect threads to expand after main scrape
        seen_tweet_ids: set[str] = set()
        threads_to_expand: set[str] = set()  # conversation_ids to expand
        backend = create_backend() if expand_threads else None

        def output_tweet(tweet: InternalTweet) -> None:
            """Output a single tweet as JSON."""
            obj = {
                "id": tweet.id,
                "created_at": tweet.created_at.isoformat(),
                "user_id": tweet.user_id,
                "screen_name": tweet.screen_name,
                "text": tweet.text,
                "conversation_id": tweet.conversation_id,
                "in_reply_to_id": tweet.in_reply_to_id,
                "is_retweet": tweet.is_retweet,
                "is_quote": tweet.is_quote,
                "has_media": tweet.has_media,
                "is_self_thread": tweet.is_self_thread,
                "is_thread_starter": tweet.is_thread_starter,
                "raw": tweet.raw,
            }
            if pretty:
                print(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default))
            else:
                print(json.dumps(obj, ensure_ascii=False, default=_json_default))

        async with aclosing(scrape_url(url, limit=max_tweets)) as stream:
            async for tweet in stream:
                total_count += 1
                seen_tweet_ids.add(tweet.id)

                # Track newest/oldest
                if newest_id is None:
                    newest_id = tweet.id
                oldest_id = tweet.id
                oldest_date = tweet.created_at

                # Verbose progress
                if verbose:
                    age = datetime.now(timezone.utc) - tweet.created_at
                    age_str = f"{age.days}d" if age.days > 0 else f"{age.seconds // 3600}h"
                    text_preview = tweet.text[:50].replace("\n", " ") + "..." if len(tweet.text) > 50 else tweet.text.replace("\n", " ")
                    thread_marker = " [thread]" if tweet.is_self_thread else ""
                    typer.echo(f"[{total_count}] @{tweet.screen_name} ({age_str} ago){thread_marker}: {text_preview}", err=True)
                elif not quiet and total_count % 20 == 0:
                    # Show progress every 20 tweets
                    typer.echo(f"Fetched {total_count} tweets...", err=True)

                # Check if we should stop based on --old cutoff
                if old_cutoff and tweet.created_at < old_cutoff:
                    if not quiet:
                        typer.echo(f"Reached tweets older than {old}, stopping.", err=True)
                    break

                # Check if already stored (cache hit)
                if store and store.has_tweet(tweet.id):
                    skipped_count += 1
                    if verbose:
                        typer.echo(f"  -> cached, skipping", err=True)
                    # If we're not in --old mode, stop on first cached tweet
                    if not old:
                        if not quiet:
                            typer.echo(f"Found cached tweet {tweet.id}, stopping.", err=True)
                        break
                    continue

                # Store the tweet
                if store:
                    store.store_tweet(tweet, target.key)
                new_count += 1

                # Output the tweet
                output_tweet(tweet)

                # Track threads to expand after main scrape
                # Expand if it's a thread continuation (is_self_thread) or thread starter
                if expand_threads and tweet.conversation_id:
                    if tweet.is_self_thread or tweet.is_thread_starter:
                        threads_to_expand.add(tweet.conversation_id)

        # Expand threads after main scrape finishes
        if expand_threads and threads_to_expand and backend:
            import random

            if not quiet:
                typer.echo(f"\nExpanding {len(threads_to_expand)} threads...", err=True)

            for i, conv_id in enumerate(threads_to_expand):
                # Human-like delay between thread fetches
                if i > 0:
                    delay = random.uniform(3.0, 6.0)
                    if verbose:
                        typer.echo(f"  Waiting {delay:.1f}s before next thread...", err=True)
                    await asyncio.sleep(delay)

                if verbose:
                    typer.echo(f"  Expanding thread {conv_id}...", err=True)

                async with aclosing(backend.iter_thread(conv_id)) as stream:
                    async for thread_tweet in stream:
                        if thread_tweet.id not in seen_tweet_ids:
                            seen_tweet_ids.add(thread_tweet.id)
                            thread_count += 1
                            if store:
                                store.store_tweet(thread_tweet, target.key)
                            output_tweet(thread_tweet)

        # Update timeline info
        if store and (newest_id or oldest_id):
            store.update_timeline_info(
                key=target.key,
                url=url,
                timeline_type=target.type.value,
                newest_tweet_id=newest_id,
                oldest_tweet_id=oldest_id,
            )

        if not quiet:
            summary = f"\nScraped {total_count} tweets: {new_count} new, {skipped_count} cached"
            if thread_count:
                summary += f", {thread_count} from threads"
            if oldest_date:
                age = datetime.now(timezone.utc) - oldest_date
                summary += f" (oldest: {age.days}d ago)"
            typer.echo(summary, err=True)

    try:
        asyncio.run(runner())
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted", err=True)
        raise typer.Exit(code=130)


@app.command()
def view(
    url: str = typer.Argument(..., help="X/Twitter timeline URL to view stored tweets"),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        help="Maximum number of tweets to output",
    ),
    pretty: bool = typer.Option(
        False,
        "--pretty",
        "-p",
        help="Pretty-print JSON output",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        "-s",
        help="Output as plain text for AI summarization (instead of JSON)",
    ),
    oldest_first: bool = typer.Option(
        False,
        "--oldest-first",
        help="Output oldest tweets first (default: newest first)",
    ),
    no_retweets: bool = typer.Option(
        False,
        "--no-retweets",
        help="Exclude retweets from output",
    ),
    thread: Optional[str] = typer.Option(
        None,
        "--thread",
        "-t",
        help="View a specific thread by conversation ID",
    ),
) -> None:
    """
    View already-scraped tweets from local database.

    Outputs stored tweets for a timeline URL as JSON (default) or plain text (--summary).
    Only shows tweets that were previously scraped - does not fetch new data.

    Examples:
        xdumper view "https://x.com/elonmusk"
        xdumper view "https://x.com/elonmusk" --limit 10 --pretty
        xdumper view "https://x.com/elonmusk" --summary --no-retweets
        xdumper view "https://x.com/elonmusk" --thread 1234567890
    """
    from .storage import TweetStore
    from .twitter.url_parser import parse_timeline_url

    cfg = load_config()
    store = TweetStore(cfg.store_path)

    # Handle thread view
    if thread:
        tweets = store.get_thread(thread)
        if not tweets:
            typer.echo(f"No tweets found for thread {thread}", err=True)
            raise typer.Exit(code=1)

        # Filter retweets if requested
        if no_retweets:
            tweets = [t for t in tweets if not t.get("is_retweet", False)]

        _output_tweets(tweets, pretty=pretty, summary=summary)
        return

    # Parse URL to get timeline key
    try:
        target = parse_timeline_url(url)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    # Check if we have data for this timeline
    info = store.get_timeline_info(target.key)
    if info is None:
        typer.echo(f"No stored data for {url}. Run 'xdumper scrape' first.", err=True)
        raise typer.Exit(code=1)

    # Get tweets
    order = "ASC" if oldest_first else "DESC"
    tweets = store.get_tweets_for_timeline(target.key, limit=limit, order=order)

    if not tweets:
        typer.echo(f"No tweets stored for {url}", err=True)
        raise typer.Exit(code=0)

    # Filter retweets if requested
    if no_retweets:
        tweets = [t for t in tweets if not t.get("is_retweet", False)]

    _output_tweets(tweets, pretty=pretty, summary=summary)


def _output_tweets(tweets: list[dict[str, Any]], pretty: bool, summary: bool) -> None:
    """Output tweets in JSON or summary format."""
    if not summary:
        # JSON output
        for tweet in tweets:
            if pretty:
                print(json.dumps(tweet, ensure_ascii=False, indent=2, default=_json_default))
            else:
                print(json.dumps(tweet, ensure_ascii=False, default=_json_default))
        return

    # Summary (plain text) output
    from collections import Counter

    # Count tweets per conversation to identify threads
    conv_counts: Counter[str] = Counter()
    for tweet in tweets:
        conv_id = tweet.get("conversation_id")
        if conv_id:
            conv_counts[conv_id] += 1

    output_lines = []
    for i, tweet in enumerate(tweets):
        tweet_id = tweet.get("id", "")
        screen_name = tweet.get("screen_name", "unknown")
        created_at = tweet.get("created_at", "")
        text = tweet.get("text", "")
        conv_id = tweet.get("conversation_id")

        # Format timestamp
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                time_str = created_at
        else:
            time_str = "unknown time"

        # Build tweet URL - use conversation_id for threads (points to main tweet)
        is_thread = conv_id and conv_counts[conv_id] > 1
        if is_thread:
            # Thread tweet - link to the main/first tweet in the thread
            tweet_url = f"https://x.com/{screen_name}/status/{conv_id}"
        else:
            # Regular tweet
            tweet_url = f"https://x.com/{screen_name}/status/{tweet_id}"

        # Build header with URL (add thread emoji for threads)
        if is_thread:
            header = f"@{screen_name} @ {time_str} - 🧵 {tweet_url}"
        else:
            header = f"@{screen_name} @ {time_str} - {tweet_url}"

        output_lines.append(header)
        output_lines.append(text)

        # Add separator between tweets (but not after the last one)
        if i < len(tweets) - 1:
            output_lines.append("")
            output_lines.append("------")
            output_lines.append("")

    print("\n".join(output_lines))


@app.command()
def login(
    url: str = typer.Option(
        "https://x.com",
        "--url",
        "-u",
        help="URL to navigate to for login",
    ),
) -> None:
    """
    Open browser for manual login (Patchright backend only).

    Opens Chrome with the persistent profile so you can log in to X/Twitter.
    The session will be saved and reused by subsequent scrape commands.

    Examples:
        xdumper login
        xdumper login --url "https://x.com/login"
    """
    cfg = load_config()

    if cfg.backend != "patchright":
        typer.echo(
            "Error: login command requires Patchright backend.\n"
            "Set XDUMPER_BACKEND=patchright and try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Opening browser with profile: {cfg.chrome_profile}", err=True)
    typer.echo("Log in to X/Twitter, then close the browser window when done.", err=True)
    typer.echo("", err=True)

    async def do_login() -> None:
        from patchright.async_api import async_playwright

        Path(cfg.chrome_profile).mkdir(parents=True, exist_ok=True)

        playwright = await async_playwright().start()
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=cfg.chrome_profile,
                channel="chrome",
                headless=False,  # Always show browser for login
                no_viewport=True,
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url)

            typer.echo("Browser opened. Close it when you're done logging in.", err=True)

            # Wait for browser to be closed by user
            await context.wait_for_event("close", timeout=0)

        finally:
            await playwright.stop()

    try:
        asyncio.run(do_login())
        typer.echo("\nSession saved! You can now use 'xdumper scrape' commands.", err=True)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted", err=True)
        raise typer.Exit(code=130)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Show version information."""
    from . import __version__

    typer.echo(f"xdumper {__version__}")


if __name__ == "__main__":
    app()
