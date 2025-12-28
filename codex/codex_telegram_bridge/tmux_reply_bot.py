from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from bridge_common import (
    TelegramClient,
    RouteStore,
    config_get,
    load_telegram_config,
    parse_allowed_chat_ids,
    parse_chat_id_list,
)


def tmux_send_text(target: str, text: str, press_enter: bool = True) -> None:
    """
    Send text to tmux target pane/session.
    If your Telegram messages include newlines, we replace them with literal '\n'
    to avoid accidentally submitting early.
    """
    safe = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    subprocess.check_call(["tmux", "send-keys", "-t", target, "-l", safe])
    if press_enter:
        subprocess.check_call(["tmux", "send-keys", "-t", target, "Enter"])


def main() -> None:
    config = load_telegram_config()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or config_get(config, "bot_token") or ""
    db_path = os.environ.get("BRIDGE_DB") or config_get(config, "bridge_db") or "./bridge_routes.sqlite3"
    allowed = parse_allowed_chat_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))
    if allowed is None:
        allowed = parse_chat_id_list(config_get(config, "allowed_chat_ids"))

    bot = TelegramClient(token)
    store = RouteStore(db_path)

    offset: Optional[int] = None
    print("Option3 reply bot running (tmux injector). Long-polling Telegram...")

    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout_s=50, allowed_updates=["message"])
        except Exception as e:
            print(f"[telegram] get_updates error: {e}")
            time.sleep(2.0)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            if "text" not in msg:
                continue

            chat_id = msg["chat"]["id"]
            if allowed is not None and int(chat_id) not in allowed:
                continue
            if msg.get("from", {}).get("is_bot"):
                continue

            text = msg["text"]
            user_msg_id = msg["message_id"]

            r = msg.get("reply_to_message")
            if not (r and "message_id" in r):
                # In tmux mode we only route replies (no reply => ignore or treat as new session)
                bot.send_message(
                    chat_id=chat_id,
                    text="Reply to a Codex message (from the bot) so I know which tmux session to send this to.",
                    reply_to_message_id=user_msg_id,
                )
                continue

            route = store.resolve(chat_id, r["message_id"])
            if not route or route.route_type != "tmux":
                bot.send_message(
                    chat_id=chat_id,
                    text="I don't know which tmux session this reply belongs to (no routing record found).",
                    reply_to_message_id=user_msg_id,
                )
                continue

            tmux_target = route.route_id
            try:
                tmux_send_text(tmux_target, text, press_enter=True)
                bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Sent to tmux target: {tmux_target}",
                    reply_to_message_id=user_msg_id,
                )
            except Exception as e:
                bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Failed to send to tmux ({tmux_target}): {e}",
                    reply_to_message_id=user_msg_id,
                )


if __name__ == "__main__":
    main()
