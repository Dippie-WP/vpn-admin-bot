#!/usr/bin/env python3
"""Fix _audit() to use parameterized queries instead of f-string interpolation.

The bug: _audit() builds SQL via f-string, embedding the JSON payload via {_q(raw)}.
_q() escapes " to '' but the JSON : separators get interpreted by SQLAlchemy's
text() as named-param syntax, producing %(119)s patterns that can't be bound
because the params dict is empty.

The fix: use ? placeholders and pass values as a tuple. The _qmark_to_named
helper will convert ? to :p1, :p2, etc., and SQLAlchemy will bind them from
the params tuple.
"""
PATH = "/opt/vpn-portal/app.py"

with open(PATH) as f:
    content = f.read()

# Read the exact bytes around _audit to find the exact old text
old = '''def _audit(actor: str, action: str, payload: dict) -> None:
    """Write to audit_log on LXC 903.

    Schema: actor TEXT, action TEXT, target_type TEXT, target_id INTEGER,
    payload TEXT, created_at INTEGER.
    """
    import json as _json
    raw = _json.dumps(payload, separators=(",", ":"))
    target_type = payload.pop("_target_type", None) if isinstance(payload, dict) else None
    target_id   = payload.pop("_target_id",   None) if isinstance(payload, dict) else None
    sql = (
        f"INSERT INTO audit_log (actor, action, target_type, target_id, payload, created_at) "
        f"VALUES ({_q(actor)}, {_q(action)}, "
        f"{_q(target_type) if target_type is not None else 'NULL'}, "
        f"{int(target_id) if target_id is not None else 'NULL'}, "
        f"{_q(raw)}, UNIX_TIMESTAMP());"
    )
    try:
        db_exec(sql)
    except HTTPException:
        pass'''

new = '''def _audit(actor: str, action: str, payload: dict) -> None:
    """Write to audit_log.

    Schema: actor TEXT, action TEXT, target_type TEXT, target_id INTEGER,
    payload TEXT, created_at INTEGER.

    Uses parameterized queries (not f-string interpolation) so the JSON
    payload's ':' separators don't get parsed as SQLAlchemy named-param
    syntax (which produced %(key)s patterns that couldn't be bound).
    """
    import json as _json
    target_type = payload.pop("_target_type", None) if isinstance(payload, dict) else None
    target_id   = payload.pop("_target_id",   None) if isinstance(payload, dict) else None
    raw = _json.dumps(payload, separators=(",", ":"))
    sql = (
        "INSERT INTO audit_log (actor, action, target_type, target_id, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, UNIX_TIMESTAMP())"
    )
    try:
        db_exec(sql, (
            actor,
            action,
            target_type,
            int(target_id) if target_id is not None else None,
            raw,
        ))
    except HTTPException:
        pass'''

n = content.count(old)
if n == 1:
    content = content.replace(old, new)
    with open(PATH, "w") as f:
        f.write(content)
    print(f"[1x]  Fixed _audit() to use parameterized queries")
else:
    print(f"[FAIL] old text not found uniquely (count={n})")
    # Try to find what's there
    import re
    m = re.search(r'def _audit\(.*?\n(?=def |\Z)', content, re.DOTALL)
    if m:
        print(f"  current _audit function (first 300 chars):")
        print(repr(m.group(0)[:300]))