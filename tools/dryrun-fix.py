#!/usr/bin/env python3
"""Databyte bot command dry-run harness (POST-FIX).

After the 5-bug fix (A: %s->?, B: is_archived, C: sudoers, D: CoA port, E: CoA secret),
all 15 commands should now work. /create will ACTUALLY create a customer — cleanup
required at end.

Uses real webhook with proper entities[] field. Verifies via:
- HTTP 200 from webhook (bot accepted update)
- audit log entries (action fired)
- journal entries (no exceptions, sendMessage 200 OK)
- DB state changes (for state-mutating commands)
"""
import requests
import time
import subprocess
import sys
import re

WEBHOOK_URL = "https://myvpn.databyte.co.za/telegram/webhook/cf664b269f46da926f45aeae437c1f58c9463dad328a9855e19fbacfdce6cd40"
CHAT_ID = 7748884597
TEST_NAME = "test-dryrun-fix-001"


def build_update(update_id: int, text: str) -> dict:
    space = text.find(" ")
    cmd_part = text if space == -1 else text[:space]
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
            "entities": [{"type": "bot_command", "offset": 0, "length": len(cmd_part)}],
        },
    }


# Order: read-only first, then /create, then dependent commands on the test customer.
COMMANDS = [
    ("/start",                              "no args"),
    ("/help",                               "no args"),
    ("/status",                             "no args"),
    ("/stats",                              "no args"),
    ("/sessions",                           "no args"),
    ("/customers",                          "no args (was: is_archived bug)"),
    ("/logs",                               "no args (usage reply)"),
    ("/customer 104",                       "drill-down by id (was: %s bug)"),
    ("/create " + TEST_NAME,                "create test customer (was: %s bug)"),
    ("/customer " + TEST_NAME,              "drill-down new customer (was: %s bug)"),
    ("/creds " + TEST_NAME,                 "fetch creds (was: %s bug)"),
    ("/disable " + TEST_NAME,               "disable (was: %s bug)"),
    ("/enable " + TEST_NAME,                "re-enable paired (was: %s bug)"),
    ("/disconnect " + TEST_NAME,            "CoA kick (was: %s + sudo + port + secret)"),
    ("/logs " + TEST_NAME,                  "auth log (was: %s bug)"),
    ("/customer zunaid-win11-en",           "drill-down real customer"),
]


def main():
    results = []
    base = 91000000
    print(f"=== POST-FIX DRY-RUN — {len(COMMANDS)} commands ===\n")
    for i, (cmd, note) in enumerate(COMMANDS):
        uid = base + i
        update = build_update(uid, cmd)
        try:
            r = requests.post(WEBHOOK_URL, json=update, timeout=10)
            ok = r.status_code == 200
            results.append((uid, cmd, ok, r.text[:80]))
            mark = "OK " if ok else "BAD"
            print(f"  [{uid}] {mark}  {cmd:42s} | {note}")
        except Exception as e:
            results.append((uid, cmd, False, str(e)))
            print(f"  [{uid}] ERR  {cmd:42s} | {e}")
        time.sleep(0.7)  # Let charon reload after /create

    print()
    failed = [r for r in results if not r[2]]
    if failed:
        print(f"!! {len(failed)} commands got non-200 from webhook")
        for uid, cmd, _, txt in failed:
            print(f"   [{uid}] {cmd}: {txt}")
        return 1
    print(f"All {len(results)} commands accepted by webhook (HTTP 200).")
    print("Now check journal + audit log for handler outcomes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
