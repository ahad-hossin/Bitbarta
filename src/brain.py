"""Gemini does the thinking, in two phases.

Phase 1 (one call): look at every fresh tech headline, cluster duplicates
across outlets, drop topics already posted, pick the top stories.

Phase 2 (one call per selected story): read the article's actual body text and
write the post — headline with a [[highlighted]] key phrase, summary, the
details-slide paragraphs, caption and tweet, all in English."""
import json
import time

import requests

from . import budget, config

_last_call = 0.0

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "indices of ALL candidates covering this same story",
                    },
                    "topic": {"type": "string", "description": "short unique English topic key, e.g. 'openai launches gpt-5'"},
                },
                "required": ["candidate_ids", "topic"],
            },
        }
    },
    "required": ["stories"],
}

_SELECT_PROMPT = """You are the editor of "{brand}", a TECH NEWS page that posts in ENGLISH on Instagram, Facebook and X.

Below are fresh candidate stories from major global tech outlets, plus topics we already posted.

1. CLUSTER candidates covering the SAME story (a big launch or acquisition appears on several outlets at once). One cluster = one post.
2. DROP any story we already posted (see list). Same event = duplicate even if worded differently. This INCLUDES new stages of one story we already covered (rumor -> announced -> released -> reactions / benchmarks are ALL one story, not four). Only re-cover an ongoing story if the development is itself a major standalone event (a shock pricing reversal, a recall, a launch after we only covered the rumor) — and even then, at most once.
3. SELECT the {max_posts} most ENGAGING and FRESH stories — these two things decide everything:
   a) ENGAGEMENT (primary): pick the stories tech-interested people are most likely to share, comment on and react to. Strongest signals, roughly in order:
      - AI breakthroughs and big model/feature releases (OpenAI, Anthropic, Google, Meta, etc.)
      - major product launches and updates with real "wow" (phones, chips, GPUs, OS, flagship apps)
      - big-tech drama: acquisitions, layoffs, lawsuits, antitrust, leadership shake-ups, earnings shocks
      - security: major breaches, hacks, zero-days, outages affecting millions
      - surprising benchmarks, prices, funding rounds, first-of-its-kind tech, or a jaw-dropping fact/number
      - a story covered by several outlets at once is a strong virality signal
   b) FRESHNESS: strongly prefer what just broke. Look at the published time — favor stories from the last few hours, and between two comparably interesting stories ALWAYS take the newer one. A story already a day old needs to be clearly more engaging to beat a brand-new one.
   Pick the single most share-worthy, most recent stories available — never settle for a dull or stale item just to fill the quota. Skip sponsored posts, deals/coupon roundups, "best X" listicles, opinion/how-to teasers, minor point-releases and routine trivia. Fewer than {max_posts} — or zero — is fine if nothing is genuinely share-worthy.
4. For each selected story, build the cluster around its MOST ENGAGING ANGLE: if several related items are bundled (e.g. many iOS 27 features), lead with the single most viral one (the surprising Siri behaviour), not the dullest (a recovery-mode tweak). Give each story a short English topic key for future dedup.

RECENTLY POSTED TOPICS (do not repeat):
{history}

CANDIDATES (index | source | lang | published | category | title | snippet):
{candidates}
"""

_COMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "English headline, max 95 chars, with the core message — the part that alone tells the story — wrapped in [[ ]], e.g. '[[OpenAI launches GPT-5]] with real-time video, rolling out today'"},
        "summary": {"type": "string", "description": "1-2 English sentences, max 220 chars, for the cover image subtext"},
        "category": {"type": "string", "description": "one word: AI, APPLE, ANDROID, GADGETS, HARDWARE, SOFTWARE, SECURITY, GAMING, STARTUPS, BIGTECH, SCIENCE, CRYPTO, ..."},
        "template": {"type": "string", "enum": ["launch", "neon", "leak", "terminal"]},
        "details": {
            "type": "array",
            "items": {"type": "string"},
            "description": "short English paragraphs (2-3 sentences each) telling the full story — as many as the story needs, typically 4-10. They flow across the details slides.",
        },
        "hook": {"type": "string", "description": "1-2 punchy factual lines that open the caption — what shows before '...more', impossible to scroll past"},
        "hashtags": {"type": "string", "description": "4-6 widely-used, non-restricted hashtags separated by spaces, mixing broad reach (#Tech #AI) with story-specific tags (#OpenAI #GPT5)"},
        "tweet": {"type": "string", "description": "standalone X post, max 270 chars incl. 1-3 hashtags"},
        "story_risk": {"type": "string", "enum": ["clean", "sensitive", "graphic", "do_not_post"]},
        "image_safe": {"type": "boolean", "description": "false if the attached photo shows blood, corpses, graphic injury, weapons in use, or nudity; true otherwise or when no photo is attached"},
        "image_good": {"type": "boolean", "description": "Keep the photo unless it is genuinely unusable. true for real photos, product shots/renders, screenshots, headshots, charts, AND any relevant illustration or graphic (tech/AI artwork is fine — an on-topic image beats none). false ONLY for: a plain logo/wordmark on a blank background, a blank/placeholder/solid-colour image, a heavily watermarked or tiny/broken image, or an image with no connection to the story. When unsure, keep it (true). true when no photo is attached."},
    },
    "required": ["headline", "summary", "category", "template", "details", "hook", "hashtags", "tweet", "story_risk", "image_safe", "image_good"],
}

_COMPOSE_PROMPT = """You are the editor of "{brand}", a TECH NEWS page that posts in ENGLISH.

Write the social post for this story. Make it as engaging as possible — but FACTS ONLY: never invent, exaggerate or editorialize beyond what the material below supports. No clickbait the article can't back up, and don't overstate specs, prices, dates or benchmarks. Keep tech terms accurate; spell product and company names exactly right.

Headline rules: scroll-stopping, concrete and factual, max 95 chars. Lead with the most striking fact, spec or number. Wrap the headline's CORE MESSAGE in [[ ]] — the contiguous phrase (typically 4-8 words, can be half the headline) that on its own tells the viewer what happened, so reading just the highlight gives the main point and the rest adds context. Never highlight a fragment that's meaningless alone, and never highlight the entire headline.
Summary rules: the second punch — the detail (a price, a date, a spec, a catch) that makes people need to know more.
Template rules — each template is a VISUAL STYLE matched to a kind of story. Go down this list and pick the FIRST one that fits; if none of 1-3 clearly fit, use "neon" (4). Pick by the story's nature, not by how big it is.
1. "terminal" — code/terminal window. Use for anything DEVELOPER- or CODE-flavored: programming languages, frameworks, SDKs, APIs, open-source projects, developer tools, AND all security stories — breaches, hacks, data leaks (security sense), zero-days/vulnerabilities, malware, ransomware, outages, cloud/infrastructure, Linux. (e.g. "Apple opens its Foundation Models framework to any LLM", "Critical OpenSSH zero-day patched", "AWS outage takes down half the web".)
2. "leak" — blurred background, stamp. Use ONLY for UNCONFIRMED information: leaks, rumors, "reportedly", "sources say", "allegedly", early/leaked renders, speculation about unannounced products. If it's officially confirmed, it is NOT a leak. (e.g. "iPhone 18 Pro reportedly drops the notch entirely, leak suggests".)
3. "launch" — clean, light keynote look. Use for OFFICIAL, CONFIRMED launches and releases: a new device/chip/app/OS/feature is announced, unveiled, released or now available. Positive, polished, official product news. (e.g. "Apple unveils the foldable iPhone with a crease-free display", "Spotify rolls out lossless audio to all users".)
4. "neon" — futuristic dark grid. The DEFAULT for everything else: AI model/research news, product reviews/benchmarks, general tech and business news, funding rounds, partnerships, company moves, policy, lawsuits, market trends, analysis, and any story that doesn't clearly match 1-3.
Details rules: short paragraphs (2-3 sentences each) telling the FULL story — what/who/specs/price/availability/why it matters/what's next. Use as many paragraphs as the story needs (typically 4-10); they flow across the details slides of the carousel. Put the most gripping facts (the key spec, price or number) in the first paragraph.
Hook rules: 1-2 lines that open the caption — it's all people see before "...more", so make it impossible to scroll past (a striking spec, number or question; still factual). The full story details follow it automatically, so don't repeat them.
Tweet rules: standalone, lead with the hook, under 270 chars, 1-3 hashtags.

Platform safety (this page must never violate Facebook/Instagram policies):
- Stay neutral and factual; don't make unverified accusations against named people or companies. Attribute claims (leaks, rumors, "according to <source>") rather than stating them as confirmed fact. Don't post how-to instructions for exploiting a vulnerability — report that it exists and its impact.
- story_risk: "clean" for normal tech news; "sensitive" for major breaches, harassment, or stories with unproven allegations (word carefully and attribute); "graphic" is rare here (only genuinely disturbing content); "do_not_post" ONLY if the story cannot be covered without violating platform policy (explicit content, doxxing, actionable hacking instructions).
- Never mask words with symbols or slang: masking looks spammy and platforms detect it anyway. Keep wording neutral and the story stays postable.
- image_safe: a photo may be attached to this message. Set image_safe=false only if it shows nudity, gore or otherwise clearly violates Meta's image policy. Product shots, screenshots, logos and headshots are safe. If no photo is attached, set true.

Image quality (image_good): every post should carry an image, so KEEP the attached photo unless it is genuinely unusable. Set image_good=true for real photographs, product photos/renders, screenshots, headshots, charts, AND any relevant illustration or graphic (stylised tech/AI artwork is perfectly fine — a relevant image beats an empty post). Set image_good=false ONLY for a plain logo or wordmark on a blank background, a blank/placeholder/solid-colour image, a heavily-watermarked or tiny/broken image, or an image with no connection at all to the story. When in doubt, keep it (true). If no photo is attached, set image_good=true.

STORY HEADLINES (from the outlets):
{titles}

ARTICLE TEXT (may be partial; primary source: {primary_source}):
{article}
"""


