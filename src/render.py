"""Render post images (1080x1350 PNG) from the Sandesh-design template using
headless Chromium. Each post gets a cover slide and, when details paragraphs
exist, a story/details slide."""
import os
import pathlib
import re
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

from . import config

BRAND_TZ = timezone(timedelta(hours=config.BRAND_TZ_OFFSET_HOURS))


def _slug(text: str, n: int = 44) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "post").lower()).strip("-")
    return s[:n] or "post"


def _shoot(page, data, fn, selector, out_path):
    page.evaluate("window.__POST_READY__ = false")
    page.evaluate(f"data => window.{fn}(data)", data)
    page.wait_for_function("window.__POST_READY__ === true", timeout=30000)
    page.locator(selector).screenshot(path=out_path)


def render_posts(posts: list) -> list:
    """Adds 'image_files' (filenames inside output/, cover first) to each post."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    template_url = pathlib.Path(config.TEMPLATE_FILE).as_uri()
    today = datetime.now(BRAND_TZ)  # the date readers see should be local time
    date_label = today.strftime("%d %b %Y").upper()
    stamp = today.strftime("%Y%m%d-%H%M")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1500})
        for i, post in enumerate(posts):
            base = f"{stamp}-{i}-{_slug(post.get('headline'))}"
            files = []

            page.goto(template_url, wait_until="domcontentloaded")
            cover = {
                "template": post.get("template", "neon"),
                "pos": config.DETAILS_POSITION,
                "accent": config.ACCENT,
                "brand": config.BRAND_NAME,
                "category": post.get("category", "TECH"),
                "title": post.get("headline_marked") or post.get("headline", ""),
                "desc": post.get("summary", ""),
                "date": date_label,
                "source": post.get("source", ""),
                "image": post.get("image_data_uri", ""),
                "credit": f"Photo: {post.get('photo_credit') or post['source'].split(',')[0]}" if post.get("image_data_uri") else "",
            }
            _shoot(page, cover, "renderPost", "#post", os.path.join(config.OUTPUT_DIR, base + ".png"))
            files.append(base + ".png")

            if post.get("details"):
                story_base = {
                    "template": post.get("template", "neon"),
                    "accent": config.ACCENT,
                    "brand": config.BRAND_NAME,
                    "category": post.get("category", "TECH"),
                    "date": date_label,
                    "source": post.get("source", ""),
                }
                # plan pass: how many paragraphs fit on each details page
                # (Instagram carousels max out at 10 slides: cover + 9 details)
                slices, remaining = [], list(post["details"])
                while remaining and len(slices) < 9:
                    page.evaluate("window.__POST_READY__ = false")
                    fitted = page.evaluate(
                        "data => window.renderStory(data)",
                        {**story_base, "paragraphs": remaining},
                    )
                    fitted = max(int(fitted or 1), 1)
                    slices.append(remaining[:fitted])
                    remaining = remaining[fitted:]
                # shoot pass: render each page with its final "n/total" label
                total = 1 + len(slices)
                for n, sl in enumerate(slices, start=2):
                    data = {**story_base, "paragraphs": sl, "page": f"{n}/{total}"}
                    fname = f"{base}-d{n - 1}.png"
                    _shoot(page, data, "renderStory", "#story", os.path.join(config.OUTPUT_DIR, fname))
                    files.append(fname)

            post["image_files"] = files
            print(f"  rendered {' + '.join(files)}")
        browser.close()
    return posts


def cleanup_old_images(keep_days: int = 14) -> None:
    if not os.path.isdir(config.OUTPUT_DIR):
        return
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    for f in os.listdir(config.OUTPUT_DIR):
        p = os.path.join(config.OUTPUT_DIR, f)
        if f.endswith(".png") and os.path.getmtime(p) < cutoff:
            os.remove(p)
