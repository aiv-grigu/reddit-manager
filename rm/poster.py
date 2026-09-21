"""Posts items the owner has explicitly approved, one at a time, within the pacing limits in config.yaml.
Nothing leaves the queue without an approval, and the limits here are not to be loosened."""
import time, random, datetime
from . import store, research

def _in_active_hours(cfg):
    h = datetime.datetime.utcnow().hour
    lo, hi = cfg["pacing"]["active_hours_utc"]
    return lo <= h < hi

def can_post_now(cfg, item):
    p = cfg["pacing"]
    total, per_sub, last, last_post = store.counts_today()
    if not _in_active_hours(cfg): return False, "outside active hours"
    if item["kind"] == "comment":
        if total >= p["max_comments_per_day"]: return False, "daily comment cap"
        if per_sub.get(item["sub"], 0) >= p["max_comments_per_sub_per_day"]: return False, f"cap for r/{item['sub']}"
    else:
        if time.time() - last_post < p["days_between_posts"] * 86400: return False, "post spacing"
    gap = p["min_minutes_between_comments"] * 60 + random.uniform(0, p["jitter_minutes"] * 60)
    if time.time() - last < gap: return False, f"pacing gap ({int((gap - (time.time() - last)) / 60)} min left)"
    return True, "ok"

def post_item(reddit, item):
    if item["kind"] == "comment":
        sub = reddit.submission(id=item["thread_id"])
        if getattr(sub, "locked", False) or getattr(sub, "archived", False):
            raise RuntimeError("thread locked or archived")
        c = sub.reply(item["body"])
        return "https://www.reddit.com" + c.permalink
    else:
        s = reddit.subreddit(item["sub"]).submit(title=item["title"], selftext=item["body"])
        return "https://www.reddit.com" + s.permalink

def worker_tick(reddit, cfg, env, tg):
    """One pass: post at most one approved item if pacing allows."""
    item = store.next_approved()
    if not item: return "nothing approved"
    ok, why = can_post_now(cfg, item)
    if not ok: return f"waiting: {why}"
    # human-like pause before acting, 20s to 3min
    time.sleep(random.uniform(20, 180))
    try:
        url = post_item(reddit, item)
        store.mark_posted(item["id"], url, True)
        tg.notify(env, f"Posted #{item['id']} in r/{item['sub']}\n{url}")
        return f"posted {url}"
    except Exception as e:
        store.mark_posted(item["id"], str(e), False)
        tg.notify(env, f"FAILED #{item['id']} in r/{item['sub']}: {e}\nIf this is a rate limit, captcha or account notice, STOP the worker and check the account manually.")
        return f"failed {e}"