def _call_gemini(parts: list, schema: dict) -> dict:
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    global _last_call
    models = [config.GEMINI_MODEL] + config.GEMINI_FALLBACK_MODELS
    keys = config.GEMINI_API_KEYS
    last_err = None
    # try the backup key on the best model before degrading to a lesser model
    for model in models:
        limit = config.GEMINI_DAILY_LIMITS.get(model, config.GEMINI_DEFAULT_DAILY_LIMIT)
        for ki, api_key in enumerate(keys):
            pair = budget.gemini_pair_key(ki, model)
            if budget.remaining(pair, limit) <= 0:
                print(f"  [budget] {model} (key {ki + 1}): daily budget used up, trying next")
                continue
            for attempt in range(4):
                if attempt:
                    wait = 2 ** attempt  # 2, 4, 8s
                    print(f"  [warn] Gemini {last_err}, retry {model} (key {ki + 1}) in {wait}s...")
                    time.sleep(wait)
                # respect the free tier's requests-per-minute ceiling
                gap = config.GEMINI_MIN_INTERVAL - (time.time() - _last_call)
                if gap > 0:
                    time.sleep(gap)
                _last_call = time.time()
                resp = requests.post(
                    _ENDPOINT.format(model=model),
                    params={"key": api_key},
                    json=body,
                    timeout=120,
                )
                budget.spend(pair)
                if resp.status_code == 429:
                    # could be the per-minute limit, not the daily one: cool
                    # off and retry once before benching this key+model pair
                    if attempt == 0:
                        print(f"  [warn] HTTP 429 on {model} (key {ki + 1}), cooling off 35s...")
                        time.sleep(35)
                        last_err = f"HTTP 429 on {model} (key {ki + 1})"
                        continue
                    budget.exhaust(pair, limit)
                    last_err = f"HTTP 429 on {model} (key {ki + 1}, persistent)"
                    break
                if resp.status_code == 403:
                    # usually Google momentarily flagging the CI runner's IP
                    # or this key — move to the next key, don't crash and
                    # don't bench the budget (it usually recovers)
                    last_err = f"HTTP 403 on {model} (key {ki + 1})"
                    break
                if resp.status_code in (500, 502, 503, 504):
                    last_err = f"HTTP {resp.status_code} on {model}"
                    continue
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code} on {model} (key {ki + 1})"
                    break
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
    raise RuntimeError(f"Gemini unavailable after retries ({last_err})")


