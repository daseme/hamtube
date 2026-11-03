#!/usr/bin/env python3
"""Stage 2: collect posts and comments that link YouTube videos.

Sources (pick with --sources, default "reddit,rtlsdr"):
  reddit    r/amateurradio, r/HamRadio, r/RTLSDR, r/meshtastic. Three ways in,
            picked with --reddit-via (default auto):
              arctic   the Arctic Shift public archive API, no credentials, every
                       post and comment since --reddit-since (hours for big subs,
                       resumable; the default when no Reddit app key is set)
              api      the official Reddit OAuth API (REDDIT_CLIENT_ID /
                       REDDIT_CLIENT_SECRET in .env); search-based, so it misses
                       old threads but is quick
              archive  a local Arctic Shift / Pushshift dump via --reddit-archive DIR
  rtlsdr    rtl-sdr.com blog, every post from its sitemap (HTML scrape, ~1 req/s)
  hackaday  hackaday.com "Radio Hacks" category via the WordPress REST API
  hn        the open-index/hacker-news archive on Hugging Face (monthly parquet,
            ~100 MB each; keyword-filtered; slow, use --hn-since to bound it)

Every item keeps only posts/comments with a YouTube link and at least 100
characters of the author's own text. Raw responses are cached under data/raw/
so the stage can be interrupted and rerun.

Writes data/02_posts.jsonl: {source, item_id, url, author, date, thread_title, text, video_ids}
"""
import argparse
import base64
import glob
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from common import (DATA, MIN_OWN_TEXT, RAW, RateLimiter, extract_youtube_ids, http_get, load_json,
                    log, matches_keywords, own_text_length, read_jsonl, save_json)

OUT = DATA / "02_posts.jsonl"
DEFAULT_SUBREDDITS = ["amateurradio", "HamRadio", "RTLSDR", "meshtastic"]


def iso(ts: float | int | str | None) -> str | None:
    if ts is None or ts == "":
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(ts)[:10]


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def make_item(source, item_id, url, author, date, thread_title, text, raw_html_for_ids=None, require_keyword=False) -> dict | None:
    ids = extract_youtube_ids((raw_html_for_ids or "") + "\n" + (text or ""))
    if not ids:
        return None
    if own_text_length(text) < MIN_OWN_TEXT:
        return None
    if require_keyword and not matches_keywords((thread_title or "") + " " + (text or ""), strong_only=True):
        return None
    return {
        "source": source,
        "item_id": item_id,
        "url": url,
        "author": author or "",
        "date": date,
        "thread_title": thread_title or "",
        "text": text,
        "video_ids": ids,
    }


class Writer:
    """Append-only output with dedupe on (source, item_id), so reruns resume."""

    def __init__(self, path: Path):
        self.path = path
        self.seen = {(r["source"], r["item_id"]) for r in read_jsonl(path)}
        self.f = path.open("a", encoding="utf-8")
        self.added = 0

    def add(self, item: dict | None) -> None:
        if not item:
            return
        key = (item["source"], item["item_id"])
        if key in self.seen:
            return
        self.seen.add(key)
        self.f.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.f.flush()
        self.added += 1

    def has(self, source: str, item_id: str) -> bool:
        return (source, item_id) in self.seen


# ------------------------------------------------------------------ Reddit API

