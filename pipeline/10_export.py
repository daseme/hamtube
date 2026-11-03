#!/usr/bin/env python3
"""Stage 10: write catalog.json for the site.

Keeps at most five mentions per video, favoring different people and positive
labels, with excerpts under 200 characters. Advanced-level videos are left out
unless --include-advanced is given: the catalog is for Technician and General
hams. If pipeline/start_here.txt lists video ids (one per line, # comments
allowed) they become the Start Here path.

Writes data/catalog.json and docs/catalog.json
"""
import argparse
from collections import Counter

from common import DATA, DOCS, PIPELINE, POSITIVE, excerpt, load_topics, log, person_key, read_jsonl, require_input, save_json, today

SOURCE_NAMES = {"hackertube": "Hacker News", "hn": "Hacker News", "reddit": "Reddit", "rtlsdr": "RTL-SDR.com", "hackaday": "Hackaday"}


def pick_mentions(ms: list[dict], n: int = 5) -> list[dict]:
    order = sorted(ms, key=lambda m: (m["label"] not in POSITIVE, m["label"] != "strong_recommendation", -(len(m.get("text") or "")), m.get("date") or ""))
    out, seen_people = [], set()
    for m in order:  # pass 1: distinct people
        pk = person_key(m["source"], m["author"])
        if pk in seen_people:
            continue
        seen_people.add(pk)
        out.append(m)
        if len(out) == n:
            break
    if len(out) < n:
        for m in order:
            if m not in out:
                out.append(m)
                if len(out) == n:
                    break
    return [{
        "source": "hn" if m["source"] == "hackertube" else m["source"],
        "url": m["url"],
        "date": m.get("date"),
        "author": m.get("author") or "",
        "label": m["label"],
        "excerpt": excerpt(m.get("excerpt") or m.get("text") or "", 200),
    } for m in out]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-docs", action="store_true", help="write data/catalog.json only")
    ap.add_argument("--include-advanced", action="store_true", help="also publish Advanced-level videos")
    args = ap.parse_args()

    src = require_input(DATA / "09_deduped.jsonl", "export")
    topics = {t["slug"]: t["name"] for t in load_topics()}
    used_topics, videos, skipped_advanced = Counter(), [], 0
    for v in sorted(read_jsonl(src), key=lambda v: (-v["score"], v["title"])):
        if v["level"] == "Advanced" and not args.include_advanced:
            skipped_advanced += 1
            continue
        sources = sorted({"hn" if s == "hackertube" else s for s in v["sources"]})
        for t in v["topics"]:
            used_topics[t] += 1
        videos.append({
            "id": v["video_id"],
            "title": v["title"],
            "channel": v["channel"],
            "duration_sec": v.get("duration_sec"),
            "published": v.get("published"),
            "summary": v["summary"],
            "why_watch": v.get("why_watch") or "",
            "topics": v["topics"],
            "level": v["level"],
            "exam_prep": v.get("exam_prep", "none"),
            "score": v["score"],
            "people": v["people"],
            "mention_count": len(v["mentions"]),
            "first_mention": v.get("first_mention"),
            "last_mention": v.get("last_mention"),
            "sources": sources,
            "embeddable": v.get("embeddable", True),
            "mentions": pick_mentions(v["mentions"]),
        })
    start = []
    sh = PIPELINE / "start_here.txt"
    if sh.exists():
        ids = {v["id"] for v in videos}
        for line in sh.read_text().splitlines():
            vid = line.split("#", 1)[0].strip()
            if vid and vid in ids:
                start.append(vid)
    catalog = {
        "generated": today(),
        "counts": {"videos": len(videos), "mentions": sum(v["mention_count"] for v in videos)},
        "topics": [{"slug": s, "name": topics[s], "count": c} for s, c in used_topics.most_common() if s in topics],
        "levels": [l for l in ["Technician", "General", "Advanced"] if any(v["level"] == l for v in videos)],
        "sources": [{"slug": s, "name": SOURCE_NAMES.get(s, s)} for s in sorted({s for v in videos for s in v["sources"]})],
        "start_here": start,
        "videos": videos,
    }
    save_json(DATA / "catalog.json", catalog)
    if not args.no_docs:
        save_json(DOCS / "catalog.json", catalog)
    log(f"export: {len(videos)} videos ({skipped_advanced} Advanced left out), {len(catalog['topics'])} topics, start_here {len(start)} -> data/catalog.json"
        + ("" if args.no_docs else " and docs/catalog.json"))


if __name__ == "__main__":
    main()
