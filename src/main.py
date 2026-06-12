"""Orchestrator.

  python -m src.main generate   scrape -> Gemini select/dedup/caption -> render PNGs -> queue
  python -m src.main publish    post the queue to IG/FB/X (dry-run without credentials)

The workflow runs generate, commits the images (so Instagram can fetch them by
raw URL), then runs publish and commits the updated state."""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Bangla titles on Windows consoles

from . import article, brain, budget, config, feeds, render, state


def _summary(lines: list) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines)
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def _pick_image(cluster: list, art_cache: dict):
    """Pick the featured photo from the cluster, preferring outlets known for
    real editorial imagery over ones that ship generic/abstract art. Tries each
    outlet (best-ranked first) by RSS thumbnail then article og:image, and
    returns the first that downloads — so e.g. Ars Technica's real header beats
    VentureBeat's abstract illustration. Returns (data_uri, credit)."""
    ranked = sorted(
        cluster,
        key=lambda c: config.SOURCE_IMAGE_RANK.get(c["source_id"], config.SOURCE_IMAGE_RANK_DEFAULT),
    )
    for c in ranked:
        urls = []
        if c.get("image"):
            urls.append(article.upgrade_thumb(c["image"]))
        if c["url"] not in art_cache:
            art_cache[c["url"]] = article.fetch_article(c["url"])
        og = art_cache[c["url"]].get("og_image")
        if og:
            urls.append(og)
        for u in urls:
            uri = article.fetch_as_data_uri(u)
            if uri:
                return uri, c["source"]
    return "", ""


