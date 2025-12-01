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

Uses [Patchright](https://github.com/AuroraWright/patchright-python) (stealth-patched Playwright) for browser automation with bot detection evasion.

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

Supports both list and user profile URLs. Tweets are stored locally in SQLite to avoid re-scraping.

```bash
# Scrape a list
mise run xdumper scrape "https://x.com/i/lists/1409181262510690310"

# Scrape a user profile
mise run xdumper scrape "https://x.com/elonmusk"

# Limit number of tweets
mise run xdumper scrape "https://x.com/elonmusk" --limit 50

# Limit by pages (each page ~20 tweets)
mise run xdumper scrape "https://x.com/elonmusk" --pages 5

# Fetch older tweets up to 7 days ago
mise run xdumper scrape "https://x.com/elonmusk" --old 7d

# Pretty print JSON
mise run xdumper scrape "https://x.com/elonmusk" -n 50 --pretty

# Output only (don't store to database)
mise run xdumper scrape "https://x.com/elonmusk" --no-store

# Save to file
mise run xdumper scrape "https://x.com/elonmusk" -n 100 -q > tweets.jsonl
```

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
| `xdumper scrape <url>` | Scrape tweets from a list or user URL |
| `xdumper view <url>` | View stored tweets as JSON |
| `xdumper summary <url>` | View stored tweets as plain text (for AI summarization) |
| `xdumper version` | Show version info |

### Options for `scrape`

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum tweets to scrape |
| `--pages` | | Maximum pages to fetch (default: 10) |
| `--old` | | Fetch older tweets up to duration (e.g., '7d', '24h') |
| `--pretty` | `-p` | Pretty-print JSON output |
| `--no-store` | | Don't store tweets to database |
| `--quiet` | `-q` | Suppress progress messages |

### Options for `view`

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum tweets to output |
| `--pretty` | `-p` | Pretty-print JSON output |
| `--oldest-first` | | Output oldest tweets first |
| `--thread` | `-t` | View a specific thread by conversation ID |

### Options for `summary`

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-n` | Maximum tweets to include |
| `--oldest-first/--newest-first` | | Order of tweets (default: newest first) |
| `--no-retweets` | | Exclude retweets from output |

### Summary output example

```
xdumper summary "https://x.com/elonmusk" --limit 3 --no-retweets
```

```
@elonmusk • 2025-11-28 05:08
Nothing else matters if birth rate is far below replacement rate

------

@elonmusk • 2025-11-28 05:07
Wow

------

@elonmusk • 2025-11-28 04:55
Great work by the team!
```

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