class Reddit:
    def __init__(self):
        cid, sec = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
        if not cid or not sec:
            raise SystemExit("reddit: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env "
                             "(a 'script' app at https://www.reddit.com/prefs/apps), or use --reddit-archive DIR")
        self.ua = os.environ.get("REDDIT_USER_AGENT", "linux:hamtube:0.1 (one-time catalog build)")
        import requests

        self.sess = requests.Session()
        tok = self.sess.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(), "User-Agent": self.ua},
            timeout=30,
        )
        tok.raise_for_status()
        self.token = tok.json()["access_token"]
        self.limiter = RateLimiter(0.9)  # OAuth allows 100 requests/min; stay well under
        self.cache = RAW / "reddit"

    def get(self, path: str, **params):
        self.limiter.wait()
        r = http_get("https://oauth.reddit.com" + path, params=dict(params, raw_json=1),
                     headers={"Authorization": f"Bearer {self.token}", "User-Agent": self.ua}, session=self.sess)
        if r is None or r.status_code != 200:
            log(f"  reddit {path}: HTTP {getattr(r, 'status_code', '?')}")
            return None
        return r.json()

    def search_posts(self, sub: str, queries: list[str], max_pages: int) -> set[str]:
        ids_path = self.cache / "search" / f"{sub}.json"
        found = set(load_json(ids_path, []))
        for q in queries:
            for sort in ("top", "new", "relevance", "comments"):
                after = None
                for _ in range(max_pages):
                    data = self.get(f"/r/{sub}/search", q=q, restrict_sr=1, sort=sort, t="all", limit=100, after=after)
                    if not data:
                        break
                    kids = data["data"]["children"]
                    found.update(c["data"]["id"] for c in kids)
                    after = data["data"].get("after")
                    if not after or not kids:
                        break
            save_json(ids_path, sorted(found))
        return found

    def thread(self, post_id: str) -> dict | None:
        p = self.cache / "threads" / f"{post_id}.json"
        if p.exists():
            return load_json(p)
        data = self.get(f"/comments/{post_id}", limit=500, depth=10, sort="top", showmore=0)
        if data:
            save_json(p, data)
        return data


def flatten_comments(listing) -> list[dict]:
    out = []
    stack = [listing]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("kind") == "Listing":
            stack.extend(node["data"]["children"])
        elif node.get("kind") == "t1":
            d = node["data"]
            out.append(d)
            if isinstance(d.get("replies"), dict):
                stack.append(d["replies"])
    return out


def reddit_items_from_thread(sub: str, data) -> list[dict]:
    items = []
    post = data[0]["data"]["children"][0]["data"]
    title = post.get("title") or ""
    permalink = "https://www.reddit.com" + post.get("permalink", "")
    post_text = title + "\n\n" + (post.get("selftext") or "")
    items.append(make_item("reddit", f"t3_{post['id']}", permalink, post.get("author"), iso(post.get("created_utc")),
                           title, post_text, raw_html_for_ids=post.get("url") or ""))
    for c in flatten_comments(data[1]):
        body = c.get("body") or ""
        if body in ("[deleted]", "[removed]"):
            continue
        items.append(make_item("reddit", f"t1_{c['id']}", "https://www.reddit.com" + c.get("permalink", ""),
                               c.get("author"), iso(c.get("created_utc")), title, body))
    return [i for i in items if i]


def collect_reddit(w: Writer, subs: list[str], max_pages: int, limit: int | None) -> None:
    api = Reddit()
    queries = ["youtube.com", "youtu.be", "video", "youtube channel", "watch this video", "best video"]
    for sub in subs:
        log(f"reddit: searching r/{sub}")
        post_ids = api.search_posts(sub, queries, max_pages)
        log(f"  {len(post_ids)} candidate posts")
        for n, pid in enumerate(sorted(post_ids)):
            if limit and n >= limit:
                break
            if w.has("reddit", f"t3_{pid}") and (api.cache / "threads" / f"{pid}.json").exists():
                continue
            data = api.thread(pid)
            if not data:
                continue
            for item in reddit_items_from_thread(sub, data):
                w.add(item)
            if n % 50 == 0:
                log(f"  r/{sub}: {n}/{len(post_ids)} threads, {w.added} items so far")


# ------------------------------------------------------------------ Reddit archive dump

