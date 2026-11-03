#!/usr/bin/env python3
"""Stage 1: seed from the HackerTube export.

Downloads videos.jsonl, mentions.jsonl and taxonomy.json from hackertube.org
(once; cached under data/raw/hackertube/), then keeps videos whose title,
summary or tags match any keyword in pipeline/keywords.txt, or whose mentions
match one of the unambiguous [strong] keywords. HackerTube has no radio
category of its own (radio sits inside "Electronics & circuit design"), so the
taxonomy is only used to report where the kept videos came from.

Writes:
  data/01_seed_videos.jsonl    HackerTube video rows that passed, plus matched_by
  data/01_seed_mentions.jsonl  their mentions in HamTube's unified mention schema
"""
import argparse
import time
from collections import defaultdict

from common import (DATA, HT_STANCE_MAP, RAW, excerpt, http_get, log, matches_keywords,
                    read_jsonl, write_jsonl)

HT_BASE = "https://hackertube.org/download/"
HT_FILES = ["videos.jsonl", "mentions.jsonl", "taxonomy.json"]
RAW_DIR = RAW / "hackertube"



def download() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name in HT_FILES:
        dest = RAW_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            log(f"  cached {name} ({dest.stat().st_size:,} bytes)")
            continue
        log(f"  downloading {name} …")
        r = http_get(HT_BASE + name, timeout=300, retries=6, backoff=15)
        if r is None or r.status_code != 200:
            raise SystemExit(f"download of {name} failed: HTTP {getattr(r, 'status_code', '?')}")
        dest.write_bytes(r.content)
        log(f"  saved {name} ({len(r.content):,} bytes)")
        time.sleep(5)  # hackertube.org rate-limits bursts


def to_mention(m: dict) -> dict:
    text = m.get("text") or ""
    return {
        "mention_id": f"hn:{m['hn_comment_id']}:{m['vid']}",
        "source": "hackertube",
        "video_id": m["vid"],
        "url": m["hn_url"],
        "author": m.get("author") or "",
        "date": m.get("month"),  # YYYY-MM is all the export carries
        "thread_title": m.get("story_title") or "",
        "text": text,
        "label": HT_STANCE_MAP.get(m.get("stance"), None),
        "label_source": "hackertube",
        "reason": m.get("why") or "",
        "excerpt": excerpt(m.get("quote") or text),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true", help="use cached files only")
    args = ap.parse_args()

    if not args.no_download:
        download()
    mentions_by_vid: dict[str, list[dict]] = defaultdict(list)
    for m in read_jsonl(RAW_DIR / "mentions.jsonl"):
        mentions_by_vid[m["vid"]].append(m)

    kept_videos, kept_mentions = [], []
    for v in read_jsonl(RAW_DIR / "videos.jsonl"):
        matched_by = []
        meta_blob = " ".join([v.get("title") or "", v.get("summary") or "", v.get("why_hn") or "",
                              v.get("primary_topic") or "", " ".join(v.get("tags") or [])])
        kw = matches_keywords(meta_blob)
        if kw:
            matched_by.append(f"meta:{kw}")
        else:
            for m in mentions_by_vid.get(v["vid"], []):
                kw = matches_keywords((m.get("text") or "") + " " + (m.get("story_title") or ""), strong_only=True)
                if kw:
                    matched_by.append(f"mention:{kw}")
                    break
        if not matched_by:
            continue
        v = dict(v, matched_by=matched_by)
        kept_videos.append(v)
        kept_mentions.extend(to_mention(m) for m in mentions_by_vid.get(v["vid"], []))

    nv = write_jsonl(DATA / "01_seed_videos.jsonl", kept_videos)
    nm = write_jsonl(DATA / "01_seed_mentions.jsonl", kept_mentions)
    by = defaultdict(int)
    cats = defaultdict(int)
    for v in kept_videos:
        by[v["matched_by"][0].split(":")[0]] += 1
        for c in (v.get("categories") or [])[:1]:
            cats[c["name"]] += 1
    log(f"seed: kept {nv} videos ({dict(by)}) with {nm} mentions")
    log("seed: top categories " + ", ".join(f"{k} ({n})" for k, n in sorted(cats.items(), key=lambda x: -x[1])[:6]))


if __name__ == "__main__":
    main()
