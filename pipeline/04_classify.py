#!/usr/bin/env python3
"""Stage 4: label each mention with Claude.

Labels: strong_recommendation, recommendation, neutral_reference, joke, criticism.
The thread title gives context. Mentions that arrived from HackerTube already
carry a label and are passed through unless --relabel is given.

Modes:
  default   concurrent Messages API calls (resumable; progress in data/raw/labels.jsonl)
  --batch   Message Batches API at half price; waits for the batch to finish

Writes data/04_mentions_labeled.jsonl
"""
import argparse
from collections import Counter

from common import (CLASSIFY_MODEL, DATA, LABELS, RAW, anthropic_client, append_jsonl, batch_params, claude_json,
                    excerpt, log, parallel_map, read_jsonl, require_input, run_batches, write_jsonl)

SYSTEM = """You label how an online comment treats a YouTube video it links to. The comments come from amateur (ham) radio, SDR and maker communities: Reddit, Hacker News, blogs.

Labels:
- strong_recommendation: enthusiastic, specific praise; tells others to watch it; calls it the best, a must-watch, the thing that made a topic click.
- recommendation: points people to the video as useful or worth watching, without strong enthusiasm. A blog post that embeds a video to show off a project it describes positively also counts as a recommendation.
- neutral_reference: cites the video as a source, example or illustration without judging it ("see this video for the waveform").
- joke: the link is a meme, punchline or sarcasm.
- criticism: criticizes, debunks or warns against the video or its creator.

Judge only this video, marked [VIDEO] in the text. Other links appear as [LINK]. When a comment links several videos the stance toward [VIDEO] may differ from the others.

Also return a short reason (at most 15 words) and an excerpt: a verbatim span of the comment, at most 200 characters, that best shows the stance. Copy it exactly; if nothing fits, use the first sentence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "reason": {"type": "string"},
        "excerpt": {"type": "string"},
    },
    "required": ["label", "reason", "excerpt"],
    "additionalProperties": False,
}


def user_prompt(m: dict) -> str:
    return (f"Source: {m['source']}\nThread title: {m.get('thread_title') or '(none)'}\n"
            f"Author: {m.get('author') or '(unknown)'}\n\nComment:\n{m['text']}")


def clean(m: dict, data: dict, model: str | None) -> dict:
    ex = (data.get("excerpt") or "").strip()
    if not ex or ex.replace(" ", "") not in m["text"].replace(" ", ""):
        ex = excerpt(m["text"])
    return dict(m, label=data["label"], label_source=model or "claude", reason=(data.get("reason") or "")[:120], excerpt=excerpt(ex))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relabel", action="store_true", help="also re-label mentions that came labeled from HackerTube")
    ap.add_argument("--batch", action="store_true", help="use the Message Batches API")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="label at most N mentions (testing)")
    ap.add_argument("--model", default=CLASSIFY_MODEL)
    args = ap.parse_args()

    src = require_input(DATA / "03_mentions.jsonl", "classify")
    mentions = list(read_jsonl(src))
    progress = RAW / "labels.jsonl"
    done = {r["mention_id"]: r for r in read_jsonl(progress)}

    todo = [m for m in mentions if m["mention_id"] not in done and (m["label"] is None or args.relabel)]
    if args.limit:
        todo = todo[: args.limit]
    log(f"classify: {len(mentions)} mentions, {len(done)} already labeled by Claude, {len(todo)} to do with {args.model}")

    client = anthropic_client() if todo else None
    failures = Counter()
    if todo and args.batch:
        reqs = [(m["mention_id"], batch_params(model=args.model, system=SYSTEM, user=user_prompt(m), schema=SCHEMA, max_tokens=1024, effort="low")) for m in todo]
        results = run_batches(client, reqs, tag="classify")
        for m in todo:
            res = results.get(m["mention_id"])
            if res and res.get("ok"):
                row = clean(m, res["data"], res.get("model"))
                append_jsonl(progress, row)
                done[m["mention_id"]] = row
            else:
                failures[(res or {}).get("error", "missing")] += 1
    elif todo:
        def work(m):
            res = claude_json(client, model=args.model, system=SYSTEM, user=user_prompt(m), schema=SCHEMA, max_tokens=1024, effort="low")
            return m, res

        for m, res in parallel_map(work, todo, args.workers, "labeled"):
            if res.get("ok"):
                row = clean(m, res["data"], res.get("model"))
                append_jsonl(progress, row)
                done[m["mention_id"]] = row
            else:
                failures[res.get("error", "?")] += 1
                log(f"  {m['mention_id']}: {res.get('error')}")

    out = []
    for m in mentions:
        out.append(done.get(m["mention_id"], m))
    n = write_jsonl(DATA / "04_mentions_labeled.jsonl", out)
    c = Counter(r["label"] or "unlabeled" for r in out)
    log(f"classify: wrote {n} mentions; labels {dict(c)}")
    if failures:
        log(f"classify: {sum(failures.values())} failures {dict(failures)} – rerun to retry them")


if __name__ == "__main__":
    main()