def open_archive(path: str):
    if path.endswith(".zst"):
        try:
            import zstandard  # type: ignore
        except ImportError:
            raise SystemExit("pip install zstandard to read .zst archive files")
        import io

        fh = open(path, "rb")
        dctx = zstandard.ZstdDecompressor(max_window_size=2**31)
        return io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def collect_reddit_archive(w: Writer, directory: str, subs: list[str]) -> None:
    subs_l = {s.lower() for s in subs}
    files = sorted(glob.glob(os.path.join(directory, "**", "*"), recursive=True))
    files = [f for f in files if re.search(r"\.(jsonl|ndjson|json|zst)$", f)]
    titles: dict[str, str] = {}
    # pass 1: submissions (RS_*), so comments can pick up their thread title
    for f in files:
        if os.path.basename(f).upper().startswith("RC"):
            continue
        log(f"reddit-archive: submissions in {os.path.basename(f)}")
        with open_archive(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (d.get("subreddit") or "").lower() not in subs_l or "title" not in d:
                    continue
                titles[d["id"]] = d["title"]
                text = d["title"] + "\n\n" + (d.get("selftext") or "")
                w.add(make_item("reddit", f"t3_{d['id']}", "https://www.reddit.com" + d.get("permalink", ""), d.get("author"),
                                iso(d.get("created_utc")), d["title"], text, raw_html_for_ids=d.get("url") or ""))
    for f in files:
        if not os.path.basename(f).upper().startswith("RC"):
            continue
        log(f"reddit-archive: comments in {os.path.basename(f)}")
        with open_archive(f) as fh:
            for line in fh:
                if "yout" not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (d.get("subreddit") or "").lower() not in subs_l:
                    continue
                link = (d.get("link_id") or "").replace("t3_", "")
                w.add(make_item("reddit", f"t1_{d['id']}", "https://www.reddit.com" + d.get("permalink", f"/comments/{link}/_/{d['id']}/"),
                                d.get("author"), iso(d.get("created_utc")), titles.get(link, ""), d.get("body") or ""))


# ------------------------------------------------------------------ Reddit via Arctic Shift

ARCTIC = "https://arctic-shift.photon-reddit.com/api"


class ArcticBusy(Exception):
    pass


def arctic_get(path: str, params: dict, limiter: RateLimiter, what: str) -> list[dict]:
    """One Arctic Shift call with patient retries (it answers 422 'Timeout' when busy)."""
    for attempt in range(16):
        limiter.wait()
        r = http_get(f"{ARCTIC}/{path}", params=params, timeout=180, retries=2)
        if r is not None and r.status_code == 200:
            return r.json().get("data") or []
        wait = min(30 * (attempt + 1), 300)
        log(f"  arctic {what}: HTTP {getattr(r, 'status_code', '?')} {getattr(r, 'text', '')[:60]} – waiting {wait}s")
        time.sleep(wait)
    raise ArcticBusy(what)


WINDOW = 30 * 86400  # query one month at a time so the server has less to scan


def collect_arctic(w: Writer, subs: list[str], since: str, limit: int | None, scan_posts: bool = True) -> None:
    """Reddit through the Arctic Shift archive API, no credentials.

    Comments: the API's word filter is fast for the token "youtube" (other
    filters such as "youtu.be" time out), so comments are pulled with
    body=youtube, oldest first, 100 per page. Comments that link a video with a
    bare youtu.be URL and never write the word "youtube" are missed; the
    official API or an archive dump catches those.
    Posts: no fast filter exists, so every post is scanned (about 30 minutes for
    r/amateurradio) to pick up link posts and self posts with a video.
    Thread titles come from posts/ids, cached per subreddit. Cursors under
    data/raw/arctic/ make each pass resumable.
    """
    cache = RAW / "arctic"
    cache.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(0.4)
    since_ts = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    now_ts = int(time.time())
    for sub in subs:
        titles_path = cache / f"{sub}_titles.jsonl"
        titles = {r["id"]: r["t"] for r in read_jsonl(titles_path)}
        tf = titles_path.open("a", encoding="utf-8")

        def remember(pid: str, title: str) -> None:
            if pid not in titles:
                titles[pid] = title or ""
                tf.write(json.dumps({"id": pid, "t": titles[pid]}) + "\n")
                tf.flush()

        def resolve_titles(ids: set[str]) -> None:
            missing = sorted(i for i in ids if i and i not in titles)
            for i in range(0, len(missing), 100):
                chunk = missing[i : i + 100]
                for d in arctic_get("posts/ids", {"ids": ",".join(chunk), "fields": "id,title"}, limiter, f"titles r/{sub}"):
                    remember(d["id"], d.get("title") or "")
                for pid in chunk:
                    remember(pid, "")

        passes = [("comments", {"body": "youtube", "fields": "id,author,body,created_utc,link_id,subreddit"})]
        if scan_posts:
            passes.append(("posts", {"fields": "id,title,author,created_utc,url,selftext,subreddit"}))
        for kind, extra in passes:
            cursor_path = cache / f"{sub}_{kind}.cursor"
            after = int(cursor_path.read_text()) if cursor_path.exists() else since_ts
            pages, rows_total = 0, 0
            try:
              while after < now_ts:
                before = min(after + WINDOW, now_ts + 60)
                rows = arctic_get(f"{kind}/search", dict(extra, subreddit=sub, after=after, before=before, limit=100, sort="asc"), limiter, f"{kind} r/{sub}")
                pages += 1
                rows_total += len(rows)
                if kind == "comments":
                    resolve_titles({(d.get("link_id") or "").replace("t3_", "") for d in rows})
                for d in rows:
                    date = iso(d.get("created_utc"))
                    if kind == "posts":
                        remember(d["id"], d.get("title") or "")
                        text = (d.get("title") or "") + "\n\n" + (d.get("selftext") or "")
                        w.add(make_item("reddit", f"t3_{d['id']}", f"https://www.reddit.com/r/{sub}/comments/{d['id']}/", d.get("author"),
                                        date, d.get("title") or "", text, raw_html_for_ids=d.get("url") or ""))
                    else:
                        body = d.get("body") or ""
                        if body in ("[deleted]", "[removed]"):
                            continue
                        link = (d.get("link_id") or "").replace("t3_", "")
                        w.add(make_item("reddit", f"t1_{d['id']}", f"https://www.reddit.com/r/{sub}/comments/{link}/_/{d['id']}/",
                                        d.get("author"), date, titles.get(link, ""), body))
                if len(rows) < 100:
                    after = before  # this month is exhausted
                else:
                    last = int(rows[-1]["created_utc"])
                    after = last if last > after else after + 1
                cursor_path.write_text(str(after))
                if pages % 20 == 0:
                    log(f"  arctic r/{sub} {kind}: {pages} pages, up to {iso(after)}, {w.added} items so far")
                if limit and pages >= limit:
                    break
            except ArcticBusy:
                log(f"arctic r/{sub} {kind}: server stayed busy at {iso(after)}; moving on, rerun later to resume")
                continue
            log(f"arctic r/{sub} {kind}: {rows_total} rows in {pages} pages, caught up to {iso(after)}; {w.added} items total")
        tf.close()


# ------------------------------------------------------------------ rtl-sdr.com

def collect_rtlsdr(w: Writer, limit: int | None) -> None:
    cache = RAW / "rtlsdr" / "html"
    cache.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(1.0)
    urls_path = RAW / "rtlsdr" / "urls.json"
    urls = load_json(urls_path)
    if not urls:
        urls = []
        idx = http_get("https://www.rtl-sdr.com/sitemap.xml")
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for sm in ET.fromstring(idx.content).findall("s:sitemap/s:loc", ns):
            if "posts-post" not in sm.text:
                continue
            limiter.wait()
            r = http_get(sm.text)
            urls += [u.text for u in ET.fromstring(r.content).findall("s:url/s:loc", ns)]
        save_json(urls_path, urls)
    log(f"rtlsdr: {len(urls)} posts in sitemap")
    for n, url in enumerate(urls):
        if limit and n >= limit:
            break
        slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[-120:]
        p = cache / f"{slug}.html"
        if p.exists():
            page = p.read_text(encoding="utf-8", errors="replace")
        else:
            limiter.wait()
            r = http_get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; hamtube/0.1)"})
            if r is None or r.status_code != 200:
                log(f"  skip {url}: HTTP {getattr(r, 'status_code', '?')}")
                continue
            page = r.text
            p.write_text(page, encoding="utf-8")
        m = re.search(r'<div class="entry-content[^"]*"[^>]*>(.*?)<footer', page, re.S) or \
            re.search(r'<article[^>]*>(.*?)</article>', page, re.S)
        body_html = m.group(1) if m else page
        title = re.search(r'property="og:title" content="([^"]*)"', page)
        date = re.search(r'property="article:published_time" content="([^"]*)"', page)
        w.add(make_item("rtlsdr", url, url, "rtl-sdr.com", date.group(1)[:10] if date else None,
                        html.unescape(title.group(1)) if title else "", strip_html(body_html), raw_html_for_ids=body_html))
        if n % 100 == 0:
            log(f"  rtlsdr {n}/{len(urls)}, {w.added} items")


