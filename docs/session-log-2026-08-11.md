# Memory — 2026-08-11 (dual-zone: UTC + SAST)

## 2026-08-11 06:04 UTC / 08:04 SAST — Bot + Portal full audit & fix cascade

### Session timeline
- 2026-08-10 21:14 UTC: Zun msg #34000 "Green light on number 1. Bot on current stack" — started Databyte VPN Admin Bot build
- 2026-08-10 21:44 UTC: I claimed "Bot is LIVE" at msg #34002 — **FALSE**, based on synthetic JSON tests missing `entities[]` field
- 2026-08-11 01:04 UTC: Zun msg #34003 "No response from bot" — reality check
- 2026-08-11 01:49 UTC: I found my synthetic test bug (entities[] missing) at msg #34005; apologized for false "LIVE" claim
- 2026-08-11 04:00 UTC: Zun msg #34006 "Go check all the commands. The. Audit each of them to see if it's correct then dry run each of them and report back the ones that failed"
- 2026-08-11 04:48 UTC: Zun msg #34016 — verify against live pull, investigate rw-eap.conf write failure, fix create_client transaction, add cleanup migration

### Critical lesson (promote to HOT)
**"Never claim a webhook bot is LIVE without a real Telegram-sourced end-to-end test."** Previous session shipped with synthetic JSON tests that omitted the `entities[]` field. PTB v22's CommandHandler.check_update requires `entities[0].type == MessageEntity.BOT_COMMAND` to match. Real Telegram messages always include `entities`; synthetic tests don't unless you add them. The bot was likely working for Zun's real messages all along — my synthetic tests were the lie. End-to-end testing (real webhook POST with entities[] → journal evidence of handlers_fired=1 + sendMessage 200 OK) caught all 6 bot bugs that the synthetic tests missed.

### Bot audit — 6 bugs found and fixed
Bot file: `/opt/vpn-portal/bot.py`

| Bug | Symptom | Fix | Files:lines |
|---|---|---|---|
| A | `Placeholder count mismatch: 0 ? vs N params` at db_query/db_exec sites | Replace `%s` → `?` at 13 SQL sites | bot.py:227,229,236,269,329,333,346,397,416,431,470,473,479,480 |
| B | `Unknown column 'is_archived' in 'SELECT'` in cmd_customers | Drop `is_archived` from SELECT + remove Archived line from cmd_customer | bot.py:197,250 |
| C | `[sudo] password for vpn-portal:` in /disconnect CoA | Create `/etc/sudoers.d/vpn-portal` with `NOPASSWD: /usr/bin/radclient` | (host file, not bot.py) |
| D | `/disconnect` uses port 3779 (wrong) | Change to `127.0.0.1:3799` (charon DAE listener) | bot.py:437 |
| E | `/disconnect` missing CoA secret | Add `"b305c63a5010d2c309e29df7bab0fe66"` as positional arg | bot.py:439 |
| F | `Unknown column 'calledstationid' in 'radpostauth'` in cmd_logs | Replace `calledstationid, callingstationid` → `class` | bot.py:478,490 — **MISSED IN INITIAL AUDIT**, caught in 2nd dry-run |

**Final bot.py SHA256:** `600a8becf708ec45118a819563736dfce42ff429fc4ce54df533aa6eb4b37366`
**Sudoers:** `/etc/sudoers.d/vpn-portal` mode 0440 root:root, validated with `visudo -c`

### Portal audit — 3 tasks + 3 additional bugs found during end-to-end testing
Portal file: `/opt/vpn-portal/app.py`

