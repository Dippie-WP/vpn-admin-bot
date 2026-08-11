#!/usr/bin/env python3
"""Fix the X? -> X'? syntax issue in users INSERT."""
PATH = "/opt/vpn-portal/app.py"
with open(PATH) as f:
    content = f.read()

# The broken SQL: INSERT INTO users (name, password) VALUES (?, X?)
# _qmark_to_named converts ? -> :p1, :p2, producing X:p2 which is invalid SQL.
# Fix: X? -> X'? so conversion produces X':p2' (valid hex literal with bound param).
old = "INSERT INTO users (name, password) VALUES (?, X?)"
new = "INSERT INTO users (name, password) VALUES (?, X'?)"

n = content.count(old)
if n == 1:
    content = content.replace(old, new)
    with open(PATH, "w") as f:
        f.write(content)
    print(f"[OK] Fixed X? -> X'?"  )
elif n == 0:
    # Maybe already fixed
    if "X'?" in content:
        print("[skip] Already fixed")
    else:
        print("[FAIL] old text not found")
else:
    print(f"[FAIL] found {n} occurrences, expected 1")