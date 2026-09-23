"""Thin client for the Zernio API (third-party Reddit access via official OAuth).

Zernio holds the approved Reddit app; you connect your Reddit account in their
dashboard and authorise it. No Reddit developer app of your own is needed.

Endpoints used here (see zernio.com/llms.txt):
  GET /v1/accounts                       list connected accounts, to find your accountId
  GET /v1/reddit/search?q=&subreddit=    keyword search, the thing RSS mode cannot do
  GET /v1/reddit/feed?subreddit=&sort=   subreddit listing

Auth is `Authorization: Bearer <ZERNIO_API_KEY>`.

Read-only as used by the research backend. Posting through Zernio is deliberately
not wired in: approved drafts go through handoff so a human still hits reply.
"""
import json, urllib.parse, urllib.request, urllib.error

BASE = "https://api.zernio.com"


class ZernioError(RuntimeError):
    pass


class Zernio:
    def __init__(self, api_key, account_id="", timeout=25, base=BASE):
        if not api_key:
            raise ZernioError("ZERNIO_API_KEY is empty. Put it in .env (see .env.example).")
        self.key = api_key
        self.account_id = account_id
        self.timeout = timeout
        self.base = base.rstrip("/")

    def _get(self, path, params=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                raise ZernioError(f"HTTP {e.code} from Zernio: check ZERNIO_API_KEY. {detail}") from None
            raise ZernioError(f"HTTP {e.code} from Zernio on {path}. {detail}") from None
        except Exception as e:
            raise ZernioError(f"{type(e).__name__} calling Zernio {path}: {e}") from None

    # --- accounts ------------------------------------------------------------
    def accounts(self):
        """Every connected account. Used to find the Reddit accountId for .env."""
        d = self._get("/v1/accounts")
        rows = d.get("data") or d.get("accounts") or (d if isinstance(d, list) else [])
        out = []
        for a in rows:
            out.append(dict(
                id=a.get("_id") or a.get("id") or "",
                platform=(a.get("platform") or "").lower(),
                username=a.get("username") or a.get("name") or "",
                profile=a.get("profileId") or "",
            ))
        return out

    def reddit_accounts(self):
        return [a for a in self.accounts() if a["platform"] == "reddit"]

    # --- reading -------------------------------------------------------------
    def search(self, query, subreddit="", sort="new", limit=25):
        return self._get("/v1/reddit/search", dict(
            accountId=self.account_id, q=query, subreddit=subreddit, sort=sort, limit=limit))

    def feed(self, subreddit, sort="new", limit=25):
        return self._get("/v1/reddit/feed", dict(
            accountId=self.account_id, subreddit=subreddit, sort=sort, limit=limit))


def print_accounts(env):
    """`python run.py zernio-accounts` - find the id to put in ZERNIO_ACCOUNT_ID."""
    z = Zernio(env.get("ZERNIO_API_KEY", ""))
    rows = z.accounts()
    if not rows:
        print("[zernio] no connected accounts. Connect Reddit in the Zernio dashboard first.")
        return
    print(f"[zernio] {len(rows)} connected account(s):\n")
    for a in rows:
        mark = "  <- put this in ZERNIO_ACCOUNT_ID" if a["platform"] == "reddit" else ""
        print(f"  {a['platform']:<10} {a['username']:<24} {a['id']}{mark}")
    if not any(a["platform"] == "reddit" for a in rows):
        print("\n[zernio] no reddit account among them. Connect one in the dashboard.")
