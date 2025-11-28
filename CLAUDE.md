# Project Rules for Claude

## Code Style

- **No backward compatibility aliases**: When refactoring or adding new features, remove old code instead of keeping it as deprecated or for backward compatibility. Clean breaks are preferred over maintaining legacy APIs.

- **No unused code**: Delete code that is no longer used. Don't comment it out or mark it as deprecated.

- **Prefer editing over creating**: Always prefer editing existing files over creating new ones.

## Architecture

- **SQLite for storage**: Use SQLite with raw JSON as BLOB for storing scraped data. Use indexed columns for queryable fields.

- **Async throughout**: Use async/await for all I/O operations.

- **Backend abstraction**: Twitter API access goes through the `TimelineBackend` abstract class.

## CLI

- **JSON output**: CLI commands output JSON (JSONL for streaming).

- **Progress to stderr**: Progress messages go to stderr, data goes to stdout.

- **Typer framework**: Use typer for CLI commands.
