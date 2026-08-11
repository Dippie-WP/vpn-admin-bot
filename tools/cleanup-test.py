#!/usr/bin/env python3
"""Clean up the test customer from the end-to-end test."""
import re
import subprocess

CUSTOMER = "audit-portal-fix-004"
EAP_USER = CUSTOMER + "-primary"

# 1. Delete from DB
sql = f"""DELETE FROM radcheck WHERE username COLLATE utf8mb4_unicode_ci = '{EAP_USER}';
DELETE FROM radusergroup WHERE username COLLATE utf8mb4_unicode_ci = '{EAP_USER}';
DELETE FROM devices WHERE customer_id IN (SELECT id FROM customers WHERE name = '{CUSTOMER}');
DELETE FROM users WHERE name COLLATE utf8mb4_unicode_ci = '{EAP_USER}';
DELETE FROM customers WHERE name = '{CUSTOMER}';
SELECT ROW_COUNT() AS total_deleted;"""
result = subprocess.run(
    ["ssh", "-i", "/root/.ssh/id_ed25519_ci_to_vps", "root@154.65.110.44",
     f"mariadb radius -e \"{sql}\""],
    capture_output=True, text=True
)
print("DB cleanup:", result.stdout)

# 2. Remove EAP block from rw-eap.conf via SSH
eap_cmd = f"""python3 -c '
import re
PATH = "/opt/strongswan-vpn-gateway/docker/swanctl/conf.d/rw-eap.conf"
with open(PATH) as f:
    content = f.read()
new_content = re.sub(r"\\s*eap-{EAP_USER}\\s*\\{{[^}}]*\\}}\\s*\\n?", "\\n", content, flags=re.DOTALL)
removed = len(content) - len(new_content)
with open(PATH, "w") as f:
    f.write(new_content)
print(f"Removed {removed} bytes from rw-eap.conf")
'"""
result = subprocess.run(
    ["ssh", "-i", "/root/.ssh/id_ed25519_ci_to_vps", "root@154.65.110.44", eap_cmd],
    capture_output=True, text=True
)
print("EAP cleanup:", result.stdout)

# 3. Verify cleanup
verify = subprocess.run(
    ["ssh", "-i", "/root/.ssh/id_ed25519_ci_to_vps", "root@154.65.110.44",
     f"mariadb radius -e \"SELECT COUNT(*) AS radcheck_leftover FROM radcheck WHERE username COLLATE utf8mb4_unicode_ci LIKE 'audit-portal-fix%'; SELECT COUNT(*) AS customers_leftover FROM customers WHERE name LIKE 'audit-portal-fix%';\""],
    capture_output=True, text=True
)
print("Verify:", verify.stdout)
print("stderr:", verify.stderr)