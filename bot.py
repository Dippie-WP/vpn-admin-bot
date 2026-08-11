"""Databyte VPN Admin Bot — Telegram interface for the VPN portal.

Lives inside the FastAPI app process (no separate service). Whitelist-only:
chat_id 7748884597 (Zun). Commands call internal portal helpers directly.

Commands:
  /start /help
  /status - service health, 24h auth count, live session count
  /stats - bandwidth today/7d/30d from radacct
  /sessions - live VPN sessions
  /customers - all customers
  /customer <id|name> - drill-down
  /create <name> [display] - create customer, return creds (auto-delete 60s)
  /creds <id|name> - fetch current creds from rw-eap.conf (auto-delete 60s)
  /disable <id|name> - set is_active=0
  /enable <id|name> - set is_active=1
  /disconnect <name> - CoA kick via radclient 127.0.0.1:3779
  /logs <name> [n=20] - last N radpostauth entries
"""
import logging
import os
import re
import subprocess
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Secrets loaded from file (chmod 600, owner vpn-portal:vpn-portal).
# Never logged, never echoed to chat.
TELEGRAM_TOKEN = open("/etc/databyte-vpn-bot/telegram_token").read().strip()
WEBHOOK_SECRET = open("/etc/databyte-vpn-bot/webhook_secret").read().strip()

AUDIT_LOG = "/var/log/vpn-portal/bot-audit.log"
ALLOWED_CHAT_ID = 7748884597  # Zun
RW_EAP_CONF = os.environ.get(
    "RW_EAP_CONF",
    "/opt/strongswan-vpn-gateway/docker/swanctl/conf.d/rw-eap.conf",
)

logger = logging.getLogger("vpn-bot")


def audit(action: str, **details):
    """Append-only audit log. No secrets."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = " ".join(f"{k}={v}" for k, v in details.items())
    line = f"{ts} chat_id={ALLOWED_CHAT_ID} action={action} {parts}\n"
    try:
        with open(AUDIT_LOG, "a") as f:
            f.write(line)
    except Exception as e:
        logger.error(f"audit log write failed: {e}")


async def reject_non_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silently reject anything from non-whitelisted chat_ids."""
    chat = update.effective_chat
    if chat and chat.id != ALLOWED_CHAT_ID:
        u = update.effective_user
        uname = u.username if u else None
        logger.warning(f"REJECT chat_id={chat.id} user={uname}")


async def _auto_delete_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception as e:
        logger.warning(f"auto-delete failed msg={job.data}: {e}")


def schedule_delete(context, chat_id: int, message_id: int, delay: int = 60):
    """Schedule auto-delete of a message (default 60s).

    No-op if the Application was built without a JobQueue (job_queue is None).
    The customer creation still succeeds — this just means the success/error
    message won't be auto-deleted after 60s.
    """
    if context.job_queue is None:
        return
    context.job_queue.run_once(
        _auto_delete_job, delay, chat_id=chat_id, data=message_id
    )


# ---------- Commands ----------

async def cmd_start(update, context):
    audit("start")
    logger.info("cmd_start fired for chat_id=%s", update.effective_chat.id)
    await update.message.reply_text(
        "Databyte VPN Admin Bot\n\n"
        "/help - list commands\n"
        "/status - health + activity\n"
        "/stats - bandwidth today/7d/30d\n"
        "/sessions - live VPN sessions\n"
        "/customers - all customers\n"
        "/customer <id|name> - drill-down\n"
        "/create <name> [display] - new customer\n"
        "/creds <id|name> - fetch creds (auto-del 60s)\n"
        "/disable /enable <id|name>\n"
        "/disconnect <name> - CoA kick\n"
        "/logs <name> [n]"
    )


async def cmd_help(update, context):
    audit("help")
    await update.message.reply_text(
        "/status /stats /sessions /customers\n"
        "/customer <id|name> /create <name> [display]\n"
        "/creds <id|name> /disable /enable <id|name>\n"
        "/disconnect <name> /logs <name> [n=20]"
    )