# ------------------------------------------------------------------ hackaday

def collect_hackaday(w: Writer, limit: int | None) -> None:
    cache = RAW / "hackaday"
    cache.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(1.0)
    page, total_pages = 1, None
    while True:
        p = cache / f"page-{page:04d}.json"
        if p.exists():
            posts = load_json(p)
        else:
            limiter.wait()
            r = http_get("https://hackaday.com/wp-json/wp/v2/posts",
                         params={"categories": 23971578, "per_page": 100, "page": page, "_embed": "author",
                                 "_fields": "id,date,link,title,content,_links,_embedded"})
            if r is None or r.status_code != 200:
                log(f"  hackaday page {page}: HTTP {getattr(r, 'status_code', '?')}; stopping")
                break
            total_pages = int(r.headers.get("X-WP-TotalPages", total_pages or 0))
            posts = r.json()
            save_json(p, posts)
        if not posts:
            break
        for post in posts:
            body = post["content"]["rendered"]
            author = ""
            emb = post.get("_embedded", {}).get("author") or []
            if emb and isinstance(emb[0], dict):
                author = emb[0].get("name") or ""
            w.add(make_item("hackaday", str(post["id"]), post["link"], author or "hackaday", post["date"][:10],
                            html.unescape(strip_html(post["title"]["rendered"])), strip_html(body), raw_html_for_ids=body))
        log(f"  hackaday page {page}{'/' + str(total_pages) if total_pages else ''}, {w.added} items")
        if (limit and page >= limit) or len(posts) < 100 or (total_pages and page >= total_pages):
            break
        page += 1


