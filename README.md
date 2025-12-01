# xdumper

X/Twitter list and user profile scraper.

## Prerequisites

Install [mise](https://mise.jdx.dev/getting-started.html):

```bash
curl https://mise.run | sh
```

## Setup

```bash
git clone <repo-url>
cd xdumper

mise trust
mise install
mise run setup
```

## Backends

xdumper supports two backends for fetching tweets:

| Backend | Description | Auth Method |
|---------|-------------|-------------|
| `twscrape` (default) | Uses twscrape library | Browser cookies |
| `patchright` | Browser automation with bot detection evasion | Browser login |

Set backend via environment variable:
```bash
export XDUMPER_BACKEND=patchright  # or twscrape (default)
```

---

## Patchright Backend (Recommended)

Uses [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) (stealth-patched Playwright) for browser automation with bot detection evasion.

### Login

Open browser to log in to X/Twitter. Session is saved to the Chrome profile.

```bash
mise run xdumper login
```

Once logged in, close the browser. Your session persists for future scraping.

### Scrape

```bash
# Scrape tweets (uses saved session)
mise run xdumper scrape "https://x.com/elonmusk" --limit 50

# Scrape a list
mise run xdumper scrape "https://x.com/i/lists/1409181262510690310"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XDUMPER_CHROME_PROFILE` | `~/.xdumper/chrome-profile` | Chrome profile directory |
| `XDUMPER_HEADLESS` | `false` | Run browser headless (not recommended) |

---

## Twscrape Backend (Default)

Uses browser cookies for authentication - no email password required.

### Get cookies from your browser

1. Log into X/Twitter in your browser
2. Open DevTools (F12) → Application → Cookies → `https://x.com`
3. Copy the values for `auth_token` and `ct0`

### Add account

```bash
mise run add-account
```

You'll be prompted for:
- **Username**: Your X/Twitter username
- **Cookies**: JSON with your cookies, e.g.:
  ```json
  {"auth_token": "your_auth_token_here", "ct0": "your_ct0_here"}
  ```

or in one line:

```bash
mise run add-account -u username -c '{"auth_token": "xxx", "ct0": "yyy"}'
```

### Verify account

```bash
mise run accounts
```

## Usage

### Scrape tweets

Supports list, user profile, and thread URLs. Tweets are stored locally in SQLite to avoid re-scraping.

```bash
# Scrape a list
mise run xdumper scrape "https://x.com/i/lists/1409181262510690310"

# Scrape a user profile
mise run xdumper scrape "https://x.com/elonmusk"

# Scrape a thread (conversation)
mise run xdumper scrape "https://x.com/letz_ai/status/1993362758054580609"

# Limit number of tweets
mise run xdumper scrape "https://x.com/elonmusk" --limit 50

# Fetch older tweets up to 7 days ago
mise run xdumper scrape "https://x.com/elonmusk" --old 7d

# Pretty print JSON
mise run xdumper scrape "https://x.com/elonmusk" -n 50 --pretty

# Output only (don't store to database)
mise run xdumper scrape "https://x.com/elonmusk" --no-store

# Save to file
mise run xdumper scrape "https://x.com/elonmusk" -n 100 -q > tweets.jsonl
```

**Thread scraping**: When you provide a tweet URL (`/status/ID`), xdumper fetches the full thread from that tweet's author (replies from other users are excluded).

### View stored tweets

View previously scraped tweets from local database (no API calls).

```bash
# View all stored tweets for a URL
mise run xdumper view "https://x.com/elonmusk"

# Limit output
mise run xdumper view "https://x.com/elonmusk" --limit 10

# View oldest first
mise run xdumper view "https://x.com/elonmusk" --oldest-first

# View a specific thread
mise run xdumper view "https://x.com/elonmusk" --thread 1234567890123456789
```

### Available Tasks

```bash
mise tasks
```

| Task | Description |
|------|-------------|
| `mise run setup` | Install package |
| `mise run add-account` | Add X account with cookies |
| `mise run accounts` | List configured accounts |
| `mise run xdumper` | Run xdumper CLI |

### xdumper Commands

| Command | Description |
|---------|-------------|
| `xdumper login` | Open browser for login (Patchright backend only) |
| `xdumper add-account` | Add account with cookies (Twscrape backend) |
| `xdumper accounts` | List accounts (Twscrape backend) |
| `xdumper scrape <url>` | Scrape tweets from list, user, or thread URL |
| `xdumper view <url>` | View stored tweets (JSON or plain text) |
| `xdumper version` | Show version info |

### Options for `scrape`

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum tweets to scrape |
| `--old` | | Fetch older tweets up to duration (e.g., '7d', '24h') |
| `--expand-threads` | `-e` | Auto-fetch full threads when detecting self-thread tweets |
| `--pretty` | `-p` | Pretty-print JSON output |
| `--no-store` | | Don't store tweets to database |
| `--quiet` | `-q` | Suppress progress messages |
| `--verbose` | `-v` | Show detailed progress |

Without `--limit` or `--old`, scraping continues until cached tweets or end of timeline.

**Thread expansion**: When `--expand-threads` is enabled, xdumper detects self-thread tweets (where the user replies to their own tweet) and fetches the full thread from the API. The output includes an `is_self_thread` field to identify thread tweets.

### Options for `view`

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum tweets to output |
| `--pretty` | `-p` | Pretty-print JSON output |
| `--summary` | `-s` | Output as plain text for AI summarization |
| `--oldest-first` | | Output oldest tweets first |
| `--no-retweets` | | Exclude retweets from output |
| `--thread` | `-t` | View a specific thread by conversation ID |

### Summary output example

```
xdumper view "https://x.com/elonmusk" --summary --limit 3 --no-retweets
```

```
@elonmusk @ 2025-11-28 05:08 - https://x.com/elonmusk/status/1234567890
Nothing else matters if birth rate is far below replacement rate

------

@elonmusk @ 2025-11-28 05:07 - 🧵 https://x.com/elonmusk/status/1234567891
Wow, this is amazing!

------

@elonmusk @ 2025-11-28 04:55 - 🧵 https://x.com/elonmusk/status/1234567891
Great work by the team!
```

For thread tweets, a 🧵 emoji is shown and the URL points to the main/first tweet of the thread.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XDUMPER_BACKEND` | `twscrape` | Backend to use (`twscrape` or `patchright`) |
| `XDUMPER_DB` | `~/.xdumper/accounts.db` | Path to accounts database (twscrape) |
| `XDUMPER_STORE` | `~/.xdumper/tweets.db` | Path to tweet storage database |
| `XDUMPER_LOG_LEVEL` | `WARNING` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `XDUMPER_PROXY` | | Proxy URL (e.g., `socks5://127.0.0.1:1080`) |
| `XDUMPER_CHROME_PROFILE` | `~/.xdumper/chrome-profile` | Chrome profile directory (patchright) |
| `XDUMPER_HEADLESS` | `false` | Run browser headless (patchright) |

## Output Format

Each tweet is output as a JSON object:

```json
{
  "id": "1234567890",
  "created_at": "2024-01-15T12:30:00+00:00",
  "user_id": "987654321",
  "screen_name": "username",
  "text": "Tweet content here",
  "conversation_id": "1234567890",
  "in_reply_to_id": null,
  "is_retweet": false,
  "is_quote": false,
  "has_media": false,
  "raw": { }
}
```

## License

MIT
