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
    pages: int = typer.Option(
        10,
        "--pages",
        help="Maximum pages to fetch (each page ~20 tweets)",
    ),
    old: Optional[str] = typer.Option(
        None,
        "--old",
        help="Fetch older tweets up to this duration (e.g., '7d', '24h')",
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
    - Scraping stops when encountering already-stored tweets

    Examples:
        xdumper scrape "https://x.com/i/lists/1409181262510690310" --limit 100
        xdumper scrape "https://x.com/elonmusk" --pages 5
        xdumper scrape "https://x.com/elonmusk" --old 7d
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
        new_count = 0
        skipped_count = 0
        total_count = 0
        # When --old is used, don't limit by pages (let the time cutoff handle it)
        # Otherwise use explicit --limit or --pages * 20
        if limit:
            max_tweets = limit
        elif old:
            max_tweets = None  # No limit when fetching old tweets
        else:
            max_tweets = pages * 20  # Approx 20 tweets per page
        newest_id: str | None = None
        oldest_id: str | None = None
        oldest_date: datetime | None = None

        async with aclosing(scrape_url(url, limit=max_tweets)) as stream:
            async for tweet in stream:
                total_count += 1

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
                    typer.echo(f"[{total_count}] @{tweet.screen_name} ({age_str} ago): {text_preview}", err=True)
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

                # Output JSON
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
                    "raw": tweet.raw,
                }
                if pretty:
                    print(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default))
                else:
                    print(json.dumps(obj, ensure_ascii=False, default=_json_default))

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
    oldest_first: bool = typer.Option(
        False,
        "--oldest-first",
        help="Output oldest tweets first (default: newest first)",
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

    Outputs stored tweets for a timeline URL as JSON. Only shows tweets that were
    previously scraped - does not fetch new data.

    Examples:
        xdumper view "https://x.com/elonmusk"
        xdumper view "https://x.com/elonmusk" --limit 10 --pretty
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

        for tweet in tweets:
            if pretty:
                print(json.dumps(tweet, ensure_ascii=False, indent=2, default=_json_default))
            else:
                print(json.dumps(tweet, ensure_ascii=False, default=_json_default))
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

    for tweet in tweets:
        if pretty:
            print(json.dumps(tweet, ensure_ascii=False, indent=2, default=_json_default))
        else:
            print(json.dumps(tweet, ensure_ascii=False, default=_json_default))


@app.command()
def version() -> None:
    """Show version information."""
    from . import __version__

    typer.echo(f"xdumper {__version__}")


if __name__ == "__main__":
    app()
