# Reddit Manager

A personal assistant for one Reddit account. Each morning it finds fresh, unanswered questions in a fixed list of subreddits where the account owner has real expertise, drafts a reply in the owner's own voice, and sends it to the owner's phone. The owner reads, edits, approves or rejects each one. Only approved items are posted, one at a time, with hard daily caps.

It runs on a small always-on box (Python 3.10+). One instance, one Reddit account, one person. Nothing is ever posted without a manual approval.

## What it does each day

**07:00 `morning`**
1. Checks the account: karma, inbox, and any moderator message goes straight to your phone before anything else, so you read it before approving anything.
2. Reads `/new` and `/rising` in the configured subreddits plus a handful of keyword searches (config.yaml). Scores each thread: fresh, few comments, a question in the title, on-topic. Drops anything seen before, locked, archived, or already answered by this account. Roughly 100 to 200 API calls a day in total.
3. For the best 8 threads, code (not the model) decides whether naming the owner's product is even permitted: the sub's policy, the 9:1 ratio, prior helpful comments in that sub, and whether the thread actually asked for tools.
4. An LLM drafts a reply seeded with the owner's writing samples (`voice.md`). A second pass edits it into the owner's plain style. A deterministic rules engine checks it. Up to three loops; if it still fails, it is dropped.
5. Every 3 days it may also draft one informational text post (industry news, a checklist, a teardown; never about the product) for a sub whose rules the owner has read.
6. Everything lands in a private Discord channel with Approve / Reject buttons. The owner reads every draft.

**All day `worker`**
Runs as a Discord bot listening for the owner's button presses. Posts approved items one at a time, 25 to 45 minutes apart with random jitter, max 6 comments a day, max 2 per sub, only in daytime hours, with a short pause before each action. If Reddit returns a rate limit, captcha or account notice, it stops and messages the owner.

## How it behaves

The account should look like what it is: one person who answers questions in their field and occasionally, with disclosure, mentions the thing they built.

- Nine of ten comments never mention the product. The tenth mentions it only in a sub whose rules allow it, only at the end, only after a complete answer, and only with an explicit "I built this" disclosure. This is enforced in code (`brain.mention_allowed` + `style_checks`), not left to the model.
- Fake-discovery phrasing ("found this tool", "came across", "there's a tool called") is a hard reject. If the product is named, it is named as the author's own.
- Comments before posts. The product is not named in a sub until the account has five prior helpful comments there.
- Pacing is randomised and capped so the account keeps a normal human rhythm.
- Nothing posts without a human tap. Ever.

## Style and policy checks

Two layers, because either alone is not enough.

**Rules engine** (`rm/style_checks.py`), deterministic, runs before and after the model:
em dash and en dash, emoji, any markdown in comments, generic assistant phrases, sign-offs, three parallel sentences, colon-lists, uniform sentence length, semicolon overuse, length bounds, and the full product mention policy.

**Editing pass** (`brain.edit_to_voice`), the model reads `voice.md` (the owner's own writing samples and register) plus the exact violations found, and rewrites to fix them without changing the advice. Every draft goes through at least one pass even if the rules engine found nothing.

Test it yourself: `python -c "from rm import style_checks as h; print(h.check(open('t.txt').read()))"`

Add to `voice.md` any time you have new real samples of your own writing. More samples = better voice match. Five real comments beat fifty rules.

## Setup (20 minutes, once)

1. **Reddit API access**: Reddit requires approval under its [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy) before an app can be created. Submit a Data Access Request describing this tool, then create a **script** app at reddit.com/prefs/apps with redirect uri `http://localhost:8080`. Copy the client id (under the app name) and secret.
2. **Discord bot**: discord.com/developers > New Application > Bot > Reset Token, copy it. Under OAuth2 > URL Generator tick `bot`, permissions View Channels, Send Messages, Read Message History, open the generated URL and invite it to a private server. Make a channel, copy its id and your own user id (Developer Mode on, right-click > Copy ID). Use a dedicated bot token for this process.
   (Telegram is a fallback if `DISCORD_BOT_TOKEN` is left empty.)
3. **Anthropic API key** from console.anthropic.com.
4. Copy `.env.example` to `.env` and fill it in. Never commit `.env`.
5. Copy `voice.example.md` to `voice.md` and paste in real samples of your own writing.
6. `pip install -r requirements.txt` (or run `setup.ps1` on Windows, which does steps 4 to 6 and opens the key pages).
7. `python run.py dry` to see what it would draft, with nothing sent or posted.
8. `python run.py status` to confirm the account connects and shows karma.

## Running it

- Windows Task Scheduler / cron: `python run.py morning` daily at 07:00.
- `python run.py worker` as a long running process (NSSM as a service on Windows, or a scheduled task).
- One instance per Reddit account, each with its own `.env` and its own `RM_DB`, so pacing is tracked per account. Do not run two instances on one account.

## Before you switch it on: the human parts

- Read the sidebar of every sub in `mention_allowed_subs` and `post_allowed_subs` yourself. The lists ship deliberately short. Add a sub only after reading its rules.
- If a subreddit's mods ask you to stop, add it to `banned_subs`. The tool then never reads from or posts to it. Never work around a community ban with another account.
- `r/SaaS` is in `mention_forbidden_subs` because of its one-mention-per-60-days rule. Leave it there.
- When the account is in a comments-only phase, set `post_allowed_subs: []`. Value posts stop, comments continue.

## Tuning knobs worth knowing

- `max_comments_on_thread`: lower = your answer sits higher, fewer threads qualify.
- `min_comments_in_sub_before_mention`: raise it in subs where you want more standing before ever naming your product.
- `max_comments_per_day`: 6 is already generous. Do not raise it.
- Add queries to `queries:` when you notice a phrase people use that you don't have.

## What it does not do, on purpose

- Upvote, follow, or DM. Vote manipulation and DM outreach from an automated pipeline are not acceptable on Reddit, and this tool has no code path for them.
- Reply to replies automatically. When someone answers your comment, it shows in `status` and in the morning inbox check; you reply yourself, from the app, in your own words.
- Post without a tap.
- Retain Reddit data. It stores only the IDs of threads already seen or replied to, and its own queue.
