#!/usr/bin/env python3
"""Portal fixes v4 — clean, no escape sequence issues.

Fix 1: shq() — remove single-quote wrapping (breaks cat > FILE via subprocess).
Fix 2: create_client — wrap DB writes in single transaction with rollback.
"""
import hashlib
import re
import subprocess
import sys

APP = "/opt/vpn-portal/app.py"

with open(APP) as f:
    content = f.read()
original = content
changes = []


def replace(old: str, new: str, label: str, required_count: int = 1):
    """Replace exact `old` with `new`. Fail if not found."""
    n = content.count(old)
    if n == 0:
        print(f"  [FAIL] {label}: old text not found")
        return False
    if n != required_count:
        print(f"  [FAIL] {label}: found {n} occurrences, expected {required_count}")
        return False
    globals()["content"] = content.replace(old, new)
    changes.append(label)
    print(f"  [{n}x]  {label}")
    return True


# --- Fix 1: shq() — remove single-quote wrapping ---
# The file has: return "'" + s.replace("'", "'\\''") + "'"
# In the raw bytes, this is: return "'" + s.replace("'", "'\\''") + "'"
# where \\ is TWO literal backslash characters in the file.
# We need to match this exact byte sequence.
print("=== Fix 1: shq() in _run_remote ===")

# Use regex on raw bytes to avoid escape sequence confusion
pattern = re.compile(
    rb'    def shq\(s: str\) -> str:\n        return .+?\n',
    re.DOTALL
)
m = pattern.search(content.encode())
if m:
    old_block = m.group(0).decode()
    new_block = (
        '    def shq(s: str) -> str:\n'
        '        # No quoting: args are passed as a list to subprocess.run (no shell),\n'
        '        # so any quotes we add reach the remote shell verbatim and break\n'
        '        # redirect parsing (e.g. `cat > FILE` becomes `\'cat > FILE\'`, which\n'
        '        # the remote shell tries to execute as a single command name).\n'
        '        # All paths in this codebase are controlled (no spaces / shell metas).\n'
        '        return s\n'
    )
    content = content.replace(old_block, new_block, 1)
    changes.append("shq() in _run_remote")
    print(f"  [1x]  shq() in _run_remote (replaced via regex)")
else:
    print(f"  [FAIL] shq() not found via regex")

# --- Fix 2: create_client single transaction ---
# Replace the try block with a with-block version
print("\n=== Fix 2: create_client single transaction ===")

