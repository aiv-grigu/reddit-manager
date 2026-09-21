import sqlite3, json, time, os

DB = os.environ.get("RM_DB", "rm_state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (thread_id TEXT PRIMARY KEY, seen_at REAL);
CREATE TABLE IF NOT EXISTS queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, sub TEXT, thread_id TEXT, thread_url TEXT, thread_title TEXT,
  body TEXT, title TEXT, mention INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending',   -- pending | approved | rejected | posted | failed | expired
  created_at REAL, decided_at REAL, posted_at REAL, result_url TEXT, tg_msg_id INTEGER
);
CREATE TABLE IF NOT EXISTS posted (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, sub TEXT, thread_id TEXT,
  mention INTEGER, posted_at REAL, url TEXT
);
"""

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.executescript(SCHEMA); return c

def mark_seen(tid):
    with conn() as c: c.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (tid, time.time()))

def is_seen(tid):
    with conn() as c: return c.execute("SELECT 1 FROM seen WHERE thread_id=?", (tid,)).fetchone() is not None

def enqueue(kind, sub, thread_id, thread_url, thread_title, body, title="", mention=0):
    with conn() as c:
        cur = c.execute("""INSERT INTO queue(kind,sub,thread_id,thread_url,thread_title,body,title,mention,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""", (kind, sub, thread_id, thread_url, thread_title, body, title, int(mention), time.time()))
        return cur.lastrowid

def set_tg(qid, msg_id):
    with conn() as c: c.execute("UPDATE queue SET tg_msg_id=? WHERE id=?", (msg_id, qid))

def decide(qid, status):
    with conn() as c: c.execute("UPDATE queue SET status=?, decided_at=? WHERE id=? AND status='pending'", (status, time.time(), qid))

def next_approved():
    with conn() as c:
        return c.execute("SELECT * FROM queue WHERE status='approved' ORDER BY decided_at ASC LIMIT 1").fetchone()

def mark_posted(qid, url, ok=True):
    with conn() as c:
        row = c.execute("SELECT * FROM queue WHERE id=?", (qid,)).fetchone()
        c.execute("UPDATE queue SET status=?, posted_at=?, result_url=? WHERE id=?", ("posted" if ok else "failed", time.time(), url, qid))
        if ok:
            c.execute("INSERT INTO posted(kind,sub,thread_id,mention,posted_at,url) VALUES(?,?,?,?,?,?)",
                      (row["kind"], row["sub"], row["thread_id"], row["mention"], time.time(), url))

def expire_old(hours=20):
    with conn() as c:
        c.execute("UPDATE queue SET status='expired' WHERE status='pending' AND created_at < ?", (time.time() - hours * 3600,))

def counts_today():
    day = time.time() - 24 * 3600
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM posted WHERE kind='comment' AND posted_at>?", (day,)).fetchone()[0]
        per_sub = dict(c.execute("SELECT sub, COUNT(*) FROM posted WHERE kind='comment' AND posted_at>? GROUP BY sub", (day,)).fetchall())
        last = c.execute("SELECT MAX(posted_at) FROM posted").fetchone()[0] or 0
        last_post = c.execute("SELECT MAX(posted_at) FROM posted WHERE kind='post'").fetchone()[0] or 0
        return total, per_sub, last, last_post

def mention_stats(sub):
    with conn() as c:
        recent = c.execute("SELECT mention FROM posted WHERE kind='comment' ORDER BY posted_at DESC LIMIT 10").fetchall()
        in_sub = c.execute("SELECT COUNT(*) FROM posted WHERE kind='comment' AND sub=?", (sub,)).fetchone()[0]
        return sum(r["mention"] for r in recent), in_sub

def already_engaged(thread_id):
    with conn() as c:
        return c.execute("SELECT 1 FROM posted WHERE thread_id=?", (thread_id,)).fetchone() is not None
