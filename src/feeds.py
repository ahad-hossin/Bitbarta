"""Fetch and normalize tech-news items straight from each outlet's RSS feed.

Every source is a standard RSS/Atom feed (see config.SOURCES). Requests go
through cloudscraper so the occasional Cloudflare/bot wall doesn't block a feed.
"""
import calendar
import re
from datetime import datetime, timedelta, timezone

import cloudscraper
import feedparser

from . import config

_scraper = cloudscraper.create_scraper()
MAX_PER_SOURCE = 80


def http_get(url: str):
    last = None
    for attempt in range(2):
        try:
            return _scraper.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=45)
        except Exception as e:
            last = e
    raise last


def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=config.MAX_ITEM_AGE_HOURS)


def _item(src, title, url, when, desc="", image="", category=""):
    return {
        "source_id": src["id"],
        "source": src["name"],
        "lang": src["lang"],
        "title": re.sub(r"\s+", " ", title).strip(),
        "url": url.strip(),
        "published": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": desc,
        "image": image,
        "category": category,
    }


def _entry_image(entry) -> str:
    """Best image we can pull out of an RSS entry, trying the common spots."""
    media = entry.get("media_content") or []
    if media and media[0].get("url"):
        return media[0]["url"]
    thumb = entry.get("media_thumbnail") or []
    if thumb and thumb[0].get("url"):
        return thumb[0]["url"]
    for enc in entry.get("enclosures") or []:
        if str(enc.get("type", "")).startswith("image/") and enc.get("href"):
            return enc["href"]
    for blob in (entry.get("summary", ""), entry.get("content", [{}])[0].get("value", "")
                 if entry.get("content") else ""):
        m = re.search(r'<img[^>]+src="([^"]+)"', blob or "")
        if m:
            return m.group(1)
    return ""


def _fetch_rss(src) -> list:
    parsed = feedparser.parse(http_get(src["url"]).content)
    items = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        t = entry.get("published_parsed") or entry.get("updated_parsed")
        when = (datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
                if t else datetime.now(timezone.utc))
        if when < _cutoff():
            continue
        desc = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or "")
        desc = re.sub(r"\s+", " ", desc).strip()[:400]
        items.append(_item(src, title, link, when, desc, _entry_image(entry)))
    return items[:MAX_PER_SOURCE]


# ---------- Hacker News (official Algolia API — one JSON request) ----------

def _fetch_hn_algolia(src) -> list:
    data = http_get(src["url"]).json()
    items = []
    for hit in data.get("hits", []):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        # external link if the post has one, else the HN discussion thread
        url = (hit.get("url") or "").strip() or \
            f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        ts = hit.get("created_at_i")
        when = (datetime.fromtimestamp(ts, tz=timezone.utc) if ts
                else datetime.now(timezone.utc))
        if when < _cutoff():
            continue
        desc = f"{hit.get('points') or 0} points, {hit.get('num_comments') or 0} comments on Hacker News"
        items.append(_item(src, title, url, when, desc))
        if len(items) >= MAX_PER_SOURCE:
            break
    return items


_FETCHERS = {
    "rss": _fetch_rss,
    "hn_algolia": _fetch_hn_algolia,
}


def interleave_cap(items: list, cap: int = 150) -> list:
    """Round-robin across sources so no single outlet crowds the others out of
    the candidate window."""
    by_src = {}
    for it in items:
        by_src.setdefault(it["source_id"], []).append(it)
    queues = list(by_src.values())
    out = []
    while len(out) < cap and any(queues):
        for q in queues:
            if q and len(out) < cap:
                out.append(q.pop(0))
    return out


def fetch_all() -> list:
    all_items = []
    for src in config.SOURCES:
        try:
            items = _FETCHERS[src["kind"]](src)
        except Exception as e:
            print(f"  [warn] {src['name']}: fetch failed: {e}")
            items = []
        print(f"  {src['name']}: {len(items)} fresh items")
        all_items.extend(items)
    all_items.sort(key=lambda x: x["published"], reverse=True)
    seen, out = set(), []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
    return out
