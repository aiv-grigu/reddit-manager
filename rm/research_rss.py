"""Research without the Reddit API, using public RSS listing feeds.

Reddit closed self-service Data API access (Responsible Builder Policy). This module
is the no-API fallback: it reads the same public listing pages a logged-out browser
would, as Atom feeds, and scores them the same way research.py does.

What works and what does not, measured against live Reddit:
  /r/<sub>/new/.rss and /rising/.rss   -> 200, 25 entries, parses clean
  /search/.rss and /r/<sub>/search/.rss -> 429, gated. Keyword search is therefore
                                          done locally over the fetched entries.
  back-to-back requests                -> 429. One request at a time, paced.

Fields RSS does not carry: comment count, score, locked/archived, and the thread's
existing comments. Freshness stands in for comment count, and the approval card links
the thread so the owner sees the real state before approving anything.

Read-only. Nothing here writes to Reddit.
"""
import time, re, random, urllib.request, urllib.error

import feedparser

from . import store

FEED_UA = "windows:reddit-manager:1.0 (personal, read-only, by /u/{user})"
TAG_RE = re.compile(r"<[^>]+>")


class RssClient:
    """Fetches feeds one at a time, politely. Reddit 429s anything faster."""

    def __init__(self, cfg, username=""):
        r = cfg.get("rss", {})
        self.min_gap = r.get("min_seconds_between_requests", 45)
        self.jitter = r.get("jitter_seconds", 15)
        self.retries = r.get("retries_on_429", 2)
        self.backoff = r.get("backoff_seconds", 120)
        self.timeout = r.get("timeout_seconds", 25)
        self.ua = FEED_UA.format(user=username or "reddit-manager")
        self._last = 0.0

    def _wait(self):
        gap = self.min_gap + random.uniform(0, self.jitter)
        due = self._last + gap - time.time()
        if due > 0:
            time.sleep(due)

    def get(self, url):
        """Returns a parsed feed, or None. Never raises."""
        for attempt in range(self.retries + 1):
            self._wait()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "application/atom+xml"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                self._last = time.time()
                d = feedparser.parse(raw)
                if d.entries:
                    return d
                print(f"[rss] empty feed: {url}")
                return None
            except urllib.error.HTTPError as e:
                self._last = time.time()
                if e.code == 429 and attempt < self.retries:
                    wait = self.backoff * (attempt + 1)
                    print(f"[rss] 429 on {url}, backing off {wait}s")
                    time.sleep(wait)
                    continue
                print(f"[rss] HTTP {e.code}: {url}")
                return None
            except Exception as e:
                self._last = time.time()
                print(f"[rss] {type(e).__name__}: {url}: {e}")
                return None
        return None


def _epoch(entry):
    for key in ("updated_parsed", "published_parsed"):
        t = entry.get(key)
        if t:
            return time.mktime(t)
    return 0.0


def _clean(html):
    return TAG_RE.sub(" ", html or "").replace("&#32;", " ").replace("&amp;", "&").strip()


def _score(tier, title, body, age_h, cfg):
    """Same shape as research._score, minus the fields RSS cannot provide."""
    if age_h > cfg["max_thread_age_hours"]:
        return None
    low = title.lower()
    blob = low + " " + body.lower()
    help_hit = any(h in low for h in cfg["help_signals"])
    kw_hit = sum(1 for q in cfg["queries"] if all(w in blob for w in q.lower().split()[:2]))
    score = {1: 30, 2: 20, 3: 8}[tier]
    score += 25 if help_hit else 0
    score += min(kw_hit, 3) * 10
    score += max(0, 15 - age_h) if age_h < 15 else 0
    # freshness stands in for "few comments so far", which RSS does not report
    score += 12 if age_h < 3 else (6 if age_h < 8 else 0)
    return int(score)


def find_threads(client, cfg):
    """Returns the same dict shape as research.find_threads, so brain/run need no changes."""
    cands = {}
    feeds = cfg.get("rss", {}).get("listings", ["new", "rising"])
    for tier_name, tier_num in (("tier1", 1), ("tier2", 2), ("tier3", 3)):
        for sub in cfg["subs"][tier_name]:
            if sub in cfg["banned_subs"]:
                continue
            for listing in feeds:
                d = client.get(f"https://www.reddit.com/r/{sub}/{listing}/.rss")
                if not d:
                    continue
                for e in d.entries:
                    tid = (e.get("id") or "").replace("t3_", "")
                    if not tid or tid in cands or store.is_seen(tid) or store.already_engaged(tid):
                        continue
                    title = e.get("title", "")
                    body = _clean(e.get("summary", ""))[:2500]
                    ts = _epoch(e)
                    age_h = (time.time() - ts) / 3600 if ts else 999
                    sc = _score(tier_num, title, body, age_h, cfg)
                    if sc is None:
                        continue
                    cands[tid] = dict(
                        id=tid, sub=sub, tier=tier_num, title=title,
                        url=e.get("link", f"https://www.reddit.com/comments/{tid}"),
                        body=body, age_h=round(age_h, 1),
                        comments=None,          # unknown without the API
                        author=(e.get("author") or "").lstrip("/u/"),
                        score=sc, top_comments=[],
                    )
    return sorted(cands.values(), key=lambda x: -x["score"])


def account_status(client, username):
    """Best effort without the API. Karma and the inbox are NOT available over RSS.

    The morning run relies on the inbox check to surface moderator warnings before
    anything is approved. That check cannot run in RSS mode, so the caller must tell
    the owner to read their inbox themselves. Never pretend the inbox was checked.
    """
    posts = []
    if username:
        d = client.get(f"https://www.reddit.com/user/{username}/.rss")
        if d:
            posts = [e.get("title", "")[:80] for e in d.entries[:5]]
    return dict(name=username or "(unknown)", comment_karma=None, link_karma=None,
                unread=[], inbox_checked=False, recent=posts)
