"""Fetch an article page once and extract both the og:image and the body text
(the body feeds the details slide). Generic heuristics that work across all
four outlets; every step degrades gracefully."""
import base64
import re

from bs4 import BeautifulSoup

from . import config
from .feeds import http_get


def fetch_article(url: str) -> dict:
    """Returns {"og_image": str, "text": str}; empty strings on failure."""
    out = {"og_image": "", "text": ""}
    try:
        resp = http_get(url)
        if resp.status_code != 200:
            return out
        html = resp.text
    except Exception as e:
        print(f"  [warn] article fetch failed {url}: {e}")
        return out

    soup = BeautifulSoup(html, "html.parser")

    og = soup.find("meta", attrs={"property": "og:image"}) or \
         soup.find("meta", attrs={"name": "og:image"}) or \
         soup.find("meta", attrs={"name": "twitter:image"})
    if og and og.get("content"):
        out["og_image"] = og["content"].strip()

    # body text: prefer <article>/known body containers, else all decent <p>s
    scope = (soup.find("article")
             or soup.find(class_=re.compile(r"(article|news|post|details?)[-_]?(body|content|details)", re.I))
             or soup)
    paras = []
    for p in scope.find_all("p"):
        t = p.get_text(" ", strip=True)
        if len(t) >= 60 and not re.search(r"(copyright|all rights reserved|follow us)", t, re.I):
            paras.append(t)
    text = "\n".join(paras)
    if not text:
        d = soup.find("meta", attrs={"property": "og:description"}) or \
            soup.find("meta", attrs={"name": "description"})
        if d and d.get("content"):
            text = d["content"].strip()
    out["text"] = text[:5000]
    return out


def upgrade_thumb(url: str) -> str:
    # RSS feeds often ship small cache thumbs; nudge the common WordPress/CDN
    # patterns toward a larger render. No-op for URLs that don't match.
    u = url or ""
    u = re.sub(r"[?&](w|width|h|height|resize|fit)=[^&]+", "", u)  # strip size query params
    u = re.sub(r"-\d{2,4}x\d{2,4}(\.(?:jpe?g|png|webp))", r"\1", u)  # WP -300x200 suffix
    return u


def fetch_logo(domain: str) -> str:
    """Best-effort brand logo as a data URI, by domain. Tries Clearbit (clean
    transparent logos) then unavatar, then a high-res Google favicon. Returns
    "" if none work."""
    domain = (domain or "").strip().lower()
    if not domain or "." not in domain:
        return ""
    for url in (
        f"https://logo.clearbit.com/{domain}?size=512&format=png",
        f"https://unavatar.io/{domain}?fallback=false",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
    ):
        uri = fetch_as_data_uri(url)
        if uri:
            return uri
    return ""


def fetch_as_data_uri(image_url: str) -> str:
    """Download the image and inline it, so the headless renderer never
    depends on a CDN allowing hotlinks from a CI box."""
    if not image_url:
        return ""
    try:
        resp = http_get(image_url)
        if resp.status_code != 200 or not resp.content:
            return ""
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/") or len(resp.content) > 8_000_000:
            return ""
        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception as e:
        print(f"  [warn] image download failed: {e}")
        return ""
