"""Deterministic style and policy checks. Run before and after the model editing pass.

Two jobs. (1) Keep drafts in the account owner's plain writing style: no assistant
phrasing, no markdown, no em dashes, no sign-offs, varied sentence length. (2) Enforce
the product mention policy: a mention is only allowed when permitted by config, must be
disclosed as the author's own tool, and fake-discovery phrasing is always rejected.
Returns a list of violations. Empty list = pass."""
import re
import statistics

EM_DASH = "\u2014"
EN_DASH = "\u2013"

BANNED_PHRASES = [
    "genuinely", "honestly", "delve", "dive into", "it's worth noting", "worth noting",
    "in today's", "game changer", "game-changer", "leverage", "navigate", "landscape",
    "robust", "seamless", "unlock", "elevate", "empower", "harness", "crucial", "vital",
    "pivotal", "foster", "streamline", "cutting edge", "cutting-edge", "tapestry",
    "testament", "hope this helps", "great question", "good question", "as an ai",
    "firstly", "secondly", "lastly", "in conclusion", "to summarise", "to summarize",
    "ultimately", "at the end of the day", "moreover", "furthermore", "additionally",
    "that being said", "with that said", "it's important to", "key takeaway", "pro tip",
    "quick tip", "as someone who", "cheers", "good luck", "let me know if",
    # assistant register: hedging, instructing, and filler openers
    "you'll want to", "you will want to", "you may want to", "i'd recommend",
    "i would recommend", "make sure to", "be sure to", "keep in mind", "note that",
    "when it comes to", "the key is", "one thing to", "a few things", "several factors",
    "at play here", "it depends on a few", "in essence", "essentially", "effectively",
    "simply put", "put simply", "rest assured", "worth considering", "consider whether",
    "this can help", "hopefully this", "happy to help", "feel free to",
    "rapidly evolving", "ever-evolving", "fast-paced", "double-edged",
    "not only", "but also", "whether you're", "if you're looking to",
    "there are several", "there are many", "a variety of", "a range of",
    "plays a role", "plays a key", "is essential", "is important to note",
]

# The voice is British. US spellings are a hard tell that a model wrote it.
US_SPELLINGS = [
    "favor", "favors", "favored", "color", "colors", "behavior", "behaviors",
    "optimize", "optimized", "optimizing", "optimization", "analyze", "analyzed",
    "organize", "organized", "organization", "recognize", "recognized",
    "prioritize", "prioritized", "customize", "customized", "utilize", "utilized",
    "center", "centers", "centered", "defense", "offense", "license plate",
    "traveling", "labeled", "canceled", "modeling", "catalog", "dialog",
    "maximize", "minimize", "summarize", "specialize", "leverages",
]
FAKE_DISCOVERY = ["found this tool", "came across", "stumbled upon", "stumbled across",
                  "there's a tool called", "there is a tool called", "i found a", "found a great"]
# A mention must make the commercial relationship visible. It does NOT have to be a
# heavy announcement: "(disclosure, that's us)" is four words and does the job. Any one
# of these satisfies the check, so the writer can pick whatever reads naturally.
DISCLOSURE_MARKERS = [
    "i built", "we built", "i made", "we made", "built this", "made this",
    "my tool", "our tool", "i work on", "we work on", "i work with", "we work with",
    "that's us", "thats us", "that's mine", "thats mine", "our thing", "we run",
    "i'm involved", "im involved", "i'm biased", "im biased", "obviously biased",
    "disclosure", "full disclosure", "for transparency", "our own", "we're behind",
    "were behind", "i help build", "we build",
]

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]")
MD_RE = re.compile(r"(^|\n)\s*(#{1,6}\s|\*\s|-\s|\d+\.\s|\*\*|__)")
COLON_LIST_RE = re.compile(r":\s*[^.\n]{3,}?,\s*[^.\n]{3,}?,?\s*(and|or)\s")


def sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if len(p.split()) > 0]


def check(text: str, kind: str = "comment", mention_allowed: bool = False, product_url: str = "") -> list:
    v = []
    low = text.lower()

    if EM_DASH in text: v.append("contains em dash")
    if EN_DASH in text: v.append("contains en dash")
    if EMOJI_RE.search(text): v.append("contains emoji")
    if kind == "comment" and MD_RE.search(text): v.append("markdown formatting in a comment")
    if COLON_LIST_RE.search(text): v.append("colon followed by an inline list")

    for p in BANNED_PHRASES:
        if p in low: v.append(f"banned phrase: '{p}'")
    for p in FAKE_DISCOVERY:
        if p in low: v.append(f"fake-discovery phrasing: '{p}' (must disclose, never pretend to have found it)")

    for w in US_SPELLINGS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            v.append(f"US spelling '{w}' (the voice is British)")

    # openers that announce an answer instead of giving one
    first = (sentences(text) or [""])[0].lower()
    for opener in ("great ", "good ", "that's a ", "thats a ", "this is a common",
                   "i think ", "so ", "well, ", "ah, ", "ah ", "yes, ", "absolutely",
                   "definitely", "certainly", "sure, "):
        if first.startswith(opener):
            v.append(f"opens with filler ('{first[:28]}...'), start with the answer")
            break
    if first.rstrip("?").endswith(("mean", "asking", "wondering")) and first.endswith("?"):
        v.append("opens by restating the question")

    # the "rule of three" cadence models fall into
    if re.search(r"\b(first(ly)?|one)\b.{10,160}\b(second(ly)?|two)\b.{10,160}\b(third(ly)?|three|final(ly)?|last(ly)?)\b", low, re.S):
        v.append("first/second/third scaffolding")

    words = len(text.split())
    if kind == "comment" and not (35 <= words <= 130): v.append(f"comment length {words} words, need 40-120")
    if kind == "post" and not (150 <= words <= 500): v.append(f"post length {words} words, need 180-450")

    s = sentences(text)
    if len(s) >= 4:
        lens = [len(x.split()) for x in s]
        if statistics.pstdev(lens) < 3.0: v.append("sentence lengths too uniform (std < 3)")
    if text.count(";") > 1: v.append("too many semicolons")

    # three parallel items with matching leading word pattern e.g. "Check X. Check Y. Check Z."
    starts = [x.split()[0].lower() for x in s if x.split()]
    for i in range(len(starts) - 2):
        if starts[i] == starts[i + 1] == starts[i + 2]:
            v.append("three consecutive sentences start with the same word")
            break

    # sign-off detection: last sentence very short and generic
    if s and len(s[-1].split()) <= 4 and any(k in s[-1].lower() for k in ["thanks", "cheers", "luck", "helps", "hth"]):
        v.append("ends with a sign-off")

    # product mention policy
    mentioned = bool(product_url) and product_url.lower() in low
    if mentioned and not mention_allowed:
        v.append("product mentioned but mention not allowed for this draft")
    if mentioned and not any(m in low for m in DISCLOSURE_MARKERS):
        v.append("product mentioned without disclosure (make the connection visible, e.g. 'disclosure, that's us')")
    if mentioned:
        idx = low.rfind(product_url.lower())
        if idx < len(low) * 0.6: v.append("product mention must come at the end, after the full manual answer")

    return v
