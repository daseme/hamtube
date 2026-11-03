#!/usr/bin/env python3
"""Stage 8: Claude writes each card.

For each video: a two-sentence summary from the mentions and metadata, one to
three topics from pipeline/topics.json, a skill level (Technician, General,
Advanced), whether it helps with the Technician or General exam, and a
relevance check. Videos Claude judges unrelated to
amateur radio, SDR or RF go to data/08_rejected.jsonl instead of the catalog.

Modes: default concurrent calls (resumable; progress in data/raw/cards.jsonl) or --batch.

Writes data/08_described.jsonl
"""
import argparse
import json
from collections import Counter

from common import (DATA, DESCRIBE_MODEL, EXAM_PREP, LEVELS, POSITIVE, RAW, anthropic_client, append_jsonl, batch_params, claude_json,
                    load_topics, log, parallel_map, read_jsonl, require_input, run_batches, write_jsonl)

TOPICS = load_topics()
TOPIC_SLUGS = [t["slug"] for t in TOPICS]

SYSTEM = """You write catalog cards for HamTube, a static catalog of YouTube videos that amateur radio operators keep recommending to each other. The main reader is a brand-new Technician licensee and friends learning alongside them.

You get a video's YouTube title and channel, and the comments where people recommended or discussed it. Return:

- relevant: true only if the video teaches, demonstrates, reviews, or tells the story of something a ham would do or learn: amateur radio operating, licensing, SDR, RF/radio electronics, antennas, radio propagation, Meshtastic/LoRa mesh, satellites as received by hobbyists, emergency communications, or closely related hobby electronics. False for anything else, including general programming talks, WiFi product reviews, music, cooking, and anything that is merely *delivered by* radio rather than *about* radio: broadcast programs, radio plays, ambient or background listening, number-station or shipping-forecast compilations, podcasts and nostalgia for radio as a medium. A video can be charming and popular with hams and still be irrelevant here. When in doubt about a borderline RF *technical* topic, say true; when the doubt is whether it is about radio at all, say false.
- summary: exactly two sentences, written for the card. First sentence: what the video is and covers. Second: why people recommend it, drawn from the comments. Plain, specific, no hype, no "this video".
- topics: 1 to 3 slugs from the topic list, most relevant first. Assign a topic only if the video actually teaches or shows that subject. Being transmitted over the air does not make a video about propagation; mentioning a radio does not make it about equipment. history-culture means the history and culture of amateur radio and its community, not general broadcasting or media history.
- level: who the video is right for. HamTube is for early hams, so be generous toward the first two.
    Technician: a new or prospective licensee can follow it with no prior radio knowledge: what the hobby is, getting licensed, first handheld, repeaters, simplex, basic antennas, safety, etiquette, APRS and mesh basics, listening with an SDR.
    General: someone upgrading to or recently holding General: HF operating, SSB, HF antennas and feedline, propagation in practice, digital modes like FT8, portable HF, station building.
    Advanced: Extra-class depth or engineering: RF design, DSP, measurement theory, EME, contest strategy, anything that assumes electronics or math background. These are hidden from the catalog for now.
- exam_prep: whether the video directly helps someone study for a license exam: covers exam-pool material such as rules, band plans, operating procedures, electrical and RF basics, antennas, safety. "Technician" for Technician-pool material, "General" for General-pool material, "both" when it spans both, "none" when it is not exam-oriented (most project, review and story videos).
- why_watch: one short phrase (under 12 words) a new ham would find motivating, or empty.

Topic list (slug: meaning):
""" + "\n".join(f"- {t['slug']}: {t['name']} – {t['hint']}" for t in TOPICS)

SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string", "enum": TOPIC_SLUGS}},
        "level": {"type": "string", "enum": LEVELS},
        "exam_prep": {"type": "string", "enum": EXAM_PREP},
        "why_watch": {"type": "string"},
    },
    "required": ["relevant", "summary", "topics", "level", "exam_prep", "why_watch"],
    "additionalProperties": False,
}


def user_prompt(v: dict) -> str:
    ms = sorted(v["mentions"], key=lambda m: (m["label"] not in POSITIVE, m["label"] != "strong_recommendation"))[:10]
    lines = []
    for m in ms:
        lines.append(f"[{m['source']}, {m.get('date') or '?'}, {m['label']}] thread: {m.get('thread_title') or '-'}\n{m['text'][:900]}")
    dur = v.get("duration_sec")
    dur_s = f"{dur // 60} min" if dur else "unknown"
    return (f"Title: {v['title']}\nChannel: {v['channel']}\nDuration: {dur_s}\nPublished: {v.get('published') or 'unknown'}\n"
            f"Sources: {', '.join(v['sources'])}\n\nComments ({len(ms)} of {len(v['mentions'])}):\n\n" + "\n\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=DESCRIBE_MODEL)
    ap.add_argument("--redo", action="store_true", help="ignore cached cards and rewrite all")
    ap.add_argument("--redo-ids", default=None, help="comma-separated video ids to rewrite (others stay cached)")
    args = ap.parse_args()

    src = require_input(DATA / "07_enriched.jsonl", "describe")
    videos = list(read_jsonl(src))
    progress = RAW / "cards.jsonl"
    done = {} if args.redo else {r["video_id"]: r for r in read_jsonl(progress)}
    for vid in (args.redo_ids or "").split(","):
        done.pop(vid.strip(), None)
    todo = [v for v in videos if v["video_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    log(f"describe: {len(videos)} videos, {len(done)} cards cached, {len(todo)} to write with {args.model}")

    client = anthropic_client() if todo else None
    failures = Counter()

    def store(v, res):
        card = dict(res["data"], video_id=v["video_id"], card_model=res.get("model"))
        append_jsonl(progress, card)
        done[v["video_id"]] = card

    if todo and args.batch:
        reqs = [(v["video_id"], batch_params(model=args.model, system=SYSTEM, user=user_prompt(v), schema=SCHEMA, max_tokens=2048, effort="medium")) for v in todo]
        results = run_batches(client, reqs, tag="describe")
        for v in todo:
            res = results.get(v["video_id"])
            if res and res.get("ok"):
                store(v, res)
            else:
                failures[(res or {}).get("error", "missing")] += 1
    elif todo:
        def work(v):
            return v, claude_json(client, model=args.model, system=SYSTEM, user=user_prompt(v), schema=SCHEMA, max_tokens=2048, effort="medium")

        for v, res in parallel_map(work, todo, args.workers, "cards"):
            if res.get("ok"):
                store(v, res)
            else:
                failures[res.get("error", "?")] += 1
                log(f"  {v['video_id']}: {res.get('error')}")

    out, rejected = [], []
    for v in videos:
        card = done.get(v["video_id"])
        if not card:
            continue
        topics = list(dict.fromkeys(card["topics"]))[:3] or ["getting-started"]
        level = {"Extra": "Advanced", "Engineering": "Advanced"}.get(card["level"], card["level"])
        row = dict(v, summary=card["summary"].strip(), topics=topics, level=level, exam_prep=card.get("exam_prep", "none"), why_watch=card.get("why_watch", ""))
        (out if card.get("relevant", True) else rejected).append(row)
    n = write_jsonl(DATA / "08_described.jsonl", out)
    write_jsonl(DATA / "08_rejected.jsonl", rejected)
    log(f"describe: {n} cards written, {len(rejected)} rejected as off-topic, levels {dict(Counter(r['level'] for r in out))}")
    if failures:
        log(f"describe: {sum(failures.values())} failures {dict(failures)} – rerun to retry")


if __name__ == "__main__":
    main()
