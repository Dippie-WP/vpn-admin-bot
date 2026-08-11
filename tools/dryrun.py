#!/usr/bin/env python3
"""Databyte bot command dry-run harness.

Posts real Telegram-shaped Update JSON to the webhook with proper entities[]
field. Then we read the journal to verify what actually fired.

This is the test the bot should have had all along — the previous session
shipped with synthetic-JSON tests that omitted entities[] and gave false
"LIVE" results.
"""
import requests
import json
import time
import sys

WEBHOOK_URL = "https://myvpn.databyte.co.za/telegram/webhook/cf664b269f46da926f45aeae437c1f58c9463dad328a9855e19fbacfdce6cd40"
CHAT_ID = 7748884597


def build_update(update_id: int, text: str) -> dict:
    """Build a minimal valid Telegram Update JSON for a command message.

    entities[0].type must be 'bot_command' with length covering just the
    command name (without arguments) — PTB CommandHandler.check_update
    in v22 requires this to match.
    """
    # Find command name length (everything before first space)
    space = text.find(" ")
    cmd_part = text if space == -1 else text[:space]
    cmd_len = len(cmd_part)
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(time.time()),
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {
                "id": CHAT_ID,
                "is_bot": False,
                "first_name": "Z",
                "username": "zuzu172",
            },
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": cmd_len}],
        },
    }


COMMANDS = [
    ("/start",                              "no args"),
    ("/help",                               "no args"),
    ("/status",                             "no args"),
    ("/stats",                              "no args"),
    ("/sessions",                           "no args"),
    ("/customers",                          "no args (will crash: is_archived)"),
    ("/customer zunaid-win11-en-laptop",    "id-or-name (will crash: %s)"),
    ("/customer 104",                       "id-only (will crash: %s)"),
    ("/create test-dryrun-001",             "new customer (will crash: %s)"),
    ("/creds zunaid-win11-en-laptop",       "fetch creds (will crash: %s)"),
    ("/disable zunaid-win11-en-laptop",     "disable (will crash: %s)"),
    ("/enable zunaid-win11-en-laptop",      "enable (will crash: %s)"),
    ("/disconnect zunaid-win11-en-laptop",  "CoA kick (will crash: %s + sudo)"),
    ("/logs zunaid-win11-en-laptop",        "logs (will crash: %s)"),
    ("/logs",                               "no args (should reply usage)"),
]


def main():
    results = []
    base_id = 90000000
    for i, (cmd, note) in enumerate(COMMANDS):
        update_id = base_id + i
        update = build_update(update_id, cmd)
        try:
            r = requests.post(WEBHOOK_URL, json=update, timeout=10)
            ok = r.status_code == 200
            results.append((update_id, cmd, ok, r.text[:120]))
            print(f"[{update_id}] {cmd:45s} HTTP {r.status_code} note={note}")
        except Exception as e:
            results.append((update_id, cmd, False, str(e)))
            print(f"[{update_id}] {cmd:45s} ERROR  {e}")
        time.sleep(0.4)  # give bot time to process each
    print()
    print("Summary:")
    for uid, cmd, ok, txt in results:
        marker = "OK " if ok else "BAD"
        print(f"  {marker} [{uid}] {cmd:45s} {txt}")
    return 0 if all(r[2] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