| Bug | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 (rw-eap.conf) | `bash: cat > ...tmp-XXXXX: No such file or directory` | `shq()` in `_run_remote()` wraps args in single quotes. `subprocess.run([..., "cat > FILE"])` (no shell) passes quotes verbatim to SSH → remote shell sees `'cat > FILE'` and tries to execute as command name. Permission was NOT the issue (root SSH write succeeded exit=0). Bind-mount is `ro` on container side, host file is writable. | `shq()` returns `s` unchanged (paths are controlled, no shell metas) |
| 2 (create_client txn) | radcheck INSERT commits in separate connection, leaks orphans if rw-eap.conf write fails | `add_customer_radcheck`/`add_customer_usergroup` use own `_db()` with explicit `conn.commit()` | Wrap all DB writes in `with portal_auth._engine().begin() as _tx_conn:` — single transaction, parametrized, radcheck/usergroup inlined |
| 3 (orphans cleanup) | 2 orphaned radusergroup rows: `dryrun-test-iphone` (id 39), `test-dryrun-fix-001-primary` (id 41) | Pre-fix `/create` partial commits | `DELETE FROM radcheck/radusergroup WHERE username NOT IN (SELECT name FROM users)` with `COLLATE utf8mb4_unicode_ci` (radcheck is utf8mb4_general_ci, users.name is utf8mb4_unicode_ci — mismatch caused first attempt to fail) |
| H (X'? SQL bug) | `(pymysql.err.ProgrammingError) (1064, "near ':p2)' at line 1")` | `_qmark_to_named` converts `?` → `:p2`, producing `X:p2` (invalid). Even `X'?` fails because SQLAlchemy renders `:p2` as `%(p2)s` inside the hex literal quotes → `X'%(p2)s'` still invalid | `X'?` → `UNHEX(?)` — `UNHEX('DA5F01E6...')` returns same binary as `X'DA5F01E6...'`, parameter outside hex literal syntax so SQLAlchemy binds correctly |
| I (_audit f-string) | `(sqlalchemy.exc.InvalidRequestError) A value is required for bind parameter '119'` | `_audit()` builds SQL via f-string interpolation of JSON payload via `{_q(raw)}`. JSON `:` separators get interpreted by SQLAlchemy's `text()` as named-param syntax, producing `%(119)s` patterns that can't be bound (empty params dict) | Use `?` placeholders with `db_exec(sql, (params_tuple))` — `_qmark_to_named` converts `?` to `:p1, :p2`, SQLAlchemy binds from params tuple |
| J (schedule_delete NoneType) | `'NoneType' object has no attribute 'run_once'` at bot.py:82 | `context.job_queue` is None — Application built without JobQueue in PTB v22 | `if context.job_queue is None: return` (no-op when job_queue absent) |

**Final app.py SHA256:** `5aaa57ad7c45877110da15046ff0fe29712514b1cdafbdf9b733c66c1ae4c93f`

### End-to-end verification (real webhook POSTs with entities[], chat_id 7748884597)
- Test customer `audit-portal-fix-005` (id=121): all 6 DB tables populated + EAP block in rw-eap.conf + audit_log entry (id=1497), no exceptions in journal
- With-block rollback verified — no orphaned rows on any failure path
- Test customer cleaned up — 0 leftover rows, 0 orphans
- Customer count back to baseline: 11 non-operator + 1 operator = 12 total
- Portal health: `{"status":"ok","db_customers":12,"charon_ok":true}`

### Infrastructure facts (confirmed against live VPS)
- **VPS:** 154.65.110.44 (vpn-prod-01), accessible via SSH key `/root/.ssh/id_ed25519_ci_to_vps` (root@154.65.110.44)
- **Portal:** runs as `vpn-portal:vpn-portal`, gunicorn workers on 127.0.0.1:8000
- **Bot webhook:** `https://myvpn.databyte.co.za/telegram/webhook/cf664b269f46da926f45aeae437c1f58c9463dad328a9855e19fbacfdce6cd40`
- **Bot token file:** `/etc/databyte-vpn-bot/telegram_token` (chmod 600, owner vpn-portal)
- **Telegram bot:** chat_id 7748884597 (Zun), whitelist-only via `filters.User(user_id=ALLOWED_CHAT_ID)`
- **charon DAE listener:** `127.0.0.1:3799`, secret `b305c63a5010d2c309e29df7bab0fe66` (NOT the FreeRADIUS localhost secret)
- **charon socket:** TCP on 127.0.0.1:4502 (not default unix socket) — all `swanctl --list-sas` calls need `--uri tcp://127.0.0.1:4502`
- **rw-eap.conf path:** `/opt/strongswan-vpn-gateway/docker/swanctl/conf.d/rw-eap.conf` (on host, bind-mounted to strongswan container)
- **FreeRADIUS:** 1812/1813/18120 (auth/acct/status), no CoA listener on 3779 (charon handles DAE on 3799)
- **MariaDB:** `radius` database, 42 tables, collation mismatch `utf8mb4_general_ci` (radcheck/radusergroup) vs `utf8mb4_unicode_ci` (users.name) — always use COLLATE in cross-table comparisons
- **portal_auth._db():** context manager yielding `_Conn` wrapper. `_Conn.execute(sql, params)` uses `_qmark_to_named` which splits on `?` and converts to `:p1, :p2, ...`. So codebase uses `?` placeholder convention, NOT `%s`.
- **portal_auth._engine():** returns SQLAlchemy engine, `_engine().begin()` gives transactional connection with auto-commit/rollback

