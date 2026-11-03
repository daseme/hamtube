#!/usr/bin/env python3
"""Stage 5: keep videos recommended by at least two different people.

A video qualifies when it has a positive label (recommendation or
strong_recommendation) from at least --min-people distinct authors. Unlabeled
mentions never count as positive.

Writes data/05_qualified.jsonl: {video_id, sources, people, mentions:[...]}
"""
import argparse
from collections import defaultdict

from common import DATA, POSITIVE, log, person_key, read_jsonl, require_input, write_jsonl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-people", type=int, default=2)
    args = ap.parse_args()

    src = require_input(DATA / "04_mentions_labeled.jsonl", "qualify")
    by_vid: dict[str, list[dict]] = defaultdict(list)
    for m in read_jsonl(src):
        by_vid[m["video_id"]].append(m)

    kept, dropped_no_pos, dropped_one = [], 0, 0
    seed_ids = {v["vid"] for v in read_jsonl(DATA / "01_seed_videos.jsonl")}
    seed_without_positive = 0
    for vid, ms in by_vid.items():
        people = {person_key(m["source"], m["author"]) for m in ms if m["label"] in POSITIVE}
        if not people:
            dropped_no_pos += 1
            if vid in seed_ids:
                seed_without_positive += 1
            continue
        if len(people) < args.min_people:
            dropped_one += 1
            continue
        kept.append({
            "video_id": vid,
            "sources": sorted({m["source"] for m in ms}),
            "people": len(people),
            "mentions": sorted(ms, key=lambda m: (m["label"] not in POSITIVE, m["label"] != "strong_recommendation", m.get("date") or "")),
        })
    n = write_jsonl(DATA / "05_qualified.jsonl", kept)
    log(f"qualify: {len(by_vid)} videos seen; kept {n}; dropped {dropped_no_pos} with no positive label "
        f"({seed_without_positive} of them HackerTube seeds) and {dropped_one} with fewer than {args.min_people} recommenders")


if __name__ == "__main__":
    main()
