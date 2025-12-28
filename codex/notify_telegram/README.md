# Notify Telegram (Codex)

Send Codex completion summaries to Telegram with safe Markdown rendering and stable list bullets.

## Install

1. Ensure `uv` is installed.
2. Copy the script into your repo (already in this folder).
3. Create your Telegram creds file at `~/.codex/telegram.json`.

Example:

```json
{
  "bot_token": "123456:ABCDEF...",
  "chat_id": "462722"
}
```

## Configure

Add a `notify` entry to `~/.codex/config.toml`:

```toml
notify = ["uv", "run", "/absolute/path/to/agents/codex/notify_telegram/notify_telegram.py"]
```

## Notes

- The script reads `last-assistant-message` and treats it as Markdown.
- Markdown is rendered to HTML, converted to Telegram text/entities via `sulguk`, then posted with `requests`.
- List bullets are normalized from `•` to `-` to keep Telegram output consistent.

## Files

- `notify_telegram.py`: the notifier script
- `telegram.json.example`: sample credentials file
- `config.toml.example`: sample Codex `notify` config line
