#!/usr/bin/env python3
"""Fix schedule_delete() to handle None job_queue gracefully.

Bug J: context.job_queue is None because the Application was built without
a JobQueue. The schedule_delete function crashes with AttributeError when
trying to call context.job_queue.run_once().

Fix: check if job_queue is None and skip (no-op) if so. The auto-delete
becomes a no-op when the job_queue isn't configured, but the customer
creation still succeeds (the schedule_delete is called AFTER the with-block
commits).
"""
PATH = "/opt/vpn-portal/bot.py"

with open(PATH) as f:
    content = f.read()

# Find the schedule_delete function
old = '''def schedule_delete(context, chat_id: int, message_id: int, delay: int = 60):
    """Schedule auto-delete of a message (default 60s)."""
    context.job_queue.run_once(
        _auto_delete_job, delay, chat_id=chat_id, data=message_id
    )'''

new = '''def schedule_delete(context, chat_id: int, message_id: int, delay: int = 60):
    """Schedule auto-delete of a message (default 60s).

    No-op if the Application was built without a JobQueue (job_queue is None).
    The customer creation still succeeds — this just means the success/error
    message won't be auto-deleted after 60s.
    """
    if context.job_queue is None:
        return
    context.job_queue.run_once(
        _auto_delete_job, delay, chat_id=chat_id, data=message_id
    )'''

n = content.count(old)
if n == 1:
    content = content.replace(old, new)
    with open(PATH, "w") as f:
        f.write(content)
    print("[1x]  Fixed schedule_delete() to handle None job_queue")
else:
    print(f"[FAIL] old text not found uniquely (count={n})")