#!/usr/bin/env python3
"""Stage 7: fetch YouTube metadata and drop dead videos.

With YOUTUBE_API_KEY set: videos.list, 50 ids per request (1 quota unit each),
raw responses cached in data/raw/youtube_meta.jsonl. A video is dropped when the
API does not return it (deleted) or it is not public.

Without a key (--oembed, or automatically when the key is missing): YouTube's
oEmbed endpoint confirms the video exists and gives title and channel, but no
duration or upload date. Good enough for a dry run, not for publishing.

Writes data/07_enriched.jsonl
"""
import argparse
import os
import re
from datetime import datetime

from common import DATA, RAW, RateLimiter, append_jsonl, http_get, log, read_jsonl, require_input, write_jsonl

CACHE = RAW / "youtube_meta.jsonl"


def parse_duration(iso: str) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_api(ids: list[str], key: str) -> None:
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        r = http_get("https://www.googleapis.com/youtube/v3/videos",
                     params={"part": "snippet,contentDetails,status,statistics", "id": ",".join(chunk), "key": key, "maxResults": 50})
        if r is None or r.status_code != 200:
            raise SystemExit(f"YouTube API error HTTP {getattr(r, 'status_code', '?')}: {getattr(r, 'text', '')[:300]}")
        got = {}
        for item in r.json().get("items", []):
            sn, cd, st, stats = item["snippet"], item["contentDetails"], item.get("status", {}), item.get("statistics", {})
            got[item["id"]] = {
                "id": item["id"], "found": True,
                "title": sn["title"], "channel": sn["channelTitle"], "channel_id": sn.get("channelId"),
                "published": sn["publishedAt"][:10], "duration_sec": parse_duration(cd.get("duration")),
                "privacy": st.get("privacyStatus"), "embeddable": st.get("embeddable", True),
                "views": int(stats["viewCount"]) if stats.get("viewCount") else None,
                "fetched": datetime.utcnow().strftime("%Y-%m-%d"),
            }
        for vid in chunk:
            append_jsonl(CACHE, got.get(vid, {"id": vid, "found": False, "fetched": datetime.utcnow().strftime("%Y-%m-%d")}))
        log(f"  youtube {min(i + 50, len(ids))}/{len(ids)}")


def fetch_oembed(ids: list[str]) -> None:
    lim = RateLimiter(4)
    for n, vid in enumerate(ids):
        lim.wait()
        r = http_get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}, retries=3)
        if r is not None and r.status_code == 200:
            d = r.json()
            row = {"id": vid, "found": True, "title": d.get("title"), "channel": d.get("author_name"), "channel_id": None,
                   "published": None, "duration_sec": None, "privacy": "public", "embeddable": True, "views": None, "oembed": True}
        else:
            row = {"id": vid, "found": False, "oembed": True, "status": getattr(r, "status_code", None)}
        row["fetched"] = datetime.utcnow().strftime("%Y-%m-%d")
        append_jsonl(CACHE, row)
        if n % 50 == 0:
            log(f"  oembed {n}/{len(ids)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oembed", action="store_true", help="use oEmbed even if a key is present")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and refetch everything")
    args = ap.parse_args()

    src = require_input(DATA / "06_scored.jsonl", "enrich")
    videos = list(read_jsonl(src))
    key = os.environ.get("YOUTUBE_API_KEY")
    use_oembed = args.oembed or not key
    cache = {} if args.refresh else {r["id"]: r for r in read_jsonl(CACHE)}
    if not use_oembed:  # a previous oembed-only row is not good enough once we have a key
        cache = {k: v for k, v in cache.items() if not v.get("oembed")}
    todo = [v["video_id"] for v in videos if v["video_id"] not in cache]
    if use_oembed:
        log(f"enrich: no YOUTUBE_API_KEY – using oEmbed for {len(todo)} videos (no duration or upload date)")
        fetch_oembed(todo)
    else:
        log(f"enrich: fetching {len(todo)} videos from the YouTube Data API ({(len(todo) + 49) // 50} quota units)")
        fetch_api(todo, key)
    cache = {r["id"]: r for r in read_jsonl(CACHE) if not (not use_oembed and r.get("oembed"))}

    out, dropped = [], 0
    for v in videos:
        meta = cache.get(v["video_id"])
        if not meta or not meta.get("found") or (meta.get("privacy") not in (None, "public")):
            dropped += 1
            continue
        out.append(dict(v, title=meta["title"], channel=meta["channel"], channel_id=meta.get("channel_id"),
                        published=meta.get("published"), duration_sec=meta.get("duration_sec"),
                        embeddable=meta.get("embeddable", True), views=meta.get("views"), meta_source="oembed" if meta.get("oembed") else "youtube_api"))
    n = write_jsonl(DATA / "07_enriched.jsonl", out)
    log(f"enrich: kept {n} videos, dropped {dropped} private/deleted")


if __name__ == "__main__":
    main()
