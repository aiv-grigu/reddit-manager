#!/usr/bin/env python3
"""Reddit Manager.

  python run.py morning   # research + draft, send drafts to Discord for approval
  python run.py worker    # long-running: handles approvals (api mode also posts, with pacing)
  python run.py handoff   # rss mode: hand you one approved draft to paste yourself
  python run.py status    # queue, today's counts, account state where available
  python run.py dry       # research + draft, print to terminal, send nothing, post nothing

Two modes, set by `mode:` in config.yaml.
  api  needs Reddit Data API approval (Responsible Builder Policy) and posts for you
       once you approve each draft.
  rss  no Reddit API at all. Research comes from public RSS listing feeds, and you
       paste each approved draft into your browser yourself via `handoff`.
"""
import os, sys, time, random, datetime, pathlib
import yaml
from dotenv import load_dotenv
from rm import brain, store, poster, handoff
from rm import discord_approve, telegram_approve

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
ENV = {k: os.environ.get(k, "") for k in ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD",
                                          "REDDIT_USER_AGENT", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                                          "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID",
                                          "ZERNIO_API_KEY", "ZERNIO_ACCOUNT_ID"]}
# approval channel: discord if configured, else telegram
tg = discord_approve if ENV["DISCORD_BOT_TOKEN"] else telegram_approve
CFG = yaml.safe_load(open(ROOT / "config.yaml"))
MODE = CFG.get("mode", "rss")
_voice_path = ROOT / "voice.md" if (ROOT / "voice.md").exists() else ROOT / "voice.example.md"
if _voice_path.name == "voice.example.md": print("[voice] voice.md not found, using voice.example.md. Copy it to voice.md and add your own writing samples.")
VOICE = open(_voice_path, encoding="utf-8").read()

# research backend: praw (api), Zernio's endpoints (zernio), or public RSS feeds (rss)
if MODE == "api":
    from rm import research
elif MODE == "zernio":
    from rm import research_zernio
else:
    from rm import research_rss

REDDIT_USER = ENV["REDDIT_USERNAME"] or CFG.get("reddit_username", "")


def _client():
    if MODE == "api":
        return research.reddit_client(ENV)
    if MODE == "zernio":
        return research_zernio.client(ENV, CFG)
    return research_rss.RssClient(CFG, REDDIT_USER)


def _account(client):
    if MODE == "api":
        return research.account_status(client)
    if MODE == "zernio":
        return research_zernio.account_status(client, REDDIT_USER)
    return research_rss.account_status(client, REDDIT_USER)


def _threads(client):
    if MODE == "api":
        return research.find_threads(client, CFG)
    if MODE == "zernio":
        return research_zernio.find_threads(client, CFG)
    return research_rss.find_threads(client, CFG)


def _news():
    if MODE == "api":
        return research.news(CFG)
    from rm import research as _r          # news feeds are ordinary RSS, no Reddit API involved
    return _r.news(CFG)


def morning(dry=False):
    client = _client()
    st = _account(client)
    if MODE == "api":
        print(f"[account] u/{st['name']} comment karma {st['comment_karma']} link karma {st['link_karma']} unread {len(st['unread'])}")
        for m in st["unread"]:
            if any(w in (m["subject"] + m["body"]).lower() for w in ["banned", "removed", "warning", "suspend"]):
                msg = f"MOD MESSAGE in inbox: {m['subject']}\n{m['body']}\nRead it before approving anything today."
                print(msg); (not dry) and tg.notify(ENV, msg)
    else:
        # RSS cannot read the inbox. Say so plainly rather than implying it was checked.
        warn = ("Inbox NOT checked: no API access in rss mode. Open your Reddit inbox and read any "
                "moderator message before you approve anything today.")
        print(f"[account] u/{st['name']} (karma and inbox unavailable in {MODE} mode)")
        print(f"[account] {warn}")
        if not dry: tg.notify(ENV, warn)
    store.expire_old()

    threads = _threads(client)
    print(f"[research] {len(threads)} candidate threads")
    picked, per_sub = [], {}
    for t in threads:
        if per_sub.get(t["sub"], 0) >= 2: continue
        picked.append(t); per_sub[t["sub"]] = per_sub.get(t["sub"], 0) + 1
        if len(picked) >= 8: break

    sent = 0
    for t in picked:
        store.mark_seen(t["id"])
        allowed, why = brain.mention_allowed(CFG, t["sub"], t)
        text, err = brain.draft_comment(ENV, CFG, VOICE, t, allowed, why)
        if not text:
            print(f"  skip r/{t['sub']} '{t['title'][:60]}': {err or 'failed style checks'}"); continue
        print(f"\n--- r/{t['sub']} | {t['title'][:80]}\n{t['url']}\n[mention={allowed}: {why}]\n{text}\n")
        if not dry:
            qid = store.enqueue("comment", t["sub"], t["id"], t["url"], t["title"], text, mention=int(allowed and CFG['product']['url'] in text.lower()))
            tg.send_for_approval(ENV, qid, "comment", t["sub"], t["title"], t["url"], text, mention=int(allowed)); sent += 1

    # value post: at most one draft every `days_between_posts`, rotating themes and subs
    _, _, _, last_post = store.counts_today()
    if time.time() - last_post >= CFG["pacing"]["days_between_posts"] * 86400 and CFG["value_posts"]["post_allowed_subs"]:
        sub = random.choice(CFG["value_posts"]["post_allowed_subs"])
        theme = random.choice(CFG["value_posts"]["themes"])
        items = _news()
        p = brain.draft_post(ENV, CFG, VOICE, sub, theme, items)
        if p:
            print(f"\n=== VALUE POST for r/{sub}\n{p['title']}\n\n{p['body']}\n")
            if not dry:
                qid = store.enqueue("post", sub, "", "", "", p["body"], title=p["title"])
                tg.send_for_approval(ENV, qid, "post", sub, "", "", p["body"], title=p["title"]); sent += 1
    if not dry:
        tail = ("Approve what you like, then run `python run.py handoff` to paste them yourself."
                if MODE != "api" else "Approve what you like, the worker posts with pacing.")
        tg.notify(ENV, f"Morning run done. {sent} drafts sent. {tail}")


def worker():
    if MODE != "api":
        # Nothing can post on your behalf without API access.
        print("[worker] rss mode: approvals only, nothing is posted automatically.")
        print("[worker] approve drafts in Discord, then run: python run.py handoff")
        if ENV["DISCORD_BOT_TOKEN"]:
            discord_approve.run_worker(None, CFG, ENV, post=False)
            return
        offset = [0]
        while True:
            try:
                telegram_approve.poll_decisions(ENV, offset)
                n = handoff.pending_count()
                print(datetime.datetime.now().strftime("%H:%M"), f"{n} approved and waiting for `run.py handoff`")
            except Exception as e:
                print("[worker] error", e)
            time.sleep(60)

    reddit = _client()
    if ENV["DISCORD_BOT_TOKEN"]:
        discord_approve.run_worker(reddit, CFG, ENV)      # blocks; handles buttons + posting loop
        return
    offset = [0]
    print("[worker] running (telegram). Ctrl+C to stop.")
    while True:
        try:
            telegram_approve.poll_decisions(ENV, offset)
            print(datetime.datetime.now().strftime("%H:%M"), poster.worker_tick(reddit, CFG, ENV, telegram_approve))
        except Exception as e:
            print("[worker] error", e)
        time.sleep(60)


def status():
    total, per_sub, last, last_post = store.counts_today()
    if MODE == "api":
        st = _account(_client())
        print(f"u/{st['name']}: comment karma {st['comment_karma']}, link karma {st['link_karma']}, unread {len(st['unread'])}")
    else:
        print(f"mode: {MODE}. Karma and inbox are not readable in this mode; check them in the app.")
    print(f"today: {total} comments {dict(per_sub)}; last activity {int((time.time()-last)/60) if last else '-'} min ago")
    with store.conn() as c:
        for r in c.execute("SELECT status, COUNT(*) n FROM queue GROUP BY status"): print(f"queue {r['status']}: {r['n']}")


def preflight():
    need = ["ANTHROPIC_API_KEY"]
    if MODE == "api":
        need += ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD", "REDDIT_USER_AGENT"]
    elif MODE == "zernio":
        need += ["ZERNIO_API_KEY", "ZERNIO_ACCOUNT_ID"]
    if not ENV["DISCORD_BOT_TOKEN"] and not ENV["TELEGRAM_BOT_TOKEN"]:
        need += ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"]
    elif ENV["DISCORD_BOT_TOKEN"]:
        need += ["DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"]
    else:
        need += ["TELEGRAM_CHAT_ID"]
    missing = [k for k in need if not ENV[k]]
    if missing:
        sys.exit(f"[.env] missing: {', '.join(missing)}\nFill them in {ROOT / '.env'} (see .env.example) and run again.")


def zernio_accounts():
    """Look up the accountId to put in ZERNIO_ACCOUNT_ID. Needs only ZERNIO_API_KEY."""
    from rm import zernio
    try:
        zernio.print_accounts(ENV)
    except zernio.ZernioError as e:
        sys.exit(f"[zernio] {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in ("morning", "worker", "status", "dry", "handoff", "zernio-accounts"):
        sys.exit(f"unknown command '{cmd}'. one of: morning worker status dry handoff zernio-accounts")
    if cmd == "zernio-accounts":      # setup helper, runs before the rest of .env is filled
        zernio_accounts(); sys.exit(0)
    preflight()
    {"morning": morning,
     "worker": worker,
     "status": status,
     "dry": lambda: morning(dry=True),
     "handoff": lambda: handoff.run_all(CFG)}[cmd]()