# ------------------------------------------------------------------ Hacker News archive

def collect_hn(w: Writer, since: str, until: str | None, skip_ids: set[str]) -> None:
    try:
        import pyarrow.parquet as pq  # type: ignore
        from huggingface_hub import hf_hub_download, list_repo_files  # type: ignore
    except ImportError:
        raise SystemExit("pip install pyarrow huggingface_hub for the hn source")
    files = sorted(f for f in list_repo_files("open-index/hacker-news", repo_type="dataset") if f.endswith(".parquet") and f.startswith("data/"))
    months = [f for f in files if since <= f.split("/")[-1][:7] <= (until or "9999-99")]
    log(f"hn: {len(months)} monthly files from {since}")
    fb_cache = RAW / "hn" / "titles.json"
    titles: dict[str, str] = load_json(fb_cache, {})
    cols = ["id", "type", "by", "time", "text", "parent", "title", "dead", "deleted"]
    for f in months:
        local = hf_hub_download("open-index/hacker-news", f, repo_type="dataset")
        tbl = pq.read_table(local, columns=cols)
        rows = tbl.to_pylist()
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            if r.get("title"):
                titles[str(r["id"])] = r["title"]
        hits = [r for r in rows if r.get("text") and "yout" in r["text"].lower() and not r.get("dead") and not r.get("deleted")]
        for r in hits:
            text = strip_html(r["text"])
            # walk up to the story for a title, within this month's rows first
            cur, title, hops = r, "", 0
            while cur and hops < 50:
                if cur.get("title"):
                    title = cur["title"]
                    break
                parent = cur.get("parent")
                if not parent:
                    break
                if str(parent) in titles:
                    title = titles[str(parent)]
                    break
                cur = by_id.get(parent)
                hops += 1
            if not title and r.get("parent"):
                title = hn_story_title(r["parent"], titles)
            cid = f"hn:{r['id']}"
            if cid in skip_ids:
                continue
            t = r.get("time")
            date = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else iso(t)
            w.add(make_item("hn", str(r["id"]), f"https://news.ycombinator.com/item?id={r['id']}", r.get("by"), date, title, text,
                            raw_html_for_ids=r["text"], require_keyword=True))
        save_json(fb_cache, titles)
        log(f"  {f.split('/')[-1]}: {len(hits)} youtube comments, {w.added} items total")


