/* HamTube front end: one page, hash routes, reads catalog.json. No build step. */
(() => {
  "use strict";

  const view = document.getElementById("view");
  const qInput = document.getElementById("q");
  const PAGE = 48;
  const EXAMS = [["Technician", "Technician exam"], ["General", "General exam"]];
  const DURATIONS = [["short", "Under 15 min"], ["medium", "15 to 60 min"], ["long", "Over 60 min"]];
  const WATCHED_OPTS = [["all", "All"], ["unwatched", "Unwatched"], ["watched", "Watched"]];
  const SOURCE_NAMES = { hn: "Hacker News", reddit: "Reddit", rtlsdr: "RTL-SDR.com", hackaday: "Hackaday" };
  const LABEL_NAMES = { strong_recommendation: "strong rec", recommendation: "rec", neutral_reference: "reference", joke: "joke", criticism: "critical" };
  // Topic order used to assemble the automatic Start Here path.
  const START_ORDER = ["getting-started", "licensing", "vhf-uhf", "repeaters", "antennas", "equipment", "portable", "digital-modes", "sdr", "propagation", "hf", "emergency", "mesh", "aprs", "morse-cw", "satellites"];

  let catalog = null;
  let byId = new Map();
  let shown = PAGE;

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const thumb = (id) => `https://i.ytimg.com/vi/${id}/hqdefault.jpg`;
  const fmtDur = (s) => {
    if (!s && s !== 0) return "";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  const bucket = (s) => (s == null ? null : s < 900 ? "short" : s <= 3600 ? "medium" : "long");
  const year = (d) => (d ? String(d).slice(0, 4) : "");
  const plural = (n, w, many) => `${n.toLocaleString()} ${n === 1 ? w : many || w + "s"}`;

  // Watched list lives only in this browser.
  const store = {
    key: "hamtube.watched",
    load() { try { return JSON.parse(localStorage.getItem(this.key) || "{}"); } catch (e) { return {}; } },
    save(o) { try { localStorage.setItem(this.key, JSON.stringify(o)); } catch (e) { /* private mode etc. */ } },
    has(id) { return Boolean(this.load()[id]); },
    toggle(id) { const o = this.load(); if (o[id]) delete o[id]; else o[id] = new Date().toISOString().slice(0, 10); this.save(o); return Boolean(o[id]); },
    count() { return Object.keys(this.load()).length; },
  };

  // ---------------------------------------------------------------- routing
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query = ""] = raw.split("?");
    const params = new URLSearchParams(query);
    const parts = path.split("/").filter(Boolean);
    return { page: parts[0] || "home", arg: parts[1] || "", params };
  }
  function buildHash(params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v);
    const s = qs.toString();
    return "#/" + (s ? "?" + s : "");
  }
  function filtersFrom(params) {
    return {
      q: params.get("q") || "",
      topic: params.get("topic") || "",
      level: params.get("level") || "",
      dur: params.get("dur") || "",
      source: params.get("source") || "",
      watched: params.get("watched") || "all",
      exam: params.get("exam") || "",
      view: params.get("view") || "",
    };
  }
  function setFilter(changes) {
    const f = filtersFrom(parseHash().params);
    Object.assign(f, changes);
    if (f.watched === "all") f.watched = "";
    shown = PAGE;
    location.hash = buildHash(f);
  }

  // ---------------------------------------------------------------- filtering
  function searchable(v) {
    return v._s || (v._s = [v.title, v.channel, v.summary, v.why_watch, ...(v.topics || []).map((t) => topicName(t))].join(" ").toLowerCase());
  }
  const topicName = (slug) => (catalog.topics.find((t) => t.slug === slug) || { name: slug }).name;

  function applyFilters(f) {
    const terms = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    const watched = store.load();
    return catalog.videos.filter((v) => {
      if (f.topic && !v.topics.includes(f.topic)) return false;
      if (f.level && v.level !== f.level) return false;
      if (f.dur && bucket(v.duration_sec) !== f.dur) return false;
      if (f.source && !v.sources.includes(f.source)) return false;
      if (f.exam && !(v.exam_prep === f.exam || v.exam_prep === "both")) return false;
      if (f.watched === "watched" && !watched[v.id]) return false;
      if (f.watched === "unwatched" && watched[v.id]) return false;
      if (terms.length) { const s = searchable(v); if (!terms.every((t) => s.includes(t))) return false; }
      return true;
    });
  }

  // ---------------------------------------------------------------- rendering
  function chip(label, on, hash, count) {
    return `<a class="chip${on ? " on" : ""}" href="${esc(hash)}">${esc(label)}${count != null ? ` <span class="n">${count}</span>` : ""}</a>`;
  }
  function filterBar(f) {
    const with_ = (ch) => buildHash({ ...f, watched: f.watched === "all" ? "" : f.watched, ...ch });
    const rows = [];
    rows.push(`<div class="frow tabs"><span class="flabel">For</span>${chip("Everyone", !f.level, with_({ level: "" }))}${catalog.levels.map((l) => chip(l === "Advanced" ? "Advanced" : l + "s", f.level === l, with_({ level: f.level === l ? "" : l }))).join("")}</div>`);
    if (catalog.videos.some((v) => v.exam_prep && v.exam_prep !== "none")) rows.push(`<div class="frow"><span class="flabel">Study for</span>${chip("Any", !f.exam, with_({ exam: "" }))}${EXAMS.map(([k, l]) => chip(l, f.exam === k, with_({ exam: f.exam === k ? "" : k }))).join("")}</div>`);
    rows.push(`<div class="frow"><span class="flabel">Length</span>${chip("Any", !f.dur, with_({ dur: "" }))}${DURATIONS.map(([k, l]) => chip(l, f.dur === k, with_({ dur: f.dur === k ? "" : k }))).join("")}</div>`);
    if (catalog.sources.length > 1) rows.push(`<div class="frow"><span class="flabel">Source</span>${chip("All", !f.source, with_({ source: "" }))}${catalog.sources.map((s) => chip(s.name, f.source === s.slug, with_({ source: f.source === s.slug ? "" : s.slug }))).join("")}</div>`);
    rows.push(`<div class="frow"><span class="flabel">Topic</span>${chip("All", !f.topic, with_({ topic: "" }))}${catalog.topics.map((t) => chip(t.name, f.topic === t.slug, with_({ topic: f.topic === t.slug ? "" : t.slug }), t.count)).join("")}</div>`);
    if (store.count()) rows.push(`<div class="frow"><span class="flabel">Watched</span>${WATCHED_OPTS.map(([k, l]) => chip(l, (f.watched || "all") === k, with_({ watched: k === "all" ? "" : k }))).join("")}</div>`);
    return `<div class="filters">${rows.join("")}</div>`;
  }
  function card(v) {
    const seen = store.has(v.id);
    return `<a class="card" href="#/watch/${esc(v.id)}">
      <div class="thumb"><img loading="lazy" src="${thumb(v.id)}" alt="">${v.duration_sec != null ? `<span class="dur">${fmtDur(v.duration_sec)}</span>` : ""}${seen ? `<span class="seen">✓ Watched</span>` : ""}</div>
      <h3>${esc(v.title)}</h3>
      <div class="meta"><span>${esc(v.channel)}</span><span class="lvl lvl-${esc(v.level)}">${esc(v.level)}</span>${v.exam_prep && v.exam_prep !== "none" ? `<span class="exam">${esc(v.exam_prep === "both" ? "Exam prep" : v.exam_prep + " exam")}</span>` : ""}</div>
      <p class="blurb">${esc(v.summary)}</p>
    </a>`;
  }
  function topicSections(list, f) {
    const PER = 4;
    const with_ = (ch) => buildHash({ ...f, watched: f.watched === "all" ? "" : f.watched, ...ch });
    const seen = new Set();
    const sections = [];
    for (const t of catalog.topics) {
      const vids = list.filter((v) => v.topics[0] === t.slug && !seen.has(v.id));
      if (!vids.length) continue;
      vids.forEach((v) => seen.add(v.id));
      sections.push(`<section class="topic-sec">
        <h2><a href="${with_({ topic: t.slug })}">${esc(t.name)}</a> <span class="n">${plural(vids.length, "video")}</span>${vids.length > PER ? ` <a class="all" href="${with_({ topic: t.slug })}">See all →</a>` : ""}</h2>
        <div class="grid">${vids.slice(0, PER).map(card).join("")}</div></section>`);
    }
    return sections.join("");
  }
  function renderHome(params) {
    const f = filtersFrom(params);
    if (qInput.value !== f.q) qInput.value = f.q;
    const list = applyFilters(f);
    const active = f.q || f.topic || f.level || f.dur || f.source || f.exam || (f.watched && f.watched !== "all");
    const byTopic = !f.topic && !f.q && f.view !== "list";
    const with_ = (ch) => buildHash({ ...f, watched: f.watched === "all" ? "" : f.watched, ...ch });
    const viewSwitch = !f.topic && !f.q ? ` · <a class="chip${byTopic ? " on" : ""}" href="${with_({ view: "" })}">By topic</a> <a class="chip${byTopic ? "" : " on"}" href="${with_({ view: "list" })}">Ranked list</a>` : "";
    const summary = `${plural(list.length, "video")}${active ? ` match · <a class="chip clear" href="#/">Clear filters</a>` : ", ranked by how often hams recommend them"}${viewSwitch}`;
    const body = !list.length ? `<p class="empty">No videos match. Try fewer filters.</p>`
      : byTopic ? topicSections(list, f)
      : `<div class="grid">${list.slice(0, shown).map(card).join("")}</div>` + (list.length > shown ? `<div class="more"><button class="btn" id="moreBtn">Show more (${list.length - shown} left)</button></div>` : "");
    view.innerHTML = filterBar(f) + `<p class="summary">${summary}</p>` + body;
    document.title = active ? "Browse - HamTube" : "HamTube";
    const more = document.getElementById("moreBtn");
    if (more) more.onclick = () => { shown += PAGE; renderHome(params); window.scrollBy(0, 200); };
  }

  function renderWatch(id) {
    const v = byId.get(id);
    if (!v) { view.innerHTML = `<p class="empty">That video is not in the catalog. <a href="#/">Back to the list</a>.</p>`; return; }
    const seen = store.has(id);
    const player = v.embeddable === false
      ? `<div class="noembed"><a class="btn primary" href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener">Watch on YouTube ↗</a></div>`
      : `<iframe src="https://www.youtube-nocookie.com/embed/${esc(id)}?rel=0" title="${esc(v.title)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    const mentions = v.mentions.map((m) => `<div class="mention">
        <div class="who"><span class="tag ${esc(m.label)}">${esc(LABEL_NAMES[m.label] || m.label)}</span><span>${esc(SOURCE_NAMES[m.source] || m.source)}</span>${m.author ? `<span>· ${esc(m.author)}</span>` : ""}${m.date ? `<span>· ${esc(m.date)}</span>` : ""}</div>
        <blockquote>${esc(m.excerpt)}</blockquote>
        <a class="src" href="${esc(m.url)}" target="_blank" rel="noopener">Read the discussion ↗</a>
      </div>`).join("");
    view.innerHTML = `<div class="watch">
      <div>
        <div class="player">${player}</div>
        <h1>${esc(v.title)}</h1>
        <div class="meta"><span>${esc(v.channel)}</span>${v.published ? `<span>· ${esc(year(v.published))}</span>` : ""}${v.duration_sec != null ? `<span>· ${fmtDur(v.duration_sec)}</span>` : ""}<span class="lvl lvl-${esc(v.level)}">${esc(v.level)}</span>${v.exam_prep && v.exam_prep !== "none" ? `<span class="exam">helps with the ${esc(v.exam_prep === "both" ? "Technician and General exams" : v.exam_prep + " exam")}</span>` : ""}</div>
        <div class="actions">
          <button class="btn${seen ? " on" : ""}" id="watchedBtn">${seen ? "✓ Watched" : "Mark as watched"}</button>
          <a class="btn" href="https://www.youtube.com/watch?v=${esc(id)}" target="_blank" rel="noopener">Open on YouTube ↗</a>
        </div>
        <div class="summary-box">
          <p>${esc(v.summary)}</p>
          ${v.why_watch ? `<p class="why">${esc(v.why_watch)}</p>` : ""}
          <p class="topics">${v.topics.map((t) => `<a class="chip" href="${buildHash({ topic: t })}">${esc(topicName(t))}</a>`).join("")}</p>
        </div>
        <p class="facts">Recommended by ${plural(v.people, "person", "people")} across ${plural(v.mention_count, "mention")}${v.first_mention ? ` between ${esc(year(v.first_mention))} and ${esc(year(v.last_mention))}` : ""} · score <span class="score">${v.score}</span> · ${v.sources.map((s) => esc(SOURCE_NAMES[s] || s)).join(", ")}</p>
      </div>
      <aside class="side">
        <h2>Who recommended it</h2>
        ${mentions}
      </aside>
    </div>`;
    document.title = `${v.title} - HamTube`;
    document.getElementById("watchedBtn").onclick = (e) => {
      const on = store.toggle(id);
      e.target.textContent = on ? "✓ Watched" : "Mark as watched";
      e.target.classList.toggle("on", on);
    };
    window.scrollTo(0, 0);
  }

  function startPath() {
    if (catalog.start_here && catalog.start_here.length) return catalog.start_here.map((id) => byId.get(id)).filter(Boolean);
    const tech = catalog.videos.filter((v) => v.level === "Technician");
    const picked = [], used = new Set();
    for (const slug of START_ORDER) {
      const v = tech.find((x) => !used.has(x.id) && x.topics[0] === slug) || tech.find((x) => !used.has(x.id) && x.topics.includes(slug));
      if (v) { picked.push(v); used.add(v.id); }
      if (picked.length >= 12) break;
    }
    for (const v of tech) { if (picked.length >= 12) break; if (!used.has(v.id)) { picked.push(v); used.add(v.id); } }
    return picked;
  }
  function renderStart() {
    const path = startPath();
    const watched = store.load();
    view.innerHTML = `<div class="prose"><h1>Start Here</h1>
      <p class="intro">A short path for a new Technician, in order: what the hobby is, getting licensed, your first handheld and repeater contacts, then antennas and the modes you will meet next. Every video here is one hams repeatedly recommended to beginners. Mark them watched as you go. Ready for the next ticket? <a href="${buildHash({ exam: "General" })}">Videos that help with the General exam</a>.</p></div>
      ${path.length ? `<ol class="path">${path.map((v, i) => `<li class="step">
        <span class="num">${i + 1}</span>
        <a class="thumb" href="#/watch/${esc(v.id)}"><img loading="lazy" src="${thumb(v.id)}" alt="">${v.duration_sec != null ? `<span class="dur">${fmtDur(v.duration_sec)}</span>` : ""}${watched[v.id] ? `<span class="seen">✓</span>` : ""}</a>
        <div class="body"><h3><a href="#/watch/${esc(v.id)}">${esc(v.title)}</a></h3>
          <div class="meta"><span>${esc(v.channel)}</span><span>· ${esc(topicName(v.topics[0]))}</span></div>
          <p>${esc(v.why_watch || v.summary)}</p></div>
      </li>`).join("")}</ol>` : `<p class="empty">No Technician-level videos yet.</p>`}`;
    document.title = "Start Here - HamTube";
  }

  function renderAbout() {
    const c = catalog.counts || {};
    view.innerHTML = `<div class="prose">
      <h1>About HamTube</h1>
      <p>HamTube is a one-time, static catalog of the amateur radio videos that hams keep telling each other to watch. It borrows the method of <a href="https://hackertube.org" rel="noopener">HackerTube</a>: instead of asking an algorithm what is popular, count real recommendations from different people over time, and use a language model only to tell praise apart from jokes and criticism.</p>
      <h2>How a video gets in</h2>
      <ol>
        <li>Posts and comments that link a YouTube video, with at least 100 characters of the author's own words, are collected from the sources below.</li>
        <li>Claude labels each mention as a strong recommendation, recommendation, neutral reference, joke or criticism, using the thread title for context.</li>
        <li>A video stays only if at least two different people recommended it.</li>
        <li>Score = distinct months with a recommendation + half the months with a strong recommendation + 1 per extra source.</li>
        <li>Claude writes the two-sentence card, assigns topics and a skill level (Technician or General; Advanced material is left out for now), and flags videos that are not actually about radio.</li>
        <li>Metadata comes from the YouTube Data API; private and deleted videos are dropped.</li>
      </ol>
      <p>Labels and summaries are machine-written and spot-checked, not perfect. Every card links to the original discussions so you can judge for yourself.</p>
      <h2>Sources and attribution</h2>
      <ul>
        <li><a href="https://hackertube.org/data" rel="noopener">HackerTube export</a> by Andrej Karpathy, licensed <a href="https://opendatacommons.org/licenses/by/1-0/" rel="noopener">ODC-By 1.0</a>. HamTube's Hacker News mentions and their labels come from this export, filtered to radio topics.</li>
        <li><a href="https://huggingface.co/datasets/open-index/hacker-news" rel="noopener">open-index/hacker-news</a>, the Hacker News archive behind HackerTube, also ODC-By 1.0.</li>
        <li>Reddit: r/amateurradio, r/HamRadio, r/RTLSDR and r/meshtastic, via the Reddit API.</li>
        <li><a href="https://www.rtl-sdr.com" rel="noopener">RTL-SDR.com</a> blog posts that embed videos.</li>
        <li><a href="https://hackaday.com/category/radio-hacks/" rel="noopener">Hackaday</a> radio hacks write-ups.</li>
      </ul>
      <p>Videos play from YouTube through youtube-nocookie.com; nothing is hosted here. Thumbnails load from YouTube's image servers. Your watched list is stored only in this browser.</p>
      <h2>This build</h2>
      <p>Generated ${esc(catalog.generated)} with ${plural(c.videos || catalog.videos.length, "video")} and ${plural(c.mentions || 0, "mention")}. The pipeline and site source live in the repository this page is served from.</p>
    </div>`;
    document.title = "About - HamTube";
  }

  function route() {
    if (!catalog) return;
    const { page, arg, params } = parseHash();
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === (page === "watch" ? "" : page)));
    if (page === "watch") renderWatch(arg);
    else if (page === "start") renderStart();
    else if (page === "about") renderAbout();
    else renderHome(params);
  }

  // ---------------------------------------------------------------- boot
  let searchTimer;
  document.getElementById("searchForm").addEventListener("submit", (e) => { e.preventDefault(); setFilter({ q: qInput.value.trim() }); });
  qInput.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (parseHash().page !== "home") location.hash = "#/"; setFilter({ q: qInput.value.trim() }); }, 250); });
  window.addEventListener("hashchange", route);

  fetch("catalog.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      catalog = data;
      byId = new Map(catalog.videos.map((v) => [v.id, v]));
      document.getElementById("footStats").textContent = `${plural(catalog.videos.length, "video")} · built ${catalog.generated}`;
      route();
    })
    .catch((err) => {
      view.innerHTML = `<p class="empty">Could not load <code>catalog.json</code> (${esc(err.message)}).<br>Run the pipeline through <code>10_export.py</code>, then serve this folder, e.g. <code>python3 -m http.server -d docs</code>.</p>`;
    });
})();
