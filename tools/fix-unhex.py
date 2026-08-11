#!/usr/bin/env python3
"""Fix the X'?' -> UNHEX(?) issue in users INSERT.

The X'?' approach is fundamentally broken because SQLAlchemy renders :p2 as %(p2)s
inside the hex literal quotes, producing X'%(p2)s' which is invalid SQL.

UNHEX(?) is equivalent: UNHEX('DA5F01E6...') returns the same binary as X'DA5F01E6...',
but the parameter is outside the hex literal syntax so SQLAlchemy can bind it correctly.
"""
PATH = "/opt/vpn-portal/app.py"
with open(PATH) as f:
    content = f.read()

# Fix 1: Change X'? to UNHEX(?) in users INSERT
old1 = "INSERT INTO users (name, password) VALUES (?, X'?)"
new1 = "INSERT INTO users (name, password) VALUES (?, UNHEX(?))"
if old1 in content:
    content = content.replace(old1, new1)
    print("[OK] Fixed users INSERT: X'?' -> UNHEX(?)")
elif "UNHEX(?)" in content and "users" in content.split("UNHEX(?)")[0][-200:]:
    print("[skip] Already fixed (UNHEX(?) present)")
else:
    print("[FAIL] old users INSERT not found")

with open(PATH, "w") as f:
    f.write(content)