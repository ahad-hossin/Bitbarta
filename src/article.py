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


def _img_min_dim(content: bytes) -> int:
    """Smaller of (width, height) for PNG/JPEG/GIF, parsed from the header
    without PIL. Returns 0 if the format/size can't be determined."""
    try:
        if content[:8] == b"\x89PNG\r\n\x1a\n" and content[12:16] == b"IHDR":
            return min(int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big"))
        if content[:2] == b"\xff\xd8":  # JPEG: find a start-of-frame marker
            i = 2
            while i < len(content) - 9:
                if content[i] != 0xFF:
                    i += 1
                    continue
                if content[i + 1] in (0xC0, 0xC1, 0xC2, 0xC3):
                    return min(int.from_bytes(content[i + 5:i + 7], "big"),
                               int.from_bytes(content[i + 7:i + 9], "big"))
                i += 2 + int.from_bytes(content[i + 2:i + 4], "big")
        if content[:6] in (b"GIF87a", b"GIF89a"):
            return min(int.from_bytes(content[6:8], "little"), int.from_bytes(content[8:10], "little"))
    except Exception:
        pass
    return 0


_LOGO_MIN_DIM = 384  # upscaling a smaller logo into the big frame looks blurry


def fetch_logo(domain: str) -> str:
    """Best-effort, HIGH-RES brand logo as a data URI, by domain. Tries Clearbit
    (clean transparent logos) then unavatar. Rejects low-res images so we never
    upscale a tiny favicon into a blurry mess. SVG (vector) always accepted.
    Returns "" if no crisp logo is found (caller then goes text-led)."""
    domain = (domain or "").strip().lower()
    if not domain or "." not in domain:
        return ""
    for url in (
        f"https://logo.clearbit.com/{domain}?size=512&format=png",
        f"https://unavatar.io/{domain}?fallback=false",
    ):
        try:
            resp = http_get(url)
        except Exception:
            continue
        if resp.status_code != 200 or not resp.content:
            continue
        ctype = resp.headers.get("Content-Type", "").split(";")[0]
        if not ctype.startswith("image/") or len(resp.content) > 8_000_000:
            continue
        dim = _img_min_dim(resp.content)
        ok = ctype == "image/svg+xml" or dim >= _LOGO_MIN_DIM  # vector, or genuinely hi-res
        if not ok:
            continue
        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
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
