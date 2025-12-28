from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

from bridge_common import TelegramClient, RouteStore, parse_allowed_chat_ids

# -------------------- Codex runner --------------------


class CodexExecRunner:
    """
    Runs Codex in non-interactive mode:
      - new:    codex exec --json ... -
      - resume: codex exec --json ... resume <SESSION_ID> -
    """

    def __init__(self, codex_cmd: str, workspace: Optional[str], extra_args: list[str]) -> None:
        self.codex_cmd = codex_cmd
        self.workspace = workspace
        self.extra_args = extra_args

        # per-session locks to prevent concurrent resumes to same session_id
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def run(self, prompt: str, session_id: Optional[str]) -> Tuple[str, str]:
        """
        Returns (session_id, final_agent_message_text)
        """
        args = [self.codex_cmd, "exec", "--json"]
        args.extend(self.extra_args)
        if self.workspace:
            args.extend(["--cd", self.workspace])

        # Always pipe prompt via stdin ("-") to avoid quoting issues.
        if session_id:
            args.extend(["resume", session_id, "-"])
        else:
            args.append("-")

        # read both stdout+stderr without deadlock
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdin and proc.stdout and proc.stderr

        # send prompt then close stdin
        proc.stdin.write(prompt)
        proc.stdin.close()

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            for line in proc.stderr:
                stderr_lines.append(line)

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        found_session: Optional[str] = session_id
        last_agent_text: Optional[str] = None

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            # From Codex JSONL event stream
            if evt.get("type") == "thread.started":
                found_session = evt.get("thread_id") or found_session

            if evt.get("type") == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    last_agent_text = item["text"]

        rc = proc.wait()
        t.join(timeout=2.0)

        if rc != 0:
            tail = "".join(stderr_lines[-200:])
            raise RuntimeError(f"codex exec failed (rc={rc}). stderr tail:\n{tail}")

        if not found_session:
            raise RuntimeError("codex exec finished but no session_id/thread_id was captured")

        return found_session, (last_agent_text or "(No agent_message captured from JSON stream.)")

    def run_serialized(self, prompt: str, session_id: Optional[str]) -> Tuple[str, str]:
        """
        If resuming, serialize per-session.
        """
        if not session_id:
            return self.run(prompt, session_id=None)
        lock = self._lock_for(session_id)
        with lock:
            return self.run(prompt, session_id=session_id)


# -------------------- Telegram loop --------------------


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    db_path = os.environ.get("BRIDGE_DB", "./bridge_routes.sqlite3")
    allowed = parse_allowed_chat_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))

    codex_cmd = os.environ.get("CODEX_CMD", "codex")
    workspace = os.environ.get("CODEX_WORKSPACE")  # optional
    extra_args = shlex.split(os.environ.get("CODEX_EXEC_ARGS", ""))  # e.g. "--full-auto --search"

    bot = TelegramClient(token)
    store = RouteStore(db_path)
    runner = CodexExecRunner(codex_cmd=codex_cmd, workspace=workspace, extra_args=extra_args)

    pool = ThreadPoolExecutor(max_workers=int(os.environ.get("MAX_WORKERS", "4")))
    offset: Optional[int] = None

    print("Option1 bridge running (codex exec). Long-polling Telegram...")

    def handle(chat_id: int, user_msg_id: int, text: str, resume_session: Optional[str]) -> None:
        try:
            session_id, answer = runner.run_serialized(text, resume_session)
            sent_msgs = bot.send_message_chunked(
                chat_id=chat_id,
                text=answer,
                reply_to_message_id=user_msg_id,
            )
            for m in sent_msgs:
                store.link(chat_id, m["message_id"], "exec", session_id, meta={"workspace": workspace})
        except Exception as e:
            err = f"❌ Error:\n{e}"
            sent_msgs = bot.send_message_chunked(chat_id=chat_id, text=err, reply_to_message_id=user_msg_id)
            for m in sent_msgs:
                store.link(chat_id, m["message_id"], "exec", resume_session or "unknown", meta={"error": True})

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

            # If user replied to a bot message, route to that session
            resume_session: Optional[str] = None
            r = msg.get("reply_to_message")
            if r and "message_id" in r:
                route = store.resolve(chat_id, r["message_id"])
                if route and route.route_type == "exec":
                    resume_session = route.route_id

            pool.submit(handle, chat_id, user_msg_id, text, resume_session)


if __name__ == "__main__":
    main()
