"""URL parsing for X/Twitter timeline URLs."""

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class TimelineType(str, Enum):
    LIST = "list"
    USER = "user"


@dataclass
class TimelineTarget:
    """Parsed timeline target from a URL."""

    type: TimelineType
    key: str  # e.g. "list:1409181262510690310" or "user:elonmusk"
    url: str
    list_id: str | None = None
    screen_name: str | None = None


LIST_PATH_RE = re.compile(r"^/i/lists/(?P<list_id>\d+)$")
# Match user profile: /@username or /username (not /i/..., /settings, etc.)
USER_PATH_RE = re.compile(r"^/@?(?P<screen_name>[A-Za-z0-9_]{1,15})(?:/(?:with_replies)?)?$")
# Reserved paths that are NOT user profiles
RESERVED_PATHS = {"i", "home", "explore", "search", "notifications", "messages", "settings", "compose", "intent"}


def parse_timeline_url(url: str) -> TimelineTarget:
    """
    Parse X/Twitter URLs.

    Supported formats:
      - https://x.com/i/lists/{list_id}
      - https://twitter.com/i/lists/{list_id}
      - https://x.com/{username}
      - https://x.com/@{username}
      - https://twitter.com/{username}

    Args:
        url: The X/Twitter URL to parse

    Returns:
        TimelineTarget with parsed information

    Raises:
        ValueError: If URL is not a supported format
    """
    parsed = urlparse(url)

    # Validate domain
    if parsed.netloc not in ("x.com", "twitter.com", "www.x.com", "www.twitter.com"):
        raise ValueError(f"Unsupported domain: {parsed.netloc}")

    path = parsed.path

    # Check for list URL first
    m = LIST_PATH_RE.match(path)
    if m:
        list_id = m.group("list_id")
        return TimelineTarget(
            type=TimelineType.LIST,
            key=f"list:{list_id}",
            url=url,
            list_id=list_id,
        )

    # Check for user profile URL
    m = USER_PATH_RE.match(path)
    if m:
        screen_name = m.group("screen_name")
        # Check it's not a reserved path
        if screen_name.lower() not in RESERVED_PATHS:
            return TimelineTarget(
                type=TimelineType.USER,
                key=f"user:{screen_name.lower()}",
                url=url,
                screen_name=screen_name,
            )

    raise ValueError(f"Unsupported or unrecognized X timeline URL: {url}")
