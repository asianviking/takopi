#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "markdown-it-py", "sulguk"]
# ///
import json
import re
import sys
from pathlib import Path

import requests
from markdown_it import MarkdownIt
from sulguk import transform_html

CREDS_PATH = Path.home() / ".codex" / "telegram.json"


def main() -> None:
    creds = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    bot_token = creds["bot_token"]
    chat_id = str(creds["chat_id"])

    payload = json.loads(sys.argv[1])

    md = payload["last-assistant-message"].rstrip()
    thread_id = payload.get("thread-id")
    cwd = payload.get("cwd")
    prompt = (payload.get("input-messages") or [""])[-1].rstrip()

    footer = "\n".join(
        line
        for line in [
            "---",
            f"- cwd: `{cwd}`" if cwd else "",
            f"- thread: `{thread_id}`" if thread_id else "",
            f"- prompt: `{prompt}`" if prompt else "",
        ]
        if line
    )

    md_full = md if not footer else f"{md}\n\n{footer}"

    html = MarkdownIt("commonmark", {"html": False}).render(md_full)
    result = transform_html(html)

    # Use "-" instead of "•" for list markers (keep offsets stable: 1 char -> 1 char)
    text = re.sub(r"(?m)^(\s*)• ", r"\1- ", result.text)

    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "entities": result.entities,
            "disable_web_page_preview": True,
        },
        timeout=15,
    ).raise_for_status()


if __name__ == "__main__":
    main()
