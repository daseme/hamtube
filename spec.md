# HamTube Spec

Nov 2, 2026 · @Kurt Olmstead

## Overview

HamTube is a one-time, static catalog of amateur radio videos that hams have repeatedly recommended in public discussion. It borrows HackerTube's method: count real recommendations from different people over time, and let Claude separate praise from jokes and criticism.

The site runs on GitHub Pages with no server. A script runs once on a local machine, writes a JSON catalog, and the catalog ships in the repo with the front end.

The primary audience is a new Technician licensee and friends learning alongside him, so every video carries a skill level.

## Scope and non-goals

In scope:

- A one-time collection run across the sources below, rerun by hand if ever needed
- Claude-based classification, scoring, topic tagging and skill-level tagging
- A static, searchable, filterable site on GitHub Pages
- Links from every video back to the discussions that recommended it

Out of scope for now:

- Scheduled updates, GitHub Actions and stored secrets
- User accounts, comments or submissions
- Hosting or downloading video; everything plays from YouTube
- Non-YouTube video platforms

## Sources

HackerTube's public export is the seed; the other sources fill in ham-specific and beginner material HN rarely covers.

| Source | What it adds | How to pull | Lean |
| --- | --- | --- | --- |
| [HackerTube export](https://hackertube.org/data) | 6,454 HN-vetted videos with labeled mentions, taxonomy and embeddings; filter to radio topics | Download once; ODC-By 1.0, attribution required | Technical |
| [Open-index HN archive](https://huggingface.co/datasets/open-index/hacker-news) | Ham videos that missed HackerTube's three-person, three-month bar | Hugging Face dataset, keyword filter | Technical |
| Reddit: r/amateurradio, r/HamRadio, r/RTLSDR, r/meshtastic | Volume, beginner questions, "watch this" threads | Reddit API (app key) or a one-time archive dump | Beginner to mid |
| Hackaday, radio hacks tag | Edited project write-ups that embed the maker's video | Article scrape | Technical |
| RTL-SDR.com blog | Highest-signal SDR coverage | Article scrape | Technical |
| QRZ.com and eHam forums | "Best video on X" threads from experienced hams | Forum scrape, light rate | Mid |
| ARRL (QST picks, podcasts) | Authoritative licensing and operating material | Manual list | Beginner |
| Southgate ARC, AmateurRadio.com | Surfaces channels the forums miss | Article scrape | Mixed |

Start with HackerTube, Reddit and RTL-SDR.com. Add the rest only if the catalog comes out thin.

## Pipeline

The pipeline copies HackerTube's method and runs once, in order. Each stage writes its own file so a stage can be rerun without redoing the ones before it.

1. **Seed.** Download the HackerTube export. Keep videos in radio-related categories or whose mentions match the keyword list (ham, amateur radio, SDR, antenna, HF, VHF, UHF, Meshtastic, APRS, Morse, QRP, Technician license).
2. **Collect.** Pull posts and comments from each added source. Keep any that contain a YouTube link plus at least 100 characters of the author's own text.
3. **Extract.** Normalize YouTube URLs to video IDs. One comment linking two videos becomes two mention rows.
4. **Classify.** Claude Sonnet labels each mention as strong recommendation, recommendation, neutral reference, joke or criticism, using the thread title as context.
5. **Qualify.** Keep a video only if it has at least one positive label from at least two different people. This is looser than HackerTube's three-person, three-month bar because the ham niche is smaller.
6. **Score.** Distinct months with a recommendation, plus half the distinct months with a strong recommendation, plus 1 for each additional source it appears in.
7. **Enrich.** Fetch title, channel, duration and upload date from the YouTube Data API. Drop videos that are private or deleted.
8. **Describe.** Claude writes a two-sentence card summary from the mentions and metadata, assigns topics, and tags a skill level: Technician, General, Extra or Engineering.
9. **Dedupe.** Flag likely duplicate uploads of the same talk by title and channel similarity; keep the higher-scored one.
10. **Export.** Write `catalog.json` for the site.

## Data model

One file, `catalog.json`, holds everything the site needs. A sample record:

```json
{
  "generated": "2026-11-15",
  "videos": [
    {
      "id": "YOUTUBE_ID",
      "title": "...",
      "channel": "...",
      "duration_sec": 1420,
      "published": "2021-04-09",
      "summary": "Two-sentence card text.",
      "topics": ["antennas", "hf"],
      "level": "Technician",
      "score": 5.5,
      "sources": ["hackertube", "reddit"],
      "mentions": [
        {
          "source": "reddit",
          "url": "https://...",
          "date": "2023-02-11",
          "label": "strong_recommendation",
          "excerpt": "Under 200 characters of the comment"
        }
      ]
    }
  ],
  "topics": [{"slug": "antennas", "name": "Antennas"}]
}
```

Keep at most five mentions per video in the published file, favoring different people and positive labels. Excerpts stay short; the link carries the reader to the full comment.

## Site

A single static page reads `catalog.json` in the browser. No build step and no framework is required; plain HTML, CSS and JavaScript keep it easy to maintain.

- **Home:** a grid of video cards ranked by score, with thumbnail, title, channel, duration, level and summary
- **Filters:** topic, skill level, duration (under 15 minutes, 15 to 60, over 60) and source
- **Search:** client-side text search over title, channel, summary and topics
- **Watch page:** privacy-enhanced YouTube embed (youtube-nocookie.com), the card details, and the mention list with links back to each discussion
- **Start Here:** a short, hand-picked path for new Technicians, drawn from the top Technician-level videos
- **Watched list:** stored in localStorage on the viewer's own browser
- **About:** method, sources, and the required attribution to HackerTube and the HN archive

Thumbnails load from YouTube's image servers by video ID, so no images ship in the repo.

## Repo and run steps

```
hamtube/
  pipeline/          Python scripts, one per stage
    01_seed.py ... 10_export.py
    keywords.txt
  data/              stage outputs (gitignored except final)
  docs/              GitHub Pages root
    index.html
    app.js
    style.css
    catalog.json
  .env.example       API key names, no values
  README.md
```

Run order, with each precondition before the step it gates:

1. Install Python 3.12 and run `pip install -r requirements.txt`.
2. Get an Anthropic API key, a YouTube Data API key and a Reddit app key. Put them in a local `.env` file, which stays out of git.
3. Run the stages in order: `python pipeline/01_seed.py` through `10_export.py`.
4. Spot-check 20 random videos and their labels before publishing.
5. Copy `catalog.json` into `docs/`, commit and push.
6. In the repo settings, set GitHub Pages to serve from the `docs/` folder on the main branch.

## Risks and open questions

The biggest risk is a thin catalog: HackerTube's whole export holds 6,454 videos across all topics, and radio is a small slice.

- **Thin seed.** If the HackerTube filter yields only a few dozen videos, lean on Reddit and the HN archive and accept the looser two-person bar.
- **Label errors.** About one in four HackerTube videos carry no positive label in their retained mentions. Decide whether to drop those or show them lower.
- **Reddit access.** API terms and rate limits may make a one-time archive dump easier than live calls.
- **Dead videos.** The export does not confirm a video is still up; the enrichment step must check.
- **Attribution.** The HackerTube data is ODC-By 1.0. The About page must credit HackerTube and the source archive.
- **Server code.** Karpathy had not published HackerTube's application code as of Oct. 31, so the pipeline is built fresh.

Open questions:

- [ ] Name: HamTube, or something else?
- [ ] Are the four skill levels right, or is Beginner / Intermediate / Advanced simpler?
- [ ] Include non-video podcast episodes from QSO Today and ARRL?

Sources: [HackerTube data page](https://hackertube.org/data), [Kingy AI analysis of the HackerTube export](https://kingy.ai/blog/hackertube-karpathy-claude-code/).
