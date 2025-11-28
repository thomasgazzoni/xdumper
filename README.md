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

## Authentication

xdumper uses browser cookies for authentication - no email password required.

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
| `xdumper add-account` | Add account with cookies |
| `xdumper accounts` | List accounts |
| `xdumper scrape <url>` | Scrape tweets from a list or user URL |
| `xdumper view <url>` | View stored tweets from database |
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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XDUMPER_DB` | `~/.xdumper/accounts.db` | Path to accounts database |
| `XDUMPER_STORE` | `~/.xdumper/tweets.db` | Path to tweet storage database |
| `XDUMPER_LOG_LEVEL` | `WARNING` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `XDUMPER_PROXY` | | Proxy URL (e.g., `socks5://127.0.0.1:1080`) |

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
