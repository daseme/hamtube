#!/usr/bin/env python3
"""Stage 6: score videos.

score = distinct months with a recommendation
      + 0.5 x distinct months with a strong recommendation
      + 1 for each source beyond the first

Writes data/06_scored.jsonl (adds score, rec_months, strong_months, first/last date)
"""
from common import DATA, POSITIVE, log, month_of, read_jsonl, require_input, write_jsonl


def score_video(v: dict) -> dict:
    rec_months, strong_months, dates = set(), set(), []
    for m in v["mentions"]:
        mo = month_of(m.get("date"))
        if m.get("date"):
            dates.append(m["date"])
        if m["label"] in POSITIVE and mo:
            rec_months.add(mo)
            if m["label"] == "strong_recommendation":
                strong_months.add(mo)
    score = len(rec_months) + 0.5 * len(strong_months) + (len(v["sources"]) - 1)
    return dict(v, score=round(score, 2), rec_months=len(rec_months), strong_months=len(strong_months),
                first_mention=min(dates) if dates else None, last_mention=max(dates) if dates else None)


def main() -> None:
    src = require_input(DATA / "05_qualified.jsonl", "score")
    rows = sorted((score_video(v) for v in read_jsonl(src)), key=lambda r: -r["score"])
    n = write_jsonl(DATA / "06_scored.jsonl", rows)
    if rows:
        log(f"score: {n} videos; top score {rows[0]['score']}, median {rows[len(rows)//2]['score']}")


if __name__ == "__main__":
    main()
