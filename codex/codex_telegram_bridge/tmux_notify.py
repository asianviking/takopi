from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from bridge_common import TelegramClient, RouteStore, config_get, load_telegram_config


def main() -> None:
    config = load_telegram_config()
    default_chat_id = config_get(config, "chat_id")
    if isinstance(default_chat_id, str):
        default_chat_id = int(default_chat_id) if default_chat_id.strip() else None
    elif not isinstance(default_chat_id, int):
        default_chat_id = None

    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", type=int, default=default_chat_id, required=default_chat_id is None)
    ap.add_argument("--tmux-target", type=str, required=True, help='tmux target, e.g. "codex1:0.0" or "codex1"')
    ap.add_argument(
        "--db",
        type=str,
        default=os.environ.get("BRIDGE_DB") or config_get(config, "bridge_db") or "./bridge_routes.sqlite3",
    )
    ap.add_argument("--reply-to", type=int, default=None, help="Optional Telegram message_id to reply to")
    ap.add_argument("--text", type=str, default=None, help="Message text. If omitted, read stdin.")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or config_get(config, "bot_token") or ""
    bot = TelegramClient(token)
    store = RouteStore(args.db)

    text = args.text
    if text is None:
        text = sys.stdin.read()

    sent = bot.send_message_chunked(
        chat_id=args.chat_id,
        text=text,
        reply_to_message_id=args.reply_to,
    )

    # Store mapping for every chunk so user can reply to any chunk.
    for m in sent:
        store.link(args.chat_id, m["message_id"], "tmux", args.tmux_target, meta={})


if __name__ == "__main__":
    main()
