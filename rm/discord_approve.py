"""Discord approval gate. Drafts are sent over REST with Approve/Reject buttons (no gateway needed for the
morning run). The worker runs a discord.py client that handles the button presses and posts with pacing."""
import asyncio, requests, time
from . import store, poster

API = "https://discord.com/api/v10"

def _h(env): return {"Authorization": f"Bot {env['DISCORD_BOT_TOKEN']}", "Content-Type": "application/json"}

def _post(env, payload):
    r = requests.post(f"{API}/channels/{env['DISCORD_CHANNEL_ID']}/messages", headers=_h(env), json=payload, timeout=20)
    return r.json() if r.ok else {}

def send_for_approval(env, qid, kind, sub, thread_title, thread_url, body, title="", mention=0):
    head = f"**#{qid} {'POST' if kind == 'post' else 'COMMENT'} in r/{sub}**" + ("  `mentions product`" if mention else "")
    ctx = f"{thread_title}\n<{thread_url}>" if kind == "comment" else f"**Title:** {title}"
    content = f"{head}\n{ctx}\n\n{body}"
    payload = {
        "content": content[:1950],
        "components": [{"type": 1, "components": [
            {"type": 2, "style": 3, "label": "Approve", "custom_id": f"ok:{qid}"},
            {"type": 2, "style": 4, "label": "Reject", "custom_id": f"no:{qid}"}]}]}
    r = _post(env, payload)
    if r.get("id"): store.set_tg(qid, int(r["id"]))
    return bool(r.get("id"))

def notify(env, text):
    _post(env, {"content": text[:1950]})

def run_worker(reddit, cfg, env):
    import discord
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    owner = int(env["DISCORD_OWNER_ID"])
    channel_id = int(env["DISCORD_CHANNEL_ID"])

    @client.event
    async def on_ready():
        print(f"[worker] connected as {client.user}")
        client.loop.create_task(tick_loop())

    @client.event
    async def on_interaction(inter: discord.Interaction):
        if inter.type != discord.InteractionType.component: return
        if inter.user.id != owner:
            await inter.response.send_message("Not your queue.", ephemeral=True); return
        data = inter.data.get("custom_id", "")
        if ":" not in data: return
        action, qid = data.split(":", 1)
        store.decide(int(qid), "approved" if action == "ok" else "rejected")
        label = "Approved. Worker will post with pacing." if action == "ok" else "Rejected."
        try:
            await inter.response.edit_message(content=inter.message.content + f"\n\n> {label}", view=None)
        except Exception:
            await inter.response.send_message(label, ephemeral=True)

    async def tick_loop():
        await client.wait_until_ready()
        ch = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        while not client.is_closed():
            try:
                msg = await asyncio.get_event_loop().run_in_executor(None, poster.worker_tick, reddit, cfg, env, _Notifier(env))
                print(time.strftime("%H:%M"), msg)
            except Exception as e:
                print("[worker] error", e)
            await asyncio.sleep(60)

    client.run(env["DISCORD_BOT_TOKEN"], log_handler=None)

class _Notifier:
    """poster.worker_tick expects an object with .notify(env, text)"""
    def __init__(self, env): self.env = env
    def notify(self, env, text): notify(env, text)
