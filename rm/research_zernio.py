"""Research via Zernio's Reddit endpoints (official OAuth, no Reddit app of our own).

Closest thing to full API parity without a Reddit developer app. Unlike rss mode this
gets real keyword search and real thread metadata, so the config filters that rss mode
could only approximate work properly here:

  numComments  -> max_comments_on_thread
  score        -> min_thread_score
  createdUtc   -> max_thread_age_hours

Still missing versus the real API: a thread's existing comments, whether it is locked
or archived, and the account inbox. The approval card links the thread, so the owner
sees the real state before approving anything.

Read-only. Posting stays in handoff, where a human hits reply.
"""
import time

from . import store
from .zernio import Zernio, ZernioError


def client(env, cfg):
    z = Zernio(env.get("ZERNIO_API_KEY", ""), env.get("ZERNIO_ACCOUNT_ID", ""))
    if not z.account_id:
        raise ZernioError("ZERNIO_ACCOUNT_ID is empty. Run `python run.py zernio-accounts` and put the reddit id in .env.")
    z.timeout = cfg.get("zernio", {}).get("timeout_seconds", 25)
    return z


def _score(tier, it, age_h, cfg):
    """Same weighting as research._score, now with the real metadata."""
    if age_h > cfg["max_thread_age_hours"]:
        return None
    n_comments = it.get("numComments")
    if n_comments is not None and n_comments > cfg["max_comments_on_thread"]:
        return None
    if (it.get("score") or 0) < cfg["min_thread_score"]:
        return None
    if it.get("stickied"):
        return None
    if it.get("over18"):
        return None
    if not it.get("author") or it["author"] in ("[deleted]", "AutoModerator"):
        return None

    title = (it.get("title") or "").lower()
    blob = title + " " + (it.get("selftext") or "").lower()
    help_hit = any(h in title for h in cfg["help_signals"])
    kw_hit = sum(1 for q in cfg["queries"] if all(w in blob for w in q.lower().split()[:2]))

    score = {1: 30, 2: 20, 3: 8}[tier]
    score += 25 if help_hit else 0
    score += min(kw_hit, 3) * 10
    score += max(0, 15 - age_h) if age_h < 15 else 0
    if n_comments is not None:
        score += max(0, 12 - n_comments)      # fewer comments = your answer sits higher
    return int(score)


def _tier_of(sub, cfg):
    """Tier for a configured sub, or None if it is not one of ours.

    Unrestricted search returns threads from anywhere on Reddit. Anything outside the
    configured lists is dropped: the owner has not read those subs' rules, and posting
    into an unvetted sub is the single fastest way to collect a ban.
    """
    low = sub.lower()
    for name, num in (("tier1", 1), ("tier2", 2), ("tier3", 3)):
        if any(s.lower() == low for s in cfg["subs"][name]):
            return num
    return None


def _absorb(cands, items, cfg, sub_hint=""):
    for it in items or []:
        tid = it.get("id") or (it.get("fullname") or "").replace("t3_", "")
        if not tid or tid in cands:
            continue
        sub = it.get("subreddit") or sub_hint
        # search can return user profile pages (u_someone) and subs we do not target
        if not sub or sub.lower().startswith("u_") or sub in cfg["banned_subs"]:
            continue
        if store.is_seen(tid) or store.already_engaged(tid):
            continue
        tier = _tier_of(sub, cfg)
        if tier is None:            # not a sub whose rules the owner has read
            continue
        created = it.get("createdUtc") or 0
        age_h = (time.time() - created) / 3600 if created else 999
        sc = _score(tier, it, age_h, cfg)
        if sc is None:
            continue
        cands[tid] = dict(
            id=tid, sub=sub, tier=tier, title=it.get("title") or "",
            url=it.get("permalink") or it.get("url") or f"https://www.reddit.com/comments/{tid}",
            body=(it.get("selftext") or "")[:2500],
            age_h=round(age_h, 1), comments=it.get("numComments"),
            author=it.get("author") or "", score=sc, top_comments=[],
        )


def find_threads(z, cfg):
    """Same dict shape as research.find_threads, so brain/run need no changes."""
    zc = cfg.get("zernio", {})
    per_feed = zc.get("feed_limit", 25)
    per_search = zc.get("search_limit", 25)
    max_queries = zc.get("max_queries", 8)
    search_subs = zc.get("search_subreddits", True)

    cands = {}

    # 1. listings per configured sub
    for tier_name in ("tier1", "tier2", "tier3"):
        for sub in cfg["subs"][tier_name]:
            if sub in cfg["banned_subs"]:
                continue
            try:
                d = z.feed(sub, sort="new", limit=per_feed)
                _absorb(cands, d.get("items"), cfg, sub_hint=sub)
            except ZernioError as e:
                print(f"[zernio] feed r/{sub}: {e}")

    # 2. keyword search, the thing rss mode cannot do
    targets = [""] if not search_subs else [""] + [s for s in cfg["subs"]["tier2"] if s not in cfg["banned_subs"]][:6]
    for q in cfg["queries"][:max_queries]:
        for sub in targets:
            try:
                d = z.search(q, subreddit=sub, sort="new", limit=per_search)
                _absorb(cands, d.get("items"), cfg, sub_hint=sub)
            except ZernioError as e:
                print(f"[zernio] search '{q}'{' in r/' + sub if sub else ''}: {e}")
                break

    return sorted(cands.values(), key=lambda x: -x["score"])


def account_status(z, username):
    """Karma and the inbox are not exposed by Zernio, same as rss mode.

    The morning run must tell the owner to read their own inbox rather than imply
    the moderator-message check ran.
    """
    return dict(name=username or "(unknown)", comment_karma=None, link_karma=None,
                unread=[], inbox_checked=False, recent=[])