def select_stories(candidates: list, history: list) -> list:
    """Phase 1 -> [{cluster: [items], topic: str}], newest stories first."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")

    recent = [e for e in history if e.get("topic")][-config.HISTORY_FOR_DEDUP:]
    history_lines = "\n".join(
        f"- {e['topic']} ({e.get('headline', '')})" for e in recent
    ) or "(nothing posted yet)"
    cand_lines = "\n".join(
        f"{i} | {c['source']} | {c['lang']} | {c['published']} | {c.get('category','')} | {c['title']} | {c['description'][:140]}"
        for i, c in enumerate(candidates)
    )
    prompt = _SELECT_PROMPT.format(
        brand=config.BRAND_NAME,
        max_posts=config.MAX_POSTS_PER_RUN,
        history=history_lines,
        candidates=cand_lines,
    )
    result = _call_gemini([{"text": prompt}], _SELECT_SCHEMA)
    stories = []
    for s in result.get("stories", [])[: config.MAX_POSTS_PER_RUN]:
        ids = [i for i in s.get("candidate_ids", []) if 0 <= i < len(candidates)]
        if not ids:
            continue
        stories.append({"cluster": [candidates[i] for i in ids], "topic": s.get("topic", "")})
    return stories


_CAPTION_MAX = 2100  # Instagram allows 2200; keep margin


def _build_caption(hook: str, details: list, hashtags: str, sources: str) -> str:
    """Hook -> full story details -> hashtags -> source credit. Used as-is on
    both Instagram and Facebook; truncated at a paragraph boundary if the full
    story would blow Instagram's caption limit."""
    tail = f"\n\n{hashtags.strip()}\n\nSource: {sources}".rstrip()
    body = hook.strip()
    for para in details:
        candidate = f"{body}\n\n{para}"
        if len(candidate) + len(tail) > _CAPTION_MAX:
            break
        body = candidate
    return body + tail


def compose_post(story: dict, article_text: str, image_data_uri: str = "") -> dict:
    """Phase 2 -> full post content for one selected story. The candidate
    photo rides along in the same request so Gemini safety-checks it for
    free (no extra API call)."""
    cluster = story["cluster"]
    primary = next((c for c in cluster if c["lang"] == "en"), cluster[0])
    titles = "\n".join(f"- [{c['source']}] {c['title']}" for c in cluster)
    prompt = _COMPOSE_PROMPT.format(
        brand=config.BRAND_NAME,
        titles=titles,
        primary_source=primary["source"],
        article=article_text[:4500] or "(article text unavailable — use only the headlines)",
    )
    parts = [{"text": prompt}]
    if image_data_uri.startswith("data:"):
        header, b64 = image_data_uri.split(",", 1)
        mime = header.split(":", 1)[1].split(";", 1)[0]
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    p = _call_gemini(parts, _COMPOSE_SCHEMA)
    details = [d.strip() for d in p.get("details", []) if d.strip()][:14]
    marked = p["headline"][:130]
    sources = ", ".join(dict.fromkeys(c["source"] for c in cluster))
    caption = _build_caption(p.get("hook", ""), details, p.get("hashtags", ""), sources)
    return {
        "topic": story["topic"],
        "headline_marked": marked,                      # with [[highlight]] for the image
        "headline": marked.replace("[[", "").replace("]]", ""),
        "summary": p["summary"][:260],
        "category": (p.get("category") or "TECH").upper()[:18],
        "template": p.get("template", "neon"),
        "details": details,
        "caption": caption,
        "tweet": p["tweet"][:275],
        "story_risk": p.get("story_risk", "clean"),
        "image_safe": bool(p.get("image_safe", True)),
        "image_good": bool(p.get("image_good", True)),
        "source": sources,
        "url": primary["url"],
        "image": primary.get("image", ""),
        "orig_title": primary["title"],
        "cluster_urls": [c["url"] for c in cluster],
        "cluster_titles": [c["title"] for c in cluster],
    }