def hn_story_title(item_id: int, titles: dict[str, str], hops: int = 0) -> str:
    """Resolve a story title through the public HN API when the parent is in another month."""
    key = str(item_id)
    if key in titles:
        return titles[key]
    if hops > 30:
        return ""
    r = http_get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=20)
    if r is None or r.status_code != 200 or not r.text or r.text == "null":
        return ""
    d = r.json()
    if d.get("title"):
        titles[key] = d["title"]
        return d["title"]
    if d.get("parent"):
        t = hn_story_title(d["parent"], titles, hops + 1)
        if t:
            titles[key] = t
        return t
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default="reddit,rtlsdr", help="comma list of reddit,rtlsdr,hackaday,hn")
    ap.add_argument("--subreddits", default=",".join(DEFAULT_SUBREDDITS))
    ap.add_argument("--reddit-via", choices=["auto", "arctic", "api", "archive"], default="auto")
    ap.add_argument("--reddit-archive", metavar="DIR", help="local Reddit archive dump (implies --reddit-via archive)")
    ap.add_argument("--reddit-since", default="2010-01-01", help="arctic: earliest post date to pull (YYYY-MM-DD)")
    ap.add_argument("--arctic-no-posts", action="store_true", help="arctic: comments only, skip the slow full post scan")
    ap.add_argument("--reddit-pages", type=int, default=10, help="search pages (x100 posts) per query and sort")
    ap.add_argument("--hn-since", default="2015-01", help="first month of the HN archive to scan (YYYY-MM)")
    ap.add_argument("--hn-until", default=None)
    ap.add_argument("--limit", type=int, default=None, help="per-source cap on threads/posts/pages, for testing")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    w = Writer(OUT)
    subs = [s.strip() for s in args.subreddits.split(",") if s.strip()]
    for src in [s.strip() for s in args.sources.split(",") if s.strip()]:
        before = w.added
        if src == "reddit":
            via = args.reddit_via
            if via == "auto":
                via = "archive" if args.reddit_archive else "api" if os.environ.get("REDDIT_CLIENT_ID") else "arctic"
            log(f"reddit: via {via}")
            if via == "archive":
                collect_reddit_archive(w, args.reddit_archive, subs)
            elif via == "api":
                collect_reddit(w, subs, args.reddit_pages, args.limit)
            else:
                collect_arctic(w, subs, args.reddit_since, args.limit, scan_posts=not args.arctic_no_posts)
        elif src == "rtlsdr":
            collect_rtlsdr(w, args.limit)
        elif src == "hackaday":
            collect_hackaday(w, args.limit)
        elif src == "hn":
            seed_ids = {m["mention_id"].rsplit(":", 1)[0] for m in read_jsonl(DATA / "01_seed_mentions.jsonl")}
            collect_hn(w, args.hn_since, args.hn_until, seed_ids)
        else:
            log(f"unknown source {src}")
        log(f"{src}: +{w.added - before} new items")
    log(f"collect: {len(w.seen)} items total in {OUT.relative_to(DATA.parent)}")


if __name__ == "__main__":
    main()
