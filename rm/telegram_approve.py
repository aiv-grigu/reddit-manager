"""Approval gate. Every draft goes to your phone. Nothing posts without a tap."""
import requests, time
from . import store

API = "https://api.telegram.org/bot{token}/{method}"

def _call(env, method, **kw):
    return requests.post(API.format(token=env["TELEGRAM_BOT_TOKEN"], method=method), json=kw, timeout=20).json()

def send_for_approval(env, qid, kind, sub, thread_title, thread_url, body, title="", mention=0):
    head = f"#{qid} {'POST' if kind == 'post' else 'COMMENT'} in r/{sub}" + ("  [mentions product]" if mention else "")
    ctx = f"{thread_title}\n{thread_url}" if kind == "comment" else f"Title: {title}"
    text = f"{head}\n\n{ctx}\n\n{body}"
    r = _call(env, "sendMessage", chat_id=env["TELEGRAM_CHAT_ID"], text=text[:4000],
              reply_markup={"inline_keyboard": [[{"text": "Approve", "callback_data": f"ok:{qid}"},
                                                 {"text": "Reject", "callback_data": f"no:{qid}"}]]})
    if r.get("ok"): store.set_tg(qid, r["result"]["message_id"])
    return r.get("ok", False)

def notify(env, text):
    _call(env, "sendMessage", chat_id=env["TELEGRAM_CHAT_ID"], text=text[:4000])

def poll_decisions(env, offset_holder):
    """Process button taps. offset_holder is a one-element list so the caller keeps the update offset."""
    r = _call(env, "getUpdates", offset=offset_holder[0], timeout=0)
    for u in r.get("result", []):
        offset_holder[0] = u["update_id"] + 1
        cq = u.get("callback_query")
        if not cq: continue
        data = cq.get("data", "")
        if ":" not in data: continue
        action, qid = data.split(":", 1)
        store.decide(int(qid), "approved" if action == "ok" else "rejected")
        _call(env, "answerCallbackQuery", callback_query_id=cq["id"], text="Approved, will post with pacing" if action == "ok" else "Rejected")
        _call(env, "editMessageReplyMarkup", chat_id=env["TELEGRAM_CHAT_ID"], message_id=cq["message"]["message_id"], reply_markup={"inline_keyboard": []})
