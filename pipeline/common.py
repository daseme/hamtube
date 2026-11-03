"""Shared helpers for the HamTube pipeline.

Every stage imports from here: paths, JSONL I/O, YouTube URL parsing, the
keyword matcher, label vocabularies, HTTP with retries, and the Claude client.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
DATA = ROOT / "data"
RAW = DATA / "raw"
DOCS = ROOT / "docs"

USER_AGENT = os.environ.get("HAMTUBE_USER_AGENT", "hamtube/0.1 (one-time catalog build; contact via repo)")


def load_env() -> None:
    """Load ROOT/.env into os.environ without overriding existing values."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(ROOT / ".env", override=False)
        return
    except ImportError:
        pass
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def log(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- JSONL I/O

def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log(f"  {path.name}: skipping unreadable line {n} (still being written?)")


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(path)
    return n


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def require_input(path: Path, stage: str) -> Path:
    if not path.exists():
        log(f"{stage}: missing input {path.relative_to(ROOT)}. Run the earlier stage first.")
        sys.exit(1)
    return path


# ---------------------------------------------------------------- YouTube ids

_YT_ID = r"([A-Za-z0-9_-]{11})"
YOUTUBE_PATTERNS = [
    re.compile(r"(?:youtube(?:-nocookie)?\.com|m\.youtube\.com)/watch\?(?:[^\s\"'<>]*?&)?v=" + _YT_ID, re.I),
    re.compile(r"youtu\.be/" + _YT_ID, re.I),
    re.compile(r"youtube(?:-nocookie)?\.com/(?:embed|v|shorts|live)/" + _YT_ID, re.I),
    re.compile(r"youtube\.com/attribution_link\?[^\s\"'<>]*?v(?:%3D|=)" + _YT_ID, re.I),
]
_YT_ANY = re.compile(r"https?://[^\s\"'<>()\]]*(?:youtube(?:-nocookie)?\.com|youtu\.be)[^\s\"'<>()\]]*", re.I)
_URL_RE = re.compile(r"https?://[^\s\"'<>()\]]+", re.I)


def extract_youtube_ids(text: str) -> list[str]:
    """All YouTube video ids in text, in first-seen order, no duplicates."""
    if not text or "yout" not in text.lower():
        return []
    seen: "OrderedDict[str, None]" = OrderedDict()
    for pat in YOUTUBE_PATTERNS:
        for m in pat.finditer(text):
            seen.setdefault(m.group(1), None)
    return list(seen)


def mask_links(text: str, video_id: str | None = None) -> str:
    """Replace links with [VIDEO] (the video in question) or [LINK]."""

    def repl(m: re.Match) -> str:
        url = m.group(0)
        if video_id and video_id in url:
            return "[VIDEO]"
        return "[LINK]"

    return _URL_RE.sub(repl, text or "")


def own_text_length(text: str) -> int:
    """Length of the author's own words once links are stripped."""
    return len(re.sub(r"\s+", " ", _URL_RE.sub("", text or "")).strip())


MIN_OWN_TEXT = 100


# ---------------------------------------------------------------- keywords

def load_keywords(path: Path | None = None) -> dict[str, list[str]]:
    """Parse keywords.txt into {"strong": [...], "weak": [...]}."""
    path = path or PIPELINE / "keywords.txt"
    out: dict[str, list[str]] = {"strong": [], "weak": []}
    section = "strong"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            out.setdefault(section, [])
            continue
        out[section].append(line)
    return out


def keyword_regex(keywords: list[str]) -> re.Pattern:
    """Whole-word match. Short all-caps terms (VHF, CW) stay case-sensitive."""
    cs, ci = [], []
    for kw in keywords:
        if kw.isupper() and len(kw) <= 4:
            cs.append(re.escape(kw))
        else:
            ci.append(re.escape(kw).replace(r"\ ", r"[\s-]+"))
    parts = []
    if ci:
        parts.append(r"(?i:\b(?:%s)\b)" % "|".join(ci))
    if cs:
        parts.append(r"\b(?:%s)\b" % "|".join(cs))
    return re.compile("|".join(parts))


_KW: dict[str, re.Pattern] = {}


def matches_keywords(text: str, strong_only: bool = False) -> str | None:
    """First keyword found in text, or None. strong_only ignores the [weak] list."""
    key = "strong" if strong_only else "all"
    if key not in _KW:
        kws = load_keywords()
        terms = kws["strong"] if strong_only else kws["strong"] + kws["weak"]
        _KW[key] = keyword_regex(terms)
    m = _KW[key].search(text or "")
    return m.group(0) if m else None


# ---------------------------------------------------------------- labels

LABELS = ["strong_recommendation", "recommendation", "neutral_reference", "joke", "criticism"]
POSITIVE = {"strong_recommendation", "recommendation"}
HT_STANCE_MAP = {
    "strong_rec": "strong_recommendation",
    "rec": "recommendation",
    "reference": "neutral_reference",
    "joke": "joke",
    "critical": "criticism",
}
LEVELS = ["Technician", "General", "Advanced"]
EXAM_PREP = ["none", "Technician", "General", "both"]
DURATION_BUCKETS = {"short": (0, 15 * 60), "medium": (15 * 60, 60 * 60), "long": (60 * 60, 10**9)}


def load_topics() -> list[dict]:
    return load_json(PIPELINE / "topics.json")["topics"]


def month_of(date: str | None) -> str | None:
    """'2023-02-11' or '2023-02' -> '2023-02'."""
    if not date or len(date) < 7:
        return None
    return date[:7]


def person_key(source: str, author: str | None) -> str:
    a = (author or "").strip().lower() or "anonymous"
    return f"{source}:{a}"


def excerpt(text: str, n: int = 200) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) <= n:
        return t
    cut = t[: n - 1]
    if " " in cut[n // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "…"


# ---------------------------------------------------------------- HTTP

def http_get(url: str, *, params=None, headers=None, retries: int = 5, backoff: float = 3.0, timeout: int = 60, session=None):
    """GET with retry on 429/5xx/connection errors. Returns a requests.Response."""
    import requests

    sess = session or requests
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries):
        try:
            r = sess.get(url, params=params, headers=hdrs, timeout=timeout)
        except requests.RequestException as e:  # connection problems
            last = e
            time.sleep(backoff * (2**attempt))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After") or backoff * (2**attempt))
            log(f"  HTTP {r.status_code} on {url[:80]} – waiting {wait:.0f}s")
            time.sleep(min(wait, 300))
            last = r
            continue
        return r
    if isinstance(last, Exception):
        raise last
    return last