# Find the try block boundaries
lines = content.split('\n')
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    # Look for the pattern: "    cust_id = None" followed by "    user_id = None" and "    dev_id  = None" and "    try:"
    if (start_idx is None and
        line.strip() == 'cust_id = None' and
        i + 3 < len(lines) and
        lines[i+1].strip() == 'user_id = None' and
        lines[i+2].strip() == 'dev_id  = None' and
        lines[i+3].strip() == 'try:'):
        start_idx = i
    # End: the line "        reload_charon_creds()" (just before the except)
    if start_idx is not None and line.strip() == 'reload_charon_creds()':
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"  [FAIL] Could not find try block boundaries (start={start_idx}, end={end_idx})")
else:
    # Build the new with-block version
    new_block_lines = [
        '    cust_id = None',
        '    user_id = None',
        '    dev_id  = None',
        '    eap_block_written = False',
        '    try:',
        '        with portal_auth._engine().begin() as _tx_conn:',
        '            wrapped = portal_auth._Conn(_tx_conn)',
        '            try:',
        '                wrapped.execute(',
        '                    "INSERT INTO customers (name, display_name, telegram_username, is_operator, is_active, "',
        '                    "over_quota, data_limit_bytes, data_used_bytes, tier_id, status, max_devices, "',
        '                    "bandwidth_down_mbps, bandwidth_up_mbps, "',
        '                    "created_at, updated_at, notes, billing_id, email) VALUES "',
        '                    "(?, ?, ?, 0, 1, 0, ?, 0, ?, \'active\', 1, ?, ?, ?, ?, ?, ?, ?)",',
        '                    (cust_name, req.display_name, req.telegram_username,',
        '                     int(data_limit), int(tier_id),',
        '                     int(bandwidth_down_mbps), int(bandwidth_up_mbps),',
        '                     now, now, req.notes, req.billing_id, req.email)',
        '                )',
        '                cust_id = wrapped.execute(',
        '                    "SELECT id FROM customers WHERE name = ?", (cust_name,)',
        '                ).fetchone()["id"]',
        '',
        '                wrapped.execute(',
        '                    "INSERT INTO users (name, password) VALUES (?, X?)",',
        '                    (eap_identity, ntlm.hex().upper())',
        '                )',
        '                user_id = wrapped.execute(',
        '                    "SELECT id FROM users WHERE name = ?", (eap_identity,)',
        '                ).fetchone()["id"]',
        '',
        '                wrapped.execute(',
        '                    "INSERT INTO devices (customer_id, strongswan_user_id, device_name, device_type, "',
        '                    "os_version, notes, is_active, created_at, updated_at) VALUES "',
        '                    "(?, ?, ?, ?, ?, ?, 1, ?, ?)",',
        '                    (int(cust_id), int(user_id), req.device_name, req.device_type,',
        '                     req.os_version, req.notes, now, now)',
        '                )',
        '',
        '                # v1.4.0 — Bug #2: populate customers.user_id with the user\'s PK.',
        '                wrapped.execute(',
        '                    "UPDATE customers SET user_id = ? WHERE id = ?",',
        '                    (int(user_id), int(cust_id))',
        '                )',
        '                dev_id = wrapped.execute(',
        '                    "SELECT id FROM devices WHERE device_name = ? AND customer_id = ?",',
        '                    (req.device_name, int(cust_id))',
        '                ).fetchone()["id"]',
        '',
        '                # Phase 4.3 + 4.7 — radcheck + usergroup in the SAME transaction.',
        '                # Idempotent wipe first (defense in depth).',
        '                wrapped.execute("DELETE FROM radcheck WHERE username = ?", (eap_identity,))',
        '                wrapped.execute(',
        '                    "INSERT INTO radcheck (username, attribute, op, value) VALUES "',
        '                    "(?, \'Cleartext-Password\', \':=\', ?)",',
        '                    (eap_identity, password)',
        '                )',
        '                wrapped.execute(',
        '                    "INSERT INTO radcheck (username, attribute, op, value) VALUES "',
        '                    "(?, \'NT-Password\', \':=\', ?)",',
        '                    (eap_identity, ntlm.hex().upper())',
        '                )',
        '                wrapped.execute(',
        '                    "INSERT INTO radusergroup (username, groupname, priority) VALUES (?, ?, ?)",',
        '                    (eap_identity, "default", 1)',
        '                )',
        '',
        '                # 7. EAP block — if this raises, the with-block rolls back the DB writes',
        '                append_eap_block(eap_identity, password)',
        '                eap_block_written = True',
        '',
        '                # 8. Reload charon — if this raises, the with-block rolls back',
        '                reload_charon_creds()',
        '            except Exception:',
        '                raise'
    ]
    new_block = '\n'.join(new_block_lines)

    # Replace lines[start_idx..end_idx] with new_block
    new_lines = lines[:start_idx] + new_block_lines + lines[end_idx+1:]
    content = '\n'.join(new_lines)
    changes.append("create_client try block (with-block + parametrized + radcheck in-txn)")
    print(f"  [{end_idx - start_idx + 1} lines replaced]  create_client try block refactored")

# Write back
with open(APP, "w") as f:
    f.write(content)

with open(APP, "rb") as f:
    new_sha = hashlib.sha256(f.read()).hexdigest()

print(f"\n=== Summary ===")
print(f"Original size: {len(original)} bytes")
print(f"New size:      {len(content)} bytes")
print(f"Delta:         {len(content) - len(original):+d} bytes")
print(f"New SHA256:    {new_sha}")
print(f"Changes:       {len(changes)}")
for c in changes:
    print(f"  - {c}")

# Validate Python syntax
print(f"\n=== Syntax check ===")
r = subprocess.run(
    ["python3", "-c", f"import ast; ast.parse(open('{APP}').read())"],
    capture_output=True, text=True
)
if r.returncode == 0:
    print("  [OK] Python syntax valid")
else:
    print(f"  [FAIL] {r.stderr[:500]}")
    sys.exit(1)