async def cmd_status(update, context):
    audit("status")
    import app
    health = app.health()
    auth24 = app.db_query(
        "SELECT COUNT(*) AS n FROM radpostauth "
        "WHERE authdate >= NOW() - INTERVAL 1 DAY"
    )
    n24 = auth24[0]["n"] if auth24 else 0
    sessions = app.swanctl_parse_sas()
    live = sum(1 for s in sessions if s.get("state") == "ESTABLISHED")
    text = (
        "*Service Health*\n"
        f"DB: `{health.get('db_ok')}` customers: `{health.get('db_customers')}`\n"
        f"charon: `{health.get('charon_ok')}`\n\n"
        "*Activity*\n"
        f"Auth (24h): `{n24}`\n"
        f"Live sessions: `{live}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update, context):
    audit("stats")
    import app

    def fmt(b):
        if not b:
            return "0 B"
        b = int(b)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    def row(interval):
        r = app.db_query(
            "SELECT COALESCE(SUM(acctinputoctets),0) AS inb, "
            "COALESCE(SUM(acctoutputoctets),0) AS outb, COUNT(*) AS n "
            f"FROM radacct WHERE acctstarttime >= NOW() - INTERVAL {interval}"
        )
        return r[0] if r else {"inb": 0, "outb": 0, "n": 0}

    r1, r7, r30 = row("1 DAY"), row("7 DAY"), row("30 DAY")
    text = (
        "*Bandwidth*\n"
        f"Today: {fmt(r1['inb'])} in / {fmt(r1['outb'])} out ({r1['n']} sessions)\n"
        f"7d:    {fmt(r7['inb'])} in / {fmt(r7['outb'])} out ({r7['n']} sessions)\n"
        f"30d:   {fmt(r30['inb'])} in / {fmt(r30['outb'])} out ({r30['n']} sessions)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_sessions(update, context):
    audit("sessions")
    import app
    sessions = app.swanctl_parse_sas()
    if not sessions:
        await update.message.reply_text("No active sessions.")
        return
    lines = ["*Active Sessions*"]
    for s in sessions[:25]:
        rid = s.get("remote_id") or "?"
        rip = s.get("remote_ip") or "?"
        vip = s.get("vip") or "?"
        st = s.get("state") or "?"
        bi = s.get("bytes_in", 0)
        bo = s.get("bytes_out", 0)
        lines.append(
            f"- `{rid}` `{rip}` -> `{vip}` `{st}` (in:{bi:,} out:{bo:,})"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_customers(update, context):
    audit("customers")
    import app
    rows = app.db_query(
        "SELECT id, name, display_name, is_active "
        "FROM customers WHERE COALESCE(is_operator, 0) = 0 ORDER BY id"
    )
    if not rows:
        await update.message.reply_text("No customers.")
        return
    lines = ["*Customers*"]
    for c in rows:
        archived = bool(c.get("is_archived"))
        active = bool(c.get("is_active"))
        if archived:
            flag = "Y"  # yellow archived
        elif active:
            flag = "G"  # green active
        else:
            flag = "R"  # red disabled
        lines.append(
            f"[{flag}] `{c['id']}` `{c['name']}` - {c.get('display_name') or ''}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_customer(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /customer <id|name>")
        return
    arg = context.args[0]
    audit("customer", arg=arg)
    import app
    if arg.isdigit():
        rows = app.db_query("SELECT * FROM customers WHERE id = ?", (int(arg),))
    else:
        rows = app.db_query("SELECT * FROM customers WHERE name = ?", (arg,))
    if not rows:
        await update.message.reply_text(f"No customer `{arg}`.")
        return
    c = rows[0]
    devs = app.db_query(
        "SELECT id, device_name, device_type, is_active "
        "FROM devices WHERE customer_id = ?",
        (c["id"],),
    )
    dev_lines = []
    for d in devs:
        flag = "G" if d.get("is_active") else "R"
        dev_lines.append(
            f"  [{flag}] `{d['id']}` `{d.get('device_name')}` ({d.get('device_type')})"
        )
    text = (
        f"*Customer #{c['id']}*\n"
        f"Slug: `{c['name']}`\n"
        f"Display: {c.get('display_name') or '-'}\n"
        f"Active: {bool(c.get('is_active'))} "
        f"Tier: {c.get('tier_id')} Created: {c.get('created_at')}\n"
        f"Devices:\n" + ("\n".join(dev_lines) if dev_lines else "  (none)")
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_create(update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage: /create <name> [display_name]\n"
            'Example: /create zun-acme "Acme Corp"'
        )
        return
    name = context.args[0].lower()
    display = context.args[1] if len(context.args) > 1 else name
    audit("create", name=name)
    import app

    if app.db_query("SELECT id FROM customers WHERE name = ?", (name,)):
        await update.message.reply_text(
            f"Customer `{name}` already exists. Use /creds to fetch."
        )
        return

    tiers = app.db_query(
        "SELECT name FROM tiers WHERE is_active = 1 "
        "ORDER BY data_limit_bytes ASC LIMIT 1"
    )
    if not tiers:
        await update.message.reply_text(
            "No active tier configured. Cannot create customer."
        )
        return
    tier_name = tiers[0]["name"]

    from app import ClientCreate

    req = ClientCreate(
        name=name,
        display_name=display,
        tier_name=tier_name,
        device_name="primary",
        device_type="Other",
    )
    try:
        result = app.create_client(
            req, _user={"name": "bot", "role": "operator"}
        )
    except Exception as e:
        await update.message.reply_text(f"Create failed: {e}")
        return

    cust = result.get("customer", {})
    eap_id = result.get("eap_identity")
    password = result.get("password")
    text = (
        f"OK Customer `{name}` created (id=`{cust.get('id')}`)\n"
        f"Tier: `{tier_name}`\n\n"
        f"! *Self-destructs in 60s*\n\n"
        f"Server: `myvpn.databyte.co.za`\n"
        f"EAP identity: `{eap_id}`\n"
        f"Password: `{password}`\n"
        f"CA cert: https://myvpn.databyte.co.za/certs/strongswan-ca.crt.pem"
    )
    msg = await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    schedule_delete(context, update.effective_chat.id, msg.message_id, 60)


async def cmd_creds(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /creds <id|name>")
        return
    arg = context.args[0]
    audit("creds", arg=arg)
    import app

    if arg.isdigit():
        rows = app.db_query(
            "SELECT id, name, user_id FROM customers WHERE id = ?", (int(arg),)
        )
    else:
        rows = app.db_query(
            "SELECT id, name, user_id FROM customers WHERE name = ?", (arg,)
        )
    if not rows:
        await update.message.reply_text(f"No customer `{arg}`.")
        return

    customer_id = rows[0]["id"]
    user_id = rows[0].get("user_id")
    if not user_id:
        await update.message.reply_text(
            f"Customer `{rows[0]['name']}` has no EAP user linked."
        )
        return
    u = app.db_query("SELECT name FROM users WHERE id = ?", (user_id,))
    if not u:
        await update.message.reply_text(f"users row missing for id={user_id}.")
        return
    eap_identity = u[0]["name"]

    try:
        conf = open(RW_EAP_CONF).read()
    except Exception as e:
        await update.message.reply_text(f"Cannot read {RW_EAP_CONF}: {e}")
        return

    block_id = f"eap-{eap_identity}"
    block_pat = re.compile(
        rf"^\s*{re.escape(block_id)}\s*\{{[^}}]*?secret\s*=\s*\"([^\"]+)\"",
        re.MULTILINE | re.DOTALL,
    )
    m = block_pat.search(conf)
    if not m:
        await update.message.reply_text(
            f"No EAP block `{block_id}` in rw-eap.conf."
        )
        return
    password = m.group(1)

    text = (
        f"Creds for `{rows[0]['name']}` (id=`{customer_id}`)\n\n"
        f"! *Self-destructs in 60s*\n\n"
        f"Server: `myvpn.databyte.co.za`\n"
        f"EAP identity: `{eap_identity}`\n"
        f"Password: `{password}`"
    )
    msg = await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    schedule_delete(context, update.effective_chat.id, msg.message_id, 60)


async def cmd_disable(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /disable <id|name>")
        return
    arg = context.args[0]
    audit("disable", arg=arg)
    import app
    if arg.isdigit():
        cid = int(arg)
    else:
        rows = app.db_query("SELECT id FROM customers WHERE name = ?", (arg,))
        if not rows:
            await update.message.reply_text(f"No customer `{arg}`.")
            return
        cid = rows[0]["id"]
    app.db_exec("UPDATE customers SET is_active = 0 WHERE id = ?", (cid,))
    await update.message.reply_text(f"Disabled customer id=`{cid}`.")


async def cmd_enable(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /enable <id|name>")
        return
    arg = context.args[0]
    audit("enable", arg=arg)
    import app
    if arg.isdigit():
        cid = int(arg)
    else:
        rows = app.db_query("SELECT id FROM customers WHERE name = ?", (arg,))
        if not rows:
            await update.message.reply_text(f"No customer `{arg}`.")
            return
        cid = rows[0]["id"]
    app.db_exec("UPDATE customers SET is_active = 1 WHERE id = ?", (cid,))
    await update.message.reply_text(f"Enabled customer id=`{cid}`.")


async def cmd_disconnect(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /disconnect <name>")
        return
    arg = context.args[0]
    audit("disconnect", arg=arg)
    import app

    rows = app.db_query(
        "SELECT u.name AS eap_identity FROM customers c "
        "JOIN users u ON c.user_id = u.id "
        "WHERE c.id = ? OR c.name = ? LIMIT 1",
        (int(arg) if arg.isdigit() else 0, arg),
    )
    if not rows:
        await update.message.reply_text(f"No customer matches `{arg}`.")
        return
    eap_identity = rows[0]["eap_identity"]

    cmd = [
        "sudo", "radclient", "127.0.0.1:3799", "coa",
        "b305c63a5010d2c309e29df7bab0fe66",
        f"User-Name={eap_identity}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            await update.message.reply_text(
                f"CoA disconnect sent for `{eap_identity}`."
            )
        else:
            await update.message.reply_text(
                f"Disconnect failed:\n`{(res.stderr or res.stdout).strip()[:500]}`"
            )
    except subprocess.TimeoutExpired:
        await update.message.reply_text("Disconnect timed out after 10s.")
    except Exception as e:
        await update.message.reply_text(f"Disconnect error: {e}")


async def cmd_logs(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /logs <name> [n=20]")
        return
    name = context.args[0]
    n = 20
    if len(context.args) > 1 and context.args[1].isdigit():
        n = int(context.args[1])
    audit("logs", name=name, n=n)
    import app

    rows = app.db_query("SELECT user_id FROM customers WHERE name = ?", (name,))
    eap_id = name
    if rows and rows[0].get("user_id"):
        u = app.db_query("SELECT name FROM users WHERE id = ?", (rows[0]["user_id"],))
        if u:
            eap_id = u[0]["name"]

    auths = app.db_query(
        "SELECT authdate, reply, class "
        "FROM radpostauth WHERE username = ? "
        "ORDER BY authdate DESC LIMIT ?",
        (eap_id, n),
    )
    if not auths:
        await update.message.reply_text(f"No auth events for `{eap_id}`.")
        return
    lines = [f"*Last {len(auths)} auth events for `{eap_id}`*"]
    for r in auths:
        lines.append(
            f"`{r['authdate']}` {r['reply']} "
            f"class=`{r.get('class') or '-'}`"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------- Build Application ----------

def build_application() -> Application:
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    whitelist = filters.User(user_id=ALLOWED_CHAT_ID)
    application.add_handler(MessageHandler(~whitelist, reject_non_admin))

    commands = [
        ("start", cmd_start),
        ("help", cmd_help),
        ("status", cmd_status),
        ("stats", cmd_stats),
        ("sessions", cmd_sessions),
        ("customers", cmd_customers),
        ("customer", cmd_customer),
        ("create", cmd_create),
        ("creds", cmd_creds),
        ("disable", cmd_disable),
        ("enable", cmd_enable),
        ("disconnect", cmd_disconnect),
        ("logs", cmd_logs),
    ]
    for cmd, handler in commands:
        application.add_handler(CommandHandler(cmd, handler, filters=whitelist))

    return application