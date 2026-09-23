"""Draft -> edit to the owner's voice -> check -> rewrite loop. Every output passes style_checks or is dropped.
The account owner reads and approves every draft by hand before anything is posted (see discord_approve)."""
import json, re, random, time
import anthropic
from . import style_checks as hc, store

def _client(env): return anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])

def _json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}

def mention_allowed(cfg, sub, thread):
    """The 9:1 rule, the cooldowns and the sub policy, decided in code, never by the model.

    Any single disclosed mention is fine. What gets a domain filtered is saying it too
    often, so frequency is capped three ways: the 9:1 ratio, a gap between mentions
    anywhere, and a much longer gap before naming it twice in the same subreddit.
    """
    p = cfg["pacing"]
    if sub in cfg["mention_forbidden_subs"]: return False, "sub forbids mentions"
    recent_mentions, in_sub = store.mention_stats(sub)
    if recent_mentions >= p["max_mentions_per_10_comments"]: return False, "9:1 ratio used up"
    if in_sub < p["min_comments_in_sub_before_mention"] and sub not in cfg["mention_allowed_subs"]:
        return False, f"only {in_sub} comments in r/{sub}, need {p['min_comments_in_sub_before_mention']}"

    now = time.time()
    anywhere, here = store.last_mention(sub)
    gap_any = p.get("min_days_between_mentions_anywhere", 2) * 86400
    gap_sub = p.get("min_days_between_mentions_same_sub", 21) * 86400
    if anywhere and now - anywhere < gap_any:
        return False, f"last mention was {int((now - anywhere) / 3600)}h ago, need {int(gap_any / 3600)}h"
    if here and now - here < gap_sub:
        return False, f"already named it in r/{sub} {int((now - here) / 86400)}d ago, need {int(gap_sub / 86400)}d"
    cap = p.get("max_mentions_per_30_days", 4)
    if store.mentions_since(30) >= cap:
        return False, f"{cap} mentions already in the last 30 days"
    asked = bool(re.search(r"\b(tool|tools|checker|scanner|software|app|service)s?\b.*\b(recommend|any|what|which|best)\b|\b(recommend|any|what|which|best)\b.*\b(tool|tools|checker|scanner|software|app)s?\b",
                           (thread["title"] + " " + thread["body"]).lower()))
    if sub in cfg["mention_allowed_subs"]: return True, "sub allows"
    if asked: return True, "thread asked for tools"
    return False, "not asked"

WRITER_SYS = """You draft Reddit comments for the account owner described in the voice profile below. They read, edit and approve every draft by hand before it is posted. The voice profile is law. Output JSON only:
{"comment": "...", "why_this_angle": "...", "confidence": 0-100}
Confidence is how sure you are that a real expert would say this. Under 60 means you are guessing; say so and keep it short.
Never invent statistics.

Your job is to actually solve the person's problem. Read what they are stuck on and answer that, specifically, the way someone who has hit the same thing would. Name the real file, the real setting, the real check. If you cannot genuinely help, say so and keep it short rather than padding.

The product:
- Never mention it unless the user message says MENTION_ALLOWED: true. Default is that it does not exist.
- When allowed, the answer must be complete and useful WITHOUT it. Write the answer first as if there were no tool, then at most one closing sentence offering it, adapted from a given disclosure line.
- The standalone test: if the mention were deleted, the comment must still fully answer the question. If it would not, the mention is doing the work and the draft fails.
- The commercial relationship must stay visible in that sentence, but keep it light and human. "disclosure, that's us" or "I work on it so obviously biased" is enough. Never a formal disclaimer, never a pitch.
- Never imply you came across it as a neutral user. Not "found this tool", not "came across", not "there's a tool called". That framing is dishonest and it is what gets a domain filtered.

VOICE PROFILE:
"""

EDITOR_SYS = """You are a strict copy editor. Your only job is to make a draft read the way the account owner actually writes, per the voice profile, and to remove generic assistant phrasing. You will be given the voice profile, a draft, and a list of violations found by a rules engine. Rewrite the draft so that:
- every listed violation is gone
- the meaning and the specific advice are unchanged
- it sounds like the samples in the voice profile, plain and direct, not like an assistant
- sentence lengths vary a lot, at least one sentence under 6 words
- no dash of any kind except " - " with spaces, and at most one of those
- no list, no header, no bold, no emoji, no sign-off, no opening pleasantry
Then read it once more: is every claim something the owner would actually stand behind? If not, cut it. Never add facts that were not in the draft.
Output JSON only: {"text": "...", "changes": ["..."]}

VOICE PROFILE:
"""

def draft_comment(env, cfg, voice, thread, allowed, reason):
    cl = _client(env)
    disclosure = random.choice(cfg["product"]["disclosure_lines"])
    user = f"""SUBREDDIT: r/{thread['sub']}
THREAD TITLE: {thread['title']}
THREAD BODY: {thread['body'][:1800]}
EXISTING TOP COMMENTS (do not repeat these, add what they missed or disagree):
{chr(10).join('- ' + c for c in thread['top_comments'])}
MENTION_ALLOWED: {str(allowed).lower()} ({reason})
DISCLOSURE LINE IF ALLOWED: {disclosure}
Write the comment. 40 to 120 words."""
    r = cl.messages.create(model=cfg["models"]["writer"], max_tokens=700, system=WRITER_SYS + voice,
                           messages=[{"role": "user", "content": user}])
    d = _json(r.content[0].text)
    text = d.get("comment", "").strip()
    conf = int(d.get("confidence", 0))
    if conf < 60 or not text: return None, f"low confidence ({conf})"
    return edit_to_voice(env, cfg, voice, text, "comment", allowed), None

def draft_post(env, cfg, voice, sub, theme, news_items):
    cl = _client(env)
    user = f"""SUBREDDIT: r/{sub}
THEME: {theme}
RECENT NEWS YOU MAY USE (cite the source name in plain words if you use one, no links in the body):
{chr(10).join(f"- {n['source']}: {n['title']} :: {n['summary']}" for n in news_items)}
Write a value post: something a founder or marketer would save. Not about WhoCanFindMe. Do not mention it at all.
Output JSON only: {{"title": "...", "body": "...", "confidence": 0-100}}. Body 180 to 450 words. Title under 100 characters, no clickbait, no colon."""
    r = cl.messages.create(model=cfg["models"]["writer"], max_tokens=1400, system=WRITER_SYS + voice,
                           messages=[{"role": "user", "content": user}])
    d = _json(r.content[0].text)
    if int(d.get("confidence", 0)) < 60: return None
    body = edit_to_voice(env, cfg, voice, d.get("body", ""), "post", False)
    if not body: return None
    return dict(title=d.get("title", "").strip(), body=body)

def edit_to_voice(env, cfg, voice, text, kind, allowed):
    cl = _client(env)
    url = cfg["product"]["url"]
    for i in range(cfg["models"]["max_rewrite_loops"] + 1):
        v = hc.check(text, kind, allowed, url)
        if not v and i > 0: return text          # must pass at least one editing pass
        if not v and i == 0: v = ["(no rule violations, still run the voice pass)"]
        if i == cfg["models"]["max_rewrite_loops"]: break
        r = cl.messages.create(model=cfg["models"]["editor"], max_tokens=900, system=EDITOR_SYS + voice,
                               messages=[{"role": "user", "content": f"KIND: {kind}\nVIOLATIONS:\n" + "\n".join("- " + x for x in v) + f"\n\nDRAFT:\n{text}"}])
        text = _json(r.content[0].text).get("text", text).strip()
    return text if not hc.check(text, kind, allowed, url) else None
