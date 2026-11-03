#!/usr/bin/env python3
"""Stage 3: turn posts into mention rows, one per (post, video).

Merges the HackerTube seed mentions (already labeled) with the collected posts
from stage 2. A comment linking two videos becomes two rows. Rows are deduped
on mention_id, with HackerTube's labeled copy winning over a raw HN copy.

Writes data/03_mentions.jsonl
"""
from collections import Counter

from common import DATA, MIN_OWN_TEXT, excerpt, log, mask_links, own_text_length, read_jsonl, require_input, write_jsonl


def main() -> None:
    seed = require_input(DATA / "01_seed_mentions.jsonl", "extract")
    rows: dict[str, dict] = {}
    for m in read_jsonl(seed):
        rows[m["mention_id"]] = m
    n_seed = len(rows)

    posts = DATA / "02_posts.jsonl"
    if not posts.exists():
        log("extract: no data/02_posts.jsonl yet; continuing with the HackerTube seed only")
    skipped_short = 0
    for p in read_jsonl(posts):
        if own_text_length(p["text"]) < MIN_OWN_TEXT:
            skipped_short += 1
            continue
        for vid in p["video_ids"]:
            prefix = "hn" if p["source"] == "hn" else p["source"]
            mid = f"{prefix}:{p['item_id']}:{vid}"
            if mid in rows:
                continue
            masked = mask_links(p["text"], vid)
            rows[mid] = {
                "mention_id": mid,
                "source": p["source"],
                "video_id": vid,
                "url": p["url"],
                "author": p.get("author") or "",
                "date": p.get("date"),
                "thread_title": p.get("thread_title") or "",
                "text": masked[:1500],
                "label": None,
                "label_source": None,
                "reason": "",
                "excerpt": excerpt(masked),
            }
    n = write_jsonl(DATA / "03_mentions.jsonl", rows.values())
    c = Counter(r["source"] for r in rows.values())
    log(f"extract: {n} mentions ({n_seed} from HackerTube, {skipped_short} posts skipped as too short) by source: {dict(c)}")
    log(f"extract: {sum(1 for r in rows.values() if r['label'] is None)} still need a label")


if __name__ == "__main__":
    main()