class RateLimiter:
    def __init__(self, per_second: float):
        self.gap = 1.0 / per_second if per_second > 0 else 0
        self.last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delta = self.last + self.gap - now
        if delta > 0:
            time.sleep(delta)
        self.last = time.monotonic()


# ---------------------------------------------------------------- Claude

CLASSIFY_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-sonnet-5-5")
DESCRIBE_MODEL = os.environ.get("DESCRIBE_MODEL", "claude-opus-5-5")
USE_FALLBACKS = os.environ.get("HAMTUBE_FALLBACKS", "1") != "0"


def anthropic_client():
    import anthropic

    return anthropic.Anthropic(max_retries=4)


def claude_json(client, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 2048, effort: str = "low") -> dict:
    """One structured-output call. Returns {"ok": True, "data": ...} or {"ok": False, "error": ...}.

    Uses the server-side refusal fallback by default so a safety-classifier
    decline on the primary model is re-run on a fallback model in the same call.
    """
    import anthropic

    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        if USE_FALLBACKS:
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        return {"ok": False, "error": f"bad_request: {e.message}"}
    except anthropic.APIStatusError as e:
        return {"ok": False, "error": f"api_{e.status_code}: {e.message}"}
    except anthropic.APIConnectionError as e:
        return {"ok": False, "error": f"connection: {e}"}
    if resp.stop_reason == "refusal":
        cat = getattr(getattr(resp, "stop_details", None), "category", None)
        return {"ok": False, "error": f"refusal:{cat}"}
    if resp.stop_reason == "max_tokens":
        return {"ok": False, "error": "max_tokens"}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return {"ok": True, "data": json.loads(text), "model": resp.model, "usage": resp.usage.to_dict() if hasattr(resp.usage, "to_dict") else None}
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad_json", "raw": text[:500]}


def batch_params(*, model: str, system: str, user: str, schema: dict, max_tokens: int, effort: str) -> dict:
    """Request params for the Message Batches API (no fallbacks: the Batches API rejects them)."""
    return dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )


def run_batches(client, requests_: list[tuple[str, dict]], tag: str, poll_seconds: int = 60) -> dict[str, dict]:
    """Submit requests to the Message Batches API and wait. Returns custom_id -> result dict like claude_json."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    state_path = RAW / "batches" / f"{tag}.json"
    state = load_json(state_path, {"batches": []})
    out: dict[str, dict] = {}
    done_ids = {cid for b in state["batches"] for cid in b.get("custom_ids", [])}
    pending = [(cid, p) for cid, p in requests_ if cid not in done_ids]
    CHUNK = 10000
    for i in range(0, len(pending), CHUNK):
        chunk = pending[i : i + CHUNK]
        batch = client.messages.batches.create(
            requests=[Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**p)) for cid, p in chunk]
        )
        log(f"  submitted batch {batch.id} with {len(chunk)} requests")
        state["batches"].append({"id": batch.id, "custom_ids": [cid for cid, _ in chunk]})
        save_json(state_path, state)
    for b in state["batches"]:
        bid = b["id"]
        while True:
            info = client.messages.batches.retrieve(bid)
            if info.processing_status == "ended":
                break
            c = info.request_counts
            log(f"  batch {bid}: processing={c.processing} succeeded={c.succeeded} errored={c.errored}")
            time.sleep(poll_seconds)
        for res in client.messages.batches.results(bid):
            cid = res.custom_id
            if res.result.type == "succeeded":
                msg = res.result.message
                if msg.stop_reason == "refusal":
                    out[cid] = {"ok": False, "error": "refusal"}
                    continue
                text = next((blk.text for blk in msg.content if blk.type == "text"), "")
                try:
                    out[cid] = {"ok": True, "data": json.loads(text), "model": msg.model}
                except json.JSONDecodeError:
                    out[cid] = {"ok": False, "error": "bad_json", "raw": text[:500]}
            else:
                err = getattr(res.result, "error", None)
                out[cid] = {"ok": False, "error": f"{res.result.type}:{getattr(err, 'type', '')}"}
    return out


def parallel_map(fn, items: list, workers: int, label: str = "") -> Iterator:
    """Run fn over items with a thread pool, yielding results as they finish."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for fut in as_completed(futs):
            done += 1
            if done % 25 == 0 or done == len(items):
                log(f"  {label} {done}/{len(items)}")
            yield fut.result()
