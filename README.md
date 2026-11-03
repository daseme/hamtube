# HamTube

A one-time, static catalog of the amateur radio videos that hams keep recommending
to each other. It borrows [HackerTube](https://hackertube.org)'s method: count real
recommendations from different people over time, and let Claude separate praise from
jokes and criticism. The site is plain HTML/CSS/JS on GitHub Pages; a Python pipeline
runs once on your machine and writes `catalog.json`.

The design is in [spec.md](spec.md).

## Layout

```
pipeline/        one script per stage, run in order; common.py holds shared helpers
  keywords.txt   radio keywords used to filter general-topic sources
  topics.json    the topic taxonomy Claude assigns from
  start_here.txt optional: hand-picked video ids for the Start Here path (one per line)
data/            stage outputs and raw caches (gitignored)
docs/            GitHub Pages root: index.html, app.js, style.css, catalog.json
```

## Setup

1. Python 3.10 or newer. Create a virtualenv and install:

   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY` for stages 4 and 8
   - `YOUTUBE_API_KEY` for stage 7 (free; [how to get one](https://hackertube.org/download/DATA.md), 5 minutes).
     Without it stage 7 falls back to YouTube's oEmbed endpoint, which confirms a video
     exists and gives its title and channel but no duration or upload date. Fine for a dry
     run, not for publishing.
   - Reddit needs no credentials by default: stage 2 reads the four subreddits from the
     Arctic Shift public archive API, every post and comment since 2010. That takes a few
     hours for r/amateurradio and resumes if interrupted. `REDDIT_CLIENT_ID` /
     `REDDIT_CLIENT_SECRET` from a Reddit "script" app (https://www.reddit.com/prefs/apps)
     switch it to the official API instead, which is quicker but search-based and misses
     older threads. A local dump works too: `--reddit-archive DIR`.

## Run

```
.venv/bin/python pipeline/01_seed.py          # HackerTube export -> radio subset
.venv/bin/python pipeline/02_collect.py       # Reddit + RTL-SDR.com (default sources)
.venv/bin/python pipeline/03_extract.py       # posts -> one row per (post, video)
.venv/bin/python pipeline/04_classify.py      # Claude labels each mention
.venv/bin/python pipeline/05_qualify.py       # >= 2 different people recommended it
.venv/bin/python pipeline/06_score.py
.venv/bin/python pipeline/07_enrich.py        # YouTube metadata, drop dead videos
.venv/bin/python pipeline/08_describe.py      # Claude writes cards, topics, level
.venv/bin/python pipeline/09_dedupe.py
.venv/bin/python pipeline/10_export.py        # data/catalog.json and docs/catalog.json
```

Or `pipeline/run_all.sh [collect options]`. Every stage writes its own file, so a stage
can be rerun without redoing earlier ones. Stages 2, 4, 7 and 8 keep progress caches
under `data/raw/` and resume where they stopped.

Useful options:

- `02_collect.py --sources reddit,rtlsdr,hackaday,hn` adds Hackaday's Radio Hacks
  category and the open-index Hacker News archive. The HN source downloads one parquet
  file per month (about 100 MB each; `pip install pyarrow huggingface_hub`), so bound it
  with `--hn-since 2018-01`.
- `04_classify.py --batch` and `08_describe.py --batch` use the Message Batches API at
  half price and wait for the batch to finish (usually well under an hour).
- `--limit N` on stages 2, 4 and 8 for a cheap trial run.
- `CLASSIFY_MODEL` (default `claude-sonnet-5-5`) and `DESCRIBE_MODEL` (default
  `claude-opus-5-5`) in `.env` override the models.

Before publishing, spot-check 20 random videos in `data/catalog.json`: label quality,
summary accuracy, level. `data/08_rejected.jsonl` lists videos Claude judged off-topic,
and `data/09_duplicates.jsonl` the duplicate uploads it folded.

## Preview and publish

```
python3 -m http.server -d docs 8000      # then open http://localhost:8000
```

Commit `docs/` (including `catalog.json`) and push. In the repository settings, set
GitHub Pages to serve from the `docs/` folder on the main branch.

## Method in short

1. Keep posts and comments that link a YouTube video and carry at least 100 characters
   of the author's own text.
2. Claude Sonnet labels each mention: strong recommendation, recommendation, neutral
   reference, joke, criticism. HackerTube's own labels are reused as-is.
3. A video stays if at least two different people recommended it.
4. Score = distinct months with a recommendation + 0.5 x months with a strong
   recommendation + 1 per additional source.
5. Claude writes the two-sentence card, picks 1 to 3 topics, assigns a level
   (Technician, General, Advanced), notes whether it helps with the Technician or
   General exam, and rejects videos that are not about radio. Advanced videos stay in
   `data/` but are left out of the published catalog unless `10_export.py
   --include-advanced` is used: HamTube is for early hams.

## Attribution

HackerTube data by Andrej Karpathy, [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/);
Hacker News archive from [open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news),
also ODC-By 1.0. The About page on the site repeats this credit.
