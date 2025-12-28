#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "markdown-it-py", "sulguk", "tomli; python_version < '3.11'"]
# ///
import json
import re
import sys
from pathlib import Path

import requests
from markdown_it import MarkdownIt
from sulguk import transform_html

CONFIG_PATH = Path.home() / ".codex" / "telegram.toml"
ERR_PATH = Path.home() / ".codex" / "telegram_last_error.txt"


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[import-not-found]
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _config_get(config: dict, key: str):
    if key in config:
        return config[key]
    nested = config.get("telegram")
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    return None


def main() -> None:
    config = _load_toml(CONFIG_PATH)
    bot_token = _config_get(config, "bot_token")
    chat_id = _config_get(config, "chat_id")
    if not bot_token or chat_id is None:
        raise KeyError("telegram.toml must include bot_token and chat_id")
    bot_token = str(bot_token)
    chat_id = str(chat_id)

    event = json.loads(sys.argv[1])

    md = event["last-assistant-message"].rstrip()
    thread_id = event.get("thread-id")
    if thread_id:
        md += f"\n\nthread: `{thread_id}`"

    html = MarkdownIt("commonmark", {"html": False}).render(md)
    rendered = transform_html(html)

    text = re.sub(r"(?m)^(\s*)•", r"\1-", rendered.text)

    # FIX: Telegram requires MessageEntity.language (if present) to be a String.
    entities = []
    for e in rendered.entities:
        d = dict(e)
        if "language" in d and not isinstance(d["language"], str):
            d.pop("language", None)
        entities.append(d)

    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "entities": entities,
            "disable_web_page_preview": True,
        },
        timeout=15,
    )

    try:
        data = r.json()
    except Exception:
        data = {"ok": False, "description": r.text}

    if not (r.status_code == 200 and data.get("ok") is True):
        ERR_PATH.write_text(
            f"{r.status_code}\n{data.get('description','')}\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
