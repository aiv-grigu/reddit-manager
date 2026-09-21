#!/usr/bin/env python3
"""WhoCanFindMe Reddit Manager.

  python run.py morning   # research Reddit + news, draft comments (+ a value post on schedule), send to Telegram
  python run.py worker    # long-running: polls Telegram approvals and posts with pacing
  python run.py status    # account karma, inbox, queue, today's counts
  python run.py dry       # research + draft, print to terminal, send nothing, post nothing
"""
import os, sys, time, random, datetime, pathlib
import yaml
from dotenv import load_dotenv
from rm import research, brain, store, poster
from rm import discord_approve, telegram_approve

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
ENV = {k: os.environ.get(k, "") for k in ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD",
                                          "REDDIT_USER_AGENT", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                                          "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"]}
# approval channel: discord if configured, else telegram
tg = discord_approve if ENV["DISCORD_BOT_TOKEN"] else telegram_approve
CFG = yaml.safe_load(open(ROOT / "config.yaml"))
_voice_path = ROOT / "voice.md" if (ROOT / "voice.md").exists() else ROOT / "voice.example.md"
if _voice_path.name == "voice.example.md": print("[voice] voice.md not found, using voice.example.md. Copy it to voice.md and add your own writing samples.")
VOICE = open(_voice_path, encoding="utf-8").read()

def morning(dry=False):
    reddit = research.reddit_client(ENV)
    st = research.account_status(reddit)
    print(f"[account] u/{st['name']} comment karma {st['comment_karma']} link karma {st['link_karma']} unread {len(st['unread'])}")
    for m in st["unread"]:
        if any(w in (m["subject"] + m["body"]).lower() for w in ["banned", "removed", "warning", "suspend"]):
            msg = f"MOD MESSAGE in inbox: {m['subject']}\n{m['body']}\nRead it before approving anything today."
            print(msg); (not dry) and tg.notify(ENV, msg)
    store.expire_old()

    threads = research.find_threads(reddit, CFG)
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
        items = research.news(CFG)
        p = brain.draft_post(ENV, CFG, VOICE, sub, theme, items)
        if p:
            print(f"\n=== VALUE POST for r/{sub}\n{p['title']}\n\n{p['body']}\n")
            if not dry:
                qid = store.enqueue("post", sub, "", "", "", p["body"], title=p["title"])
                tg.send_for_approval(ENV, qid, "post", sub, "", "", p["body"], title=p["title"]); sent += 1
    if not dry: tg.notify(ENV, f"Morning run done. {sent} drafts sent. Karma {st['comment_karma']}/{st['link_karma']}. Approve what you like, the worker posts with pacing.")

def worker():
    reddit = research.reddit_client(ENV)
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
    reddit = research.reddit_client(ENV)
    st = research.account_status(reddit)
    total, per_sub, last, last_post = store.counts_today()
    print(f"u/{st['name']}: comment karma {st['comment_karma']}, link karma {st['link_karma']}, unread {len(st['unread'])}")
    print(f"today: {total} comments {dict(per_sub)}; last activity {int((time.time()-last)/60) if last else '-'} min ago")
    with store.conn() as c:
        for r in c.execute("SELECT status, COUNT(*) n FROM queue GROUP BY status"): print(f"queue {r['status']}: {r['n']}")

def preflight():
    need = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD", "REDDIT_USER_AGENT", "ANTHROPIC_API_KEY"]
    if not ENV["DISCORD_BOT_TOKEN"] and not ENV["TELEGRAM_BOT_TOKEN"]:
        need += ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"]
    elif ENV["DISCORD_BOT_TOKEN"]:
        need += ["DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"]
    else:
        need += ["TELEGRAM_CHAT_ID"]
    missing = [k for k in need if not ENV[k]]
    if missing:
        sys.exit(f"[.env] missing: {', '.join(missing)}\nFill them in {ROOT / '.env'} (see .env.example) and run again.")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    preflight()
    {"morning": morning, "worker": worker, "status": status, "dry": lambda: morning(dry=True)}[cmd]()
