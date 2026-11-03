#!/usr/bin/env python3
"""Stage 9: flag duplicate uploads of the same talk.

Two videos are duplicates when their normalized titles are near-identical and
either the channels match or the durations are within 3%. The higher-scored
copy stays; the other is written to data/09_duplicates.jsonl with duplicate_of.

Writes data/09_deduped.jsonl
"""
import re
from difflib import SequenceMatcher

from common import DATA, log, read_jsonl, require_input, write_jsonl

NOISE = re.compile(r"\b(official|full|hd|4k|video|talk|lecture|presentation|part \d+|ep(?:isode)?\.? ?\d+|\(.*?\)|\[.*?\])\b", re.I)


def norm(s: str) -> str:
    s = NOISE.sub(" ", s or "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def main() -> None:
    src = require_input(DATA / "08_described.jsonl", "dedupe")
    videos = sorted(read_jsonl(src), key=lambda v: -v["score"])
    kept, dups = [], []
    for v in videos:
        nt = norm(v["title"])
        dup_of = None
        for k in kept:
            if abs(len(nt) - len(k["_nt"])) > 20:
                continue
            tr = similar(nt, k["_nt"])
            if tr < 0.88:
                continue
            same_channel = norm(v["channel"]) == norm(k["channel"]) or similar(norm(v["channel"]), norm(k["channel"])) > 0.8
            d1, d2 = v.get("duration_sec"), k.get("duration_sec")
            close_dur = d1 and d2 and abs(d1 - d2) / max(d1, d2) < 0.03
            if same_channel or close_dur or tr > 0.96:
                dup_of = k["video_id"]
                break
        if dup_of:
            dups.append(dict(v, duplicate_of=dup_of))
        else:
            kept.append(dict(v, _nt=nt))
    for k in kept:
        k.pop("_nt")
    n = write_jsonl(DATA / "09_deduped.jsonl", kept)
    write_jsonl(DATA / "09_duplicates.jsonl", dups)
    log(f"dedupe: kept {n}, flagged {len(dups)} duplicates")
    for d in dups[:10]:
        log(f"  {d['video_id']} '{d['title'][:50]}' -> {d['duplicate_of']}")


if __name__ == "__main__":
    main()
