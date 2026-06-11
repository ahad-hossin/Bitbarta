"""Central configuration. Everything tunable lives here or in env vars."""
import os

# Load a local .env file if present (for testing on your PC; Actions uses secrets)
_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_file):
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# --- Gemini ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY_2 = os.environ.get("GEMINI_API_KEY_2", "")  # backup keys
GEMINI_API_KEY_3 = os.environ.get("GEMINI_API_KEY_3", "")
GEMINI_API_KEYS = [k for k in (GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY_3) if k]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# tried in order when the primary model keeps returning 429/5xx
GEMINI_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get(
        "GEMINI_FALLBACK_MODELS", "gemini-2.0-flash,gemini-2.5-flash-lite"
    ).split(",") if m.strip()
]

# --- Posting volume ---
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "2"))
MAX_ITEM_AGE_HOURS = int(os.environ.get("MAX_ITEM_AGE_HOURS", "24"))

# --- API budgets (calls per UTC day unless noted) ---
# Gemini free tier is roughly 10 req/min and 250 req/day per model; budgets
# stay under that with margin for retries. Override any of these via env.
GEMINI_DEFAULT_DAILY_LIMIT = 150
GEMINI_DAILY_LIMITS = {
    "gemini-2.5-flash": int(os.environ.get("GEMINI_25_FLASH_DAILY", "230")),
    "gemini-2.0-flash": int(os.environ.get("GEMINI_20_FLASH_DAILY", "180")),
    "gemini-2.5-flash-lite": int(os.environ.get("GEMINI_25_LITE_DAILY", "900")),
}
GEMINI_MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "6.5"))  # sec between calls (10 RPM)

IG_DAILY_LIMIT = int(os.environ.get("IG_DAILY_LIMIT", "45"))    # Meta hard limit: 50 posts/24h
FB_DAILY_LIMIT = int(os.environ.get("FB_DAILY_LIMIT", "90"))    # generous self-imposed cap
X_DAILY_LIMIT = int(os.environ.get("X_DAILY_LIMIT", "16"))      # keeps X free tier viable
X_MONTHLY_LIMIT = int(os.environ.get("X_MONTHLY_LIMIT", "480")) # X free tier: ~500 writes/month

# --- Brand (shown on the rendered post image) ---
BRAND_NAME = os.environ.get("BRAND_NAME", "BITBARTA")
ACCENT = os.environ.get("ACCENT", "oklch(0.55 0.19 257)")  # Electric Blue (studio default)
BRAND_TZ_OFFSET_HOURS = int(os.environ.get("BRAND_TZ_OFFSET_HOURS", "6"))  # Bangladesh = UTC+6
# cover layout: "top" = headline above the image, "bottom" = headline below it
DETAILS_POSITION = os.environ.get("DETAILS_POSITION", "top")

# --- History / dedup ---
HISTORY_KEEP = 600          # entries kept in posted.json
HISTORY_FOR_DEDUP = 120     # recent topics shown to Gemini for dedup

# --- Sources (global tech-news RSS feeds) ---
# kind "rss" -> direct RSS/Atom feed. Every source below is English.
# "beat" is a coarse tag (press / dev / ai / gadgets) used only for logging.
SOURCES = [
    # Major EN tech press
    {"id": "techcrunch", "name": "TechCrunch", "kind": "rss",
     "url": "https://techcrunch.com/feed/", "lang": "en", "beat": "press"},
    {"id": "theverge", "name": "The Verge", "kind": "rss",
     "url": "https://www.theverge.com/rss/index.xml", "lang": "en", "beat": "press"},
    {"id": "arstechnica", "name": "Ars Technica", "kind": "rss",
     "url": "https://feeds.arstechnica.com/arstechnica/index", "lang": "en", "beat": "press"},
    {"id": "wired", "name": "Wired", "kind": "rss",
     "url": "https://www.wired.com/feed/rss", "lang": "en", "beat": "press"},
    {"id": "engadget", "name": "Engadget", "kind": "rss",
     "url": "https://www.engadget.com/rss.xml", "lang": "en", "beat": "press"},

    # Hacker News + developer
    {"id": "hackernews", "name": "Hacker News", "kind": "hn_algolia",
     "url": "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50", "lang": "en", "beat": "dev"},
    {"id": "devto", "name": "DEV", "kind": "rss",
     "url": "https://dev.to/feed", "lang": "en", "beat": "dev"},

    # AI-focused
    {"id": "venturebeat", "name": "VentureBeat", "kind": "rss",
     "url": "https://venturebeat.com/feed/", "lang": "en", "beat": "ai"},
    {"id": "techreview", "name": "MIT Technology Review", "kind": "rss",
     "url": "https://www.technologyreview.com/feed/", "lang": "en", "beat": "ai"},

    # Gadgets / consumer
    {"id": "ninetofivemac", "name": "9to5Mac", "kind": "rss",
     "url": "https://9to5mac.com/feed/", "lang": "en", "beat": "gadgets"},
    {"id": "androidpolice", "name": "Android Police", "kind": "rss",
     "url": "https://www.androidpolice.com/feed/", "lang": "en", "beat": "gadgets"},
    {"id": "tomshardware", "name": "Tom's Hardware", "kind": "rss",
     "url": "https://www.tomshardware.com/feeds/all", "lang": "en", "beat": "gadgets"},
]

# --- Featured-image source preference ---
# When one story is covered by several outlets, pull the featured photo from the
# outlet that ships the best imagery. Lower number = preferred (real editorial
# photos); higher = tends toward generic/abstract art or no image.
SOURCE_IMAGE_RANK = {
    "theverge": 0, "wired": 0, "arstechnica": 0,
    "techcrunch": 1, "engadget": 1, "ninetofivemac": 1, "androidpolice": 1, "tomshardware": 1,
    "techreview": 2,
    "hackernews": 5,   # links out; og:image varies
    "devto": 7,        # author-uploaded covers, often generic
    "venturebeat": 9,  # mostly abstract AI/tech art
}
SOURCE_IMAGE_RANK_DEFAULT = 4

# --- Social credentials (set as GitHub secrets; pipeline dry-runs without them) ---
IG_USER_ID = os.environ.get("IG_USER_ID", "")            # Instagram Business account ID
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")  # long-lived Page token (works for IG + FB)
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")

X_API_KEY = os.environ.get("X_API_KEY", "")
X_API_SECRET = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET", "")

# --- Paths ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "state", "posted.json")
QUEUE_FILE = os.path.join(ROOT, "state", "queue.json")
OUTPUT_DIR = os.path.join(ROOT, "output")
TEMPLATE_FILE = os.path.join(ROOT, "templates", "post.html")

# Public base URL for generated images (needed by Instagram, which fetches by
# URL). Images live on the orphan 'images' branch, which the workflow
# force-overwrites every run so old image blobs never pile up in git history.
def public_image_base() -> str:
    explicit = os.environ.get("PUBLIC_IMAGE_BASE", "")
    if explicit:
        return explicit.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/images"
    return ""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