def generate() -> int:
    # --- budget check: each run costs 1 Gemini call to select + 1 per post ---
    print(f"API budgets: {budget.summary()}")
    gem_left = budget.gemini_remaining_total()
    if config.GROQ_API_KEY:
        # Groq backs us up, so a spent Gemini budget never blocks or caps a run
        if gem_left < 2:
            print("[budget] Gemini budget low — Groq will handle this run")
    else:
        if gem_left < 2:
            _summary(["## News bot", f"Skipped: Gemini daily budget exhausted ({budget.summary()})"])
            state.save_queue([])
            return 0
        affordable = min(config.MAX_POSTS_PER_RUN, gem_left - 1)
        if affordable < config.MAX_POSTS_PER_RUN:
            print(f"[budget] capping run at {affordable} post(s) (Gemini calls left: {gem_left})")
            config.MAX_POSTS_PER_RUN = affordable

    print("== Fetching feeds ==")
    candidates = feeds.fetch_all()
    print(f"Total fresh candidates: {len(candidates)}")

    history = state.load_history()
    seen = state.seen_keys(history)
    fresh = [
        c for c in candidates
        if c["url"] not in seen and state.title_hash(c["title"]) not in seen
    ]
    print(f"After exact-dedup against history: {len(fresh)}")
    if not fresh:
        _summary(["## News bot", "No new candidates this run."])
        state.save_queue([])
        return 0

    print("== Phase 1: Gemini selects and dedupes stories ==")
    try:
        stories = brain.select_stories(feeds.interleave_cap(fresh, 90), history)
    except Exception as e:
        # a Gemini outage shouldn't fail the whole run — skip this hour
        _summary(["## News bot", f"Skipped this hour: Gemini unavailable ({e})"])
        state.save_queue([])
        return 0
    print(f"Gemini selected {len(stories)} story(ies)")
    if not stories:
        _summary(["## News bot", "Gemini selected no stories this run."])
        state.save_queue([])
        return 0

    print("== Phase 2: fetch articles, compose posts ==")
    posts = []
    for idx, story in enumerate(stories):
        cluster = story["cluster"]
        primary = next((c for c in cluster if c["lang"] == "en"), cluster[0])
        art = article.fetch_article(primary["url"])
        art_cache = {primary["url"]: art}
        # fall back to another cluster member's page if the primary gave nothing
        if not art["text"] and len(cluster) > 1:
            for c in cluster:
                if c["url"] != primary["url"]:
                    alt = article.fetch_article(c["url"])
                    art_cache[c["url"]] = alt
                    if alt["text"]:
                        art = alt
                        break
        # featured photo: pick the best image across the cluster (preferring
        # outlets with real editorial photos), so the compose call can also
        # safety-check it for free
        image_uri, photo_credit = _pick_image(cluster, art_cache)

        try:
            post = brain.compose_post(story, art["text"], image_uri)
        except Exception as e:
            print(f"  [warn] compose failed for '{story['topic']}': {e}")
            continue

        # deterministic dedup backstop — checks BOTH past posts (history) and the
        # posts already accepted earlier in THIS run, since the model sometimes
        # returns the same story as two separate clusters in one run
        run_seen = history + [{"headline": q["headline"], "topic": q["topic"]} for q in posts]
        if state.is_duplicate(post["headline"], post["topic"], run_seen):
            print(f"  [dedup] skipping duplicate of an earlier story: {post['headline'][:60]}")
            continue

        # content safety verdicts (came back in the same compose call)
        if post["story_risk"] == "do_not_post":
            print(f"  [policy] skipping '{post['headline'][:60]}' (cannot be covered within platform rules)")
            state.record(history, post, "policy-skip")
            for url, title in zip(post.get("cluster_urls", []), post.get("cluster_titles", [])):
                if url != post["url"]:
                    state.record(history, {"orig_title": title, "url": url,
                                           "headline": post["headline"], "topic": post["topic"],
                                           "source": post["source"]}, "policy-skip")
            continue
        if image_uri and (post["story_risk"] == "graphic" or not post["image_safe"]):
            print(f"  [policy] dropping photo for '{post['headline'][:60]}' (risk={post['story_risk']}, image_safe={post['image_safe']})")
            image_uri, photo_credit = "", ""
        if image_uri and not post.get("image_good", True):
            print(f"  [image] dropping weak/generic photo for '{post['headline'][:60]}' — rendering text-led")
            image_uri, photo_credit = "", ""

        post["image_data_uri"] = image_uri
        post["photo_credit"] = photo_credit
        print(f"  {post['headline'][:60]}... photo={'yes' if image_uri else 'no'}, risk={post['story_risk']}, details={len(post['details'])} paras")
        posts.append(post)

    if not posts:
        state.save_history(history)  # policy-skips must be remembered too
        _summary(["## News bot", "No posts could be composed this run."])
        state.save_queue([])
        return 0

    print("== Rendering post images ==")
    render.cleanup_old_images()
    render.render_posts(posts)

    # the queue holds everything publish needs; data URIs are too big to keep
    queue = []
    for p in posts:
        queue.append({k: v for k, v in p.items() if k != "image_data_uri"})
        # record every clustered title/url so other outlets' copies of the
        # same story are also recognized as posted
        state.record(history, p, "queued")
        for url, title in zip(p.get("cluster_urls", []), p.get("cluster_titles", [])):
            if url != p["url"]:
                state.record(history, {"orig_title": title, "url": url,
                                       "headline": p["headline"], "topic": p["topic"],
                                       "source": p["source"]}, "queued")
    state.save_queue(queue)
    state.save_history(history)

    lines = ["## News bot — generated", ""]
    for p in posts:
        lines.append(f"- **{p['headline']}** ({p['category']}, {p['template']}) — {p['source']}")
    lines += ["", f"Budgets after run: {budget.summary()}"]
    _summary(lines)
    return 0


def publish() -> int:
    from . import publish as pub

    queue = state.load_queue()
    if not queue:
        print("Queue is empty, nothing to publish.")
        return 0

    history = state.load_history()
    lines = ["## News bot — published", ""]
    for post in queue:
        print(f"== Publishing: {post['headline'][:70]} ==")
        results = pub.publish_post(post)
        ok = [k for k, v in results.items()
              if not str(v).startswith(("error", "skipped", "dry-run"))]
        dry = all(str(v).startswith(("dry-run", "skipped")) for v in results.values())
        status = "dry-run" if dry else ("posted" if ok else "failed")
        # update the matching queued history entries
        for e in history:
            if e.get("url") in ([post["url"]] + post.get("cluster_urls", [])) and e.get("status") == "queued":
                e["status"] = status
                e["results"] = {k: str(v)[:160] for k, v in results.items()}
        lines.append(f"- **{post['headline']}** → " + ", ".join(f"{k}: {v if str(v).startswith(('dry', 'error')) else 'ok'}" for k, v in results.items()))

    state.save_history(history)
    state.save_queue([])
    lines += ["", f"Budgets after publish: {budget.summary()}"]
    _summary(lines)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "generate"
    if cmd == "generate":
        sys.exit(generate())
    elif cmd == "publish":
        sys.exit(publish())
    else:
        print(f"Unknown command: {cmd} (use 'generate' or 'publish')")
        sys.exit(1)
