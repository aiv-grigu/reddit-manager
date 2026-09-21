"""Find threads worth answering. Read-only. Never writes to Reddit."""
import time, re
import praw, feedparser
from . import store

def reddit_client(env):
    return praw.Reddit(client_id=env["REDDIT_CLIENT_ID"], client_secret=env["REDDIT_CLIENT_SECRET"],
                       username=env["REDDIT_USERNAME"], password=env["REDDIT_PASSWORD"],
                       user_agent=env["REDDIT_USER_AGENT"])

def _score(sub, tier, s, cfg):
    age_h = (time.time() - s.created_utc) / 3600
    if age_h > cfg["max_thread_age_hours"]: return None
    if s.num_comments > cfg["max_comments_on_thread"]: return None
    if s.score < cfg["min_thread_score"]: return None
    if getattr(s, "locked", False) or getattr(s, "archived", False): return None
    if s.author is None: return None
    title = s.title.lower()
    help_hit = any(h in title for h in cfg["help_signals"])
    kw_hit = sum(1 for q in cfg["queries"] if any(w in (title + " " + (s.selftext or "").lower()) for w in q.lower().split()[:2]))
    score = 0
    score += {1: 30, 2: 20, 3: 8}[tier]
    score += 25 if help_hit else 0
    score += min(kw_hit, 3) * 10
    score += max(0, 15 - age_h) if age_h < 15 else 0
    score += max(0, 12 - s.num_comments)   # fewer comments = more visible answer
    return score

def find_threads(reddit, cfg, limit_per_sub=40):
    cands = {}
    tiers = cfg["subs"]
    for tier_name, tier_num in (("tier1", 1), ("tier2", 2), ("tier3", 3)):
        for sub in tiers[tier_name]:
            if sub in cfg["banned_subs"]: continue
            try:
                sr = reddit.subreddit(sub)
                pool = list(sr.new(limit=limit_per_sub)) + list(sr.rising(limit=15))
                for q in cfg["queries"][:8]:
                    try: pool += list(sr.search(q, sort="new", time_filter="week", limit=8))
                    except Exception: pass
            except Exception as e:
                print(f"[research] skip r/{sub}: {e}"); continue
            for s in pool:
                if s.id in cands or store.is_seen(s.id) or store.already_engaged(s.id): continue
                sc = _score(sub, tier_num, s, cfg)
                if sc is None: continue
                cands[s.id] = dict(id=s.id, sub=sub, tier=tier_num, title=s.title, url="https://www.reddit.com" + s.permalink,
                                   body=(s.selftext or "")[:2500], age_h=round((time.time() - s.created_utc) / 3600, 1),
                                   comments=s.num_comments, score=sc,
                                   top_comments=_top_comments(s))
    ranked = sorted(cands.values(), key=lambda x: -x["score"])
    return ranked

def _top_comments(s, n=4):
    try:
        s.comments.replace_more(limit=0)
        return [c.body[:400] for c in s.comments[:n] if hasattr(c, "body")]
    except Exception:
        return []

def account_status(reddit):
    me = reddit.user.me()
    unread = []
    try:
        for m in reddit.inbox.unread(limit=10):
            unread.append(dict(subject=getattr(m, "subject", "comment reply"), body=getattr(m, "body", "")[:300]))
    except Exception: pass
    return dict(name=me.name, comment_karma=me.comment_karma, link_karma=me.link_karma, unread=unread)

def news(cfg, max_items=12):
    items = []
    for url in cfg["value_posts"]["news_feeds"]:
        try:
            f = feedparser.parse(url)
            for e in f.entries[:6]:
                items.append(dict(source=f.feed.get("title", url), title=e.get("title", ""), link=e.get("link", ""),
                                  summary=re.sub("<[^>]+>", "", e.get("summary", ""))[:300]))
        except Exception as ex:
            print(f"[news] {url}: {ex}")
    return items[:max_items]
