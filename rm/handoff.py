"""Posting by hand, for when there is no Reddit API access.

The pipeline is unchanged up to the point of posting: research, draft, style checks,
and your approval in Discord. Only the last step differs. Instead of the worker calling
the API, it hands you one approved item at a time: the draft goes on your clipboard and
the thread opens in your browser. You paste it, read it once more in context, and hit
reply yourself. Then you tell it what happened.

This keeps every pacing rule meaningful, because the counters only advance when you
confirm you actually posted.
"""
import subprocess, sys, time, webbrowser

from . import store, poster


def to_clipboard(text):
    """Best effort per platform. Returns True if the text is on the clipboard."""
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text, text=True, encoding="utf-8", check=True, shell=True)
        elif sys.platform == "darwin":
            subprocess.run("pbcopy", input=text, text=True, check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True)
        return True
    except Exception as e:
        print(f"[handoff] clipboard unavailable ({e}). Copy the text above by hand.")
        return False


def pending_count():
    with store.conn() as c:
        return c.execute("SELECT COUNT(*) FROM queue WHERE status='approved'").fetchone()[0]


def next_item(cfg):
    """The next approved item, if pacing allows it. Returns (item, reason)."""
    item = store.next_approved()
    if not item:
        return None, "nothing approved"
    ok, why = poster.can_post_now(cfg, item)
    if not ok:
        return None, why
    return item, "ok"


def hand_off(item, open_browser=True):
    """Show the draft, put it on the clipboard, open the thread."""
    kind = item["kind"]
    print("\n" + "=" * 70)
    print(f"#{item['id']}  {kind}  r/{item['sub']}")
    if kind == "comment":
        print(f"thread: {item['thread_title']}")
        print(f"url   : {item['thread_url']}")
    else:
        print(f"title : {item['title']}")
        print(f"post to: https://www.reddit.com/r/{item['sub']}/submit")
    print("-" * 70)
    print(item["body"])
    print("=" * 70)

    payload = item["body"] if kind == "comment" else f"{item['title']}\n\n{item['body']}"
    if to_clipboard(payload):
        print("[handoff] draft copied to clipboard")
    if open_browser:
        url = item["thread_url"] if kind == "comment" else f"https://www.reddit.com/r/{item['sub']}/submit"
        try:
            webbrowser.open(url)
            print(f"[handoff] opened {url}")
        except Exception as e:
            print(f"[handoff] could not open browser ({e}), go to {url}")


def confirm(item):
    """Record what actually happened. Only a yes advances the pacing counters."""
    print("\nDid you post it?  [y] yes  [n] no, put it back  [s] skip it for good")
    while True:
        a = input("> ").strip().lower()[:1]
        if a == "y":
            url = input("paste the comment url (or press Enter to skip): ").strip()
            store.mark_posted(item["id"], url or item["thread_url"], True)
            print(f"recorded. {store.counts_today()[0]} comments in the last 24h.")
            return "posted"
        if a == "n":
            print("left as approved, it will come up again next time.")
            return "deferred"
        if a == "s":
            store.decide(item["id"], "rejected")
            print("marked rejected, it will not come back.")
            return "skipped"
        print("y, n or s")


def run_once(cfg, open_browser=True):
    item, why = next_item(cfg)
    if not item:
        print(f"[handoff] {why}" + (f" ({pending_count()} approved and waiting)" if pending_count() else ""))
        return False
    hand_off(item, open_browser)
    confirm(item)
    return True


def run_all(cfg, open_browser=True):
    """Work through the approved queue, respecting pacing between items."""
    n = pending_count()
    if not n:
        print("[handoff] nothing approved yet. Approve drafts in Discord first.")
        return
    print(f"[handoff] {n} approved item(s) waiting.")
    while True:
        item, why = next_item(cfg)
        if not item:
            print(f"[handoff] stopping: {why}")
            return
        hand_off(item, open_browser)
        if confirm(item) == "deferred":
            return
        if not pending_count():
            print("[handoff] queue empty. Done.")
            return
        print("\n[handoff] next item is subject to the pacing gap. Re-run when you are ready.")
        return
