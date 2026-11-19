# Reddit Library

A private research tool. It turns top threads from game design and TCG subreddits into a book per subreddit, a master book of cross-subreddit topics, and an ideas board, with a local app for reading and chatting with them.

The full README (starting the app, adding threads, managing subreddits and games, switching collectors, config reference) will be written in Phase 9.

## Quick check

```
uv sync
uv run rl env-check
```

Secrets go in `.env` (copy `.env.example`). Raw scraped data in `data/raw/` is never committed. Books are summaries with links back to Reddit; thread text is never republished.