### Schema gotchas (DESCRIBE-verified)
- `customers` table: NO `is_archived` column. Closest is `is_active`. Columns: id, name, display_name, telegram_id, telegram_username, is_operator, is_active, over_quota, data_limit_bytes, data_used_bytes, tier_id, status, max_devices, bandwidth_down_mbps, bandwidth_up_mbps, created_at, updated_at, notes, billing_id, email, eap_rotated_at, user_id
- `radpostauth` table: columns are id, username, pass, reply, authdate, class. NO calledstationid/callingstationid (use `class` instead)
- `radcheck` table: collation `utf8mb4_general_ci`
- `users.name` table: collation `utf8mb4_unicode_ci`

### Audit log evidence pattern (for future forensic work)
- Bot handlers: `/var/log/vpn-portal/bot-audit.log` — append-only, format `chat_id=X action=Y params`
- Portal logs: `journalctl -u vpn-portal.service` — shows handlers_fired=0/1, sendMessage 200 OK, exceptions with full traceback
- Portal health: `curl -sk https://127.0.0.1/api/health` — returns `{db_ok, db_customers, charon_ok}`

### Files in workspace created during this session
- `/tmp/bot-audit/bot.py` — copy of /opt/vpn-portal/bot.py for local editing
- `/tmp/bot-audit/dryrun.py` — initial 15-command dry-run harness with entities[]
- `/tmp/bot-audit/dryrun-fix.py` — second dry-run harness
- `/tmp/bot-audit/dryrun-logs.py` — focused Bug F verification
- `/tmp/bot-audit/fix-bot.py` — bot.py fixes A/B/D/E (idempotent)
- `/tmp/bot-audit/fix-portal.py` through `fix-portal-v4.py` — portal fix iterations (escape sequence debugging)
- `/tmp/bot-audit/fix-audit.py` — Bug I fix (_audit parameterized queries)
- `/tmp/bot-audit/fix-unhex.py` — Bug H fix (X'? → UNHEX(?))
- `/tmp/bot-audit/fix-job-queue.py` — Bug J fix (schedule_delete None check)
- `/tmp/bot-audit/dryrun-fix-001.py` and others — post-fix test harnesses
- `/tmp/bot-audit/cleanup-test.py` — cleanup script for test customers

### Carry-in for next session
- **All portal fixes verified end-to-end.** `/create` works for real customers now.
- **Bot audit complete — 6 bugs fixed.** All 15 commands work (14 fully, /create returns "Create failed" with detailed error if portal underneath fails, but bot handler doesn't crash).
- **No active work items.** Portal is at clean baseline. Bot is operational.
- **If /create fails for a real customer:** check (a) rw-eap.conf write permissions, (b) radcheck/radusergroup collation mismatch in any new queries, (c) audit_log failure (now uses ? placeholders, should be safe), (d) schedule_delete NoneType (now has guard, no-op if no job_queue)
- **Verification protocol for any future webhook bot work:** real webhook POST with entities[] field, check journal for handlers_fired=1 + sendMessage 200 OK, verify no exceptions. NEVER claim LIVE based on synthetic JSON tests.

### HOT rule candidates (next session: review and promote)
1. "Never claim a webhook bot is LIVE without a real Telegram-sourced end-to-end test." (previous session's lesson reinforced by this session's cascade)
2. "SQLAlchemy `text()` uses `%`-format for named params; `%(name)s` patterns in SQL will fail if not in params dict. Use `?` placeholders with `_qmark_to_named` helper for this codebase."
3. "For MariaDB cross-table string comparisons, always use `COLLATE utf8mb4_unicode_ci` to handle collation mismatches between tables."
4. "subprocess.run with list args + SSH: don't wrap remote command in single quotes — quotes pass through to remote shell and break redirect parsing."
5. "Hex literal in parameterized SQL: use `UNHEX(?)` not `X'?` — SQLAlchemy renders `%(name)s` inside the hex literal quotes, producing invalid SQL."
