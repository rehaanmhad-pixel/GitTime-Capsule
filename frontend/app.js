(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Colour ramps (RGB stops): quiet to busy.
  const SEDIMENT = [[226, 213, 172], [205, 160, 66], [140, 92, 44], [88, 56, 30]];
  const HEAT = [[44, 122, 123], [201, 183, 122], [227, 154, 14]];

  const LEVEL_TEXT = {
    deep: "Deep: imports traced between files",
    metrics: "Line counts only",
    basic: "Git history and line counts",
    binary: "Git history only",
  };
  const CATEGORY_TEXT = {
    all: "Everything",
    source: "Source code",
    tests: "Tests",
    docs: "Docs",
    config: "Config",
    other: "Other",
  };
  const PAGE_SIZE = 40;

  const state = {
    data: null,
    files: [],
    byPath: new Map(),
    imports: new Map(),
    importedBy: new Map(),
    months: [],
    maxScore: 1,
    filters: { category: "source", language: "all", search: "", sort: "score" },
    shown: PAGE_SIZE,
    selected: null,
    lastFocus: null,
  };

  // ---------- helpers ----------
  const fmt = (n) => (n === null || n === undefined ? "-" : Number(n).toLocaleString());

  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const kid of kids) if (kid !== null && kid !== undefined) node.append(kid);
    return node;
  }

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    return node;
  }

  function mix(stops, t) {
    t = Math.max(0, Math.min(1, t));
    const pos = t * (stops.length - 1);
    const i = Math.min(Math.floor(pos), stops.length - 2);
    const u = pos - i;
    const a = stops[i];
    const b = stops[i + 1];
    return "rgb(" + a.map((v, k) => Math.round(v + (b[k] - v) * u)).join(",") + ")";
  }

  function longDate(iso) {
    return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
    });
  }

  function monthLabel(ym) {
    return new Date(ym + "-01T00:00:00Z").toLocaleDateString(undefined, {
      year: "numeric", month: "long", timeZone: "UTC",
    });
  }

  function splitPath(path) {
    const slash = path.lastIndexOf("/");
    return slash >= 0
      ? { dir: path.slice(0, slash + 1), base: path.slice(slash + 1) }
      : { dir: "", base: path };
  }

  // Fill in months with no commits so layers sit in true time order.
  function fillMonths(list) {
    if (!list.length) return [];
    const byMonth = new Map(list.map((m) => [m.month, m]));
    let [y, m] = list[0].month.split("-").map(Number);
    const [endY, endM] = list[list.length - 1].month.split("-").map(Number);
    const out = [];
    while (y < endY || (y === endY && m <= endM)) {
      const key = y + "-" + String(m).padStart(2, "0");
      out.push(byMonth.get(key) || { month: key, commits: 0, added: 0, deleted: 0 });
      m += 1;
      if (m > 12) { m = 1; y += 1; }
    }
    return out;
  }

  function setStatus(message, kind) {
    const s = $("status");
    s.textContent = message;
    s.className = "status" + (kind ? " " + kind : "");
  }

  // ---------- tooltip ----------
  function showTip(x, y, title, lines) {
    const tip = $("tip");
    tip.replaceChildren(el("div", { class: "t", text: title }), ...lines.map((t) => el("div", { text: t })));
    tip.hidden = false;
    const w = tip.offsetWidth;
    const left = x + 14 + w > window.innerWidth ? x - w - 14 : x + 14;
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = y + 14 + "px";
  }
  function hideTip() { $("tip").hidden = true; }

  // ---------- loading ----------
  async function excavate(path) {
    setStatus("Digging through the history… large repositories can take a minute.", "busy");
    $("go").disabled = true;
    $("go").textContent = "Digging…";
    try {
      let res;
      let data;
      try {
        res = await fetch("/api/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        });
        data = await res.json();
      } catch (err) {
        setStatus("Could not reach GitTime-Capsule’s local backend. Check that python backend/app.py is still running.", "error");
        return;
      }
      if (!res.ok) {
        setStatus(data.error || "Something went wrong.", "error");
        return;
      }
      load(data);
    } finally {
      $("go").disabled = false;
      $("go").textContent = "Begin excavation";
    }
  }

  function load(data) {
    state.data = data;
    state.files = data.files;
    state.byPath = new Map(data.files.map((f) => [f.path, f]));
    state.imports = new Map();
    state.importedBy = new Map();
    for (const [from, to] of data.edges) {
      if (!state.imports.has(from)) state.imports.set(from, []);
      state.imports.get(from).push(to);
      if (!state.importedBy.has(to)) state.importedBy.set(to, []);
      state.importedBy.get(to).push(from);
    }
    state.maxScore = Math.max(1, ...data.files.map((f) => f.score));

    const hasSource = data.files.some((f) => f.category === "source");
    state.filters = { category: hasSource ? "source" : "all", language: "all", search: "", sort: "score" };
    state.shown = PAGE_SIZE;
    $("search").value = "";
    $("sort").value = "score";

    setStatus("");
    $("welcome").hidden = true;
    $("report").hidden = false;
    closeInspector(true);

    renderReport();
    renderStrata();
    renderScoreExplainer();
    renderControls();
    renderTable();
    renderExcavators();
    renderLanguages();
  }

  // ---------- site report ----------
  function renderReport() {
    const o = state.data.overview;
    $("report-text").replaceChildren(
      el("p", { class: "fieldnote" },
        el("strong", { text: fmt(o.non_merge_commits) }), " commits by ",
        el("strong", { text: fmt(o.total_contributors) }), " people, from ",
        longDate(o.first_commit), " to ", longDate(o.last_commit),
        ". The latest layer holds ",
        el("strong", { text: fmt(o.files_at_head) }), " files. Over the life of the project, ",
        el("strong", { text: fmt(o.lines_added) }), " lines were added and ",
        el("strong", { text: fmt(o.lines_deleted) }), " were removed."),
      el("p", { class: "fine", text: fmt(o.merge_commits) + " merge commits are listed apart and left out of these counts. A renamed file counts as removed and added again." }),
      el("p", { class: "fine" }, "Site: ", el("code", { text: o.path }))
    );
  }

  // ---------- strata ----------
  function renderStrata() {
    const months = fillMonths(state.data.timeline);
    state.months = months;
    const svg = $("strata-svg");
    const ticks = $("ticks");
    svg.replaceChildren();
    ticks.replaceChildren();
    if (!months.length) return;

    const maxCommits = Math.max(1, ...months.map((m) => m.commits));
    const weight = (m) => (m.commits > 0 ? Math.pow(m.commits, 0.55) : 0.45);
    const total = months.reduce((sum, m) => sum + weight(m), 0);
    const H = 1000;
    const bodyH = $("strata-body").clientHeight || 600;

    let y = 0;
    let lastYear = null;
    let lastTickPx = -100;
    for (let i = months.length - 1; i >= 0; i--) {
      const m = months[i];
      const h = (H * weight(m)) / total;
      svg.appendChild(svgEl("rect", {
        x: 0,
        y: y.toFixed(2),
        width: 100,
        height: h.toFixed(2),
        "data-i": i,
        fill: m.commits > 0 ? mix(SEDIMENT, Math.sqrt(m.commits / maxCommits)) : "#cbc3a4",
        stroke: "rgba(40,28,14,0.35)",
        "stroke-width": 0.5,
        "vector-effect": "non-scaling-stroke",
      }));

      const year = m.month.slice(0, 4);
      if (year !== lastYear) {
        lastYear = year;
        const px = (y / H) * bodyH;
        if (px - lastTickPx >= 18) {
          lastTickPx = px;
          ticks.append(el("span", { class: "tick", text: year, style: "top:" + ((y / H) * 100).toFixed(2) + "%" }));
        }
      }
      y += h;
    }

    $("cap-latest").textContent = monthLabel(months[months.length - 1].month);
    $("cap-first").textContent = monthLabel(months[0].month);
  }

  // ---------- dig sites ----------
  function renderScoreExplainer() {
    const s = state.data.scoring;
    const pct = (w) => Math.round(w * 100) + "%";
    $("score-explainer").replaceChildren(
      el("p", { text: "Every file gets a score from 0 to 100. Each ingredient is scaled against the highest value in this repository, then they are blended:" }),
      el("ul", {},
        el("li", { text: pct(s.frequency_weight) + " how many commits touched the file" }),
        el("li", { text: pct(s.churn_weight) + " how many lines were added and removed (scaled logarithmically, so one huge commit doesn’t drown out the rest)" }),
        el("li", { text: pct(s.recent_weight) + " how many commits landed in the " + s.recent_days + " days before the latest commit" }),
        el("li", { text: pct(s.size_weight) + " how many non-blank lines the file has (also logarithmic)" })),
      el("p", { text: "A high score means changes pile up here, so it may deserve a look. It does not mean the file is buggy or that it is technical debt." })
    );
  }

  function renderControls() {
    const counts = { all: state.files.length };
    for (const f of state.files) counts[f.category] = (counts[f.category] || 0) + 1;

    const chips = $("chips");
    chips.replaceChildren();
    for (const key of ["all", "source", "tests", "docs", "config", "other"]) {
      if (key !== "all" && !counts[key]) continue;
      chips.append(el("button", {
        type: "button",
        class: "chip",
        "data-cat": key,
        "aria-pressed": String(state.filters.category === key),
        text: CATEGORY_TEXT[key] + " (" + fmt(counts[key] || 0) + ")",
        onclick: () => {
          state.filters.category = key;
          state.shown = PAGE_SIZE;
          syncChips();
          renderTable();
        },
      }));
    }

    const langCounts = new Map();
    for (const f of state.files) langCounts.set(f.language, (langCounts.get(f.language) || 0) + 1);
    const select = $("language");
    select.replaceChildren(el("option", { value: "all", text: "All languages" }));
    [...langCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .forEach(([lang, n]) => select.append(el("option", { value: lang, text: lang + " (" + fmt(n) + ")" })));
    select.value = "all";
  }

  function syncChips() {
    document.querySelectorAll("#chips .chip").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.cat === state.filters.category));
    });
  }

  function filteredFiles() {
    const { category, language, search, sort } = state.filters;
    const q = search.trim().toLowerCase();
    const keys = { score: "score", commits: "commits", churn: "churn", recent: "recent_commits", size: "code_lines" };
    const key = keys[sort] || "score";
    return state.files
      .filter((f) =>
        (category === "all" || f.category === category) &&
        (language === "all" || f.language === language) &&
        (!q || f.path.toLowerCase().includes(q)))
      .sort((a, b) => ((b[key] ?? -1) - (a[key] ?? -1)) || (b.score - a.score));
  }

  function renderTable() {
    const list = filteredFiles();
    const shown = list.slice(0, state.shown);
    const body = $("sites-body");
    body.replaceChildren(...shown.map(row));

    $("empty").hidden = list.length > 0;
    $("result-count").textContent = list.length
      ? "Showing " + fmt(shown.length) + " of " + fmt(list.length) + " files. Scores are relative to the whole repository, even when filtered."
      : "";
    const more = $("more");
    more.hidden = shown.length >= list.length;
    more.textContent = "Show " + Math.min(PAGE_SIZE, list.length - shown.length) + " more";
  }

  function row(f) {
    const { dir, base } = splitPath(f.path);
    const t = f.score / state.maxScore;
    const sub = f.language + (f.category !== "source" ? ", " + CATEGORY_TEXT[f.category].toLowerCase() : "");
    return el("tr", { "data-path": f.path, class: f.path === state.selected ? "selected" : "" },
      el("td", { class: "file" },
        el("button", { type: "button", class: "filelink", title: f.path },
          el("span", { class: "dir", text: dir }), el("span", { class: "base", text: base })),
        el("span", { class: "sub", text: sub })),
      el("td", { class: "score" },
        el("span", { class: "bar" },
          el("span", { class: "fill", style: "width:" + Math.max(3, t * 100).toFixed(1) + "%;background:" + mix(HEAT, t) })),
        el("span", { class: "num", text: f.score.toFixed(1) })),
      el("td", { class: "n", text: fmt(f.commits) }),
      el("td", { class: "n", text: fmt(f.churn) }),
      el("td", { class: "n", text: fmt(f.authors) }),
      el("td", { class: "n", text: f.last_changed || "-" }));
  }

  function markSelected() {
    document.querySelectorAll("#sites-body tr").forEach((tr) => {
      tr.classList.toggle("selected", tr.dataset.path === state.selected);
    });
  }

  // ---------- excavators ----------
  function renderExcavators() {
    const total = state.data.overview.non_merge_commits || 1;
    $("excavators").replaceChildren(...state.data.contributors.map((c) => {
      const share = (c.commits / total) * 100;
      const shareText = share < 1 ? "<1%" : Math.round(share) + "%";
      const isBot = /\[bot\]/i.test(c.name);
      return el("li", { class: "tag" },
        el("div", {}, el("span", { class: "name", text: c.name }), isBot ? el("span", { class: "badge", text: "bot" }) : null),
        el("div", { text: fmt(c.commits) + " commits (" + shareText + ")" }),
        el("div", { class: "meter", "aria-hidden": "true" },
          el("span", { style: "width:" + Math.max(2, share).toFixed(1) + "%" })),
        el("div", { class: "lines", text: "+" + fmt(c.added) + " / −" + fmt(c.deleted) + " lines" }));
    }));
  }

  // ---------- languages ----------
  function renderLanguages() {
    $("lang-body").replaceChildren(...state.data.languages.map((l) =>
      el("tr", {},
        el("td", { text: l.language }),
        el("td", { text: LEVEL_TEXT[l.level] || l.level }),
        el("td", { class: "n", text: fmt(l.files) }),
        el("td", { class: "n", text: fmt(l.code_lines) }))));

    const d = state.data.dependencies;
    let note = fmt(d.links) + " import links were traced between " + fmt(d.files_with_links) +
      " files. A link is only recorded when the imported file exists inside this repository, so a missing link means “not found”, not “no dependency”.";
    if (state.data.edges_truncated) note += " Only the first part of the links is kept in this view.";
    $("dep-note").textContent = note;
  }

  // ---------- file inspector ----------
  function linkList(title, paths, emptyText) {
    const section = el("div", { class: "links" }, el("h3", { text: title + " (" + (paths ? paths.length : 0) + ")" }));
    if (!paths || !paths.length) {
      section.append(el("p", { class: "fine", text: emptyText }));
      return section;
    }
    const ul = el("ul", {});
    for (const p of paths.slice(0, 12)) {
      ul.append(el("li", {}, state.byPath.has(p)
        ? el("button", { type: "button", text: p, onclick: () => openInspector(p) })
        : el("span", { class: "plain", text: p })));
    }
    if (paths.length > 12) ul.append(el("li", { class: "fine", text: "and " + (paths.length - 12) + " more" }));
    section.append(ul);
    return section;
  }

  function openInspector(path) {
    const f = state.byPath.get(path);
    if (!f) return;
    if (!state.selected) state.lastFocus = document.activeElement;
    state.selected = path;
    markSelected();

    const days = state.data.scoring.recent_days;
    const stats = [
      ["Dig-site score", f.score.toFixed(1) + " of 100"],
      ["Commits", fmt(f.commits)],
      ["Recent commits", fmt(f.recent_commits)],
      ["People", fmt(f.authors)],
      ["Lines changed", fmt(f.churn) + " (+" + fmt(f.added) + " / −" + fmt(f.deleted) + ")", true],
      ["Non-blank lines now", f.code_lines === null ? "Not counted" : fmt(f.code_lines)],
      ["Most commits by", f.top_author + " (" + Math.round(f.top_author_share * 100) + "%)"],
      ["First changed", f.first_changed],
      ["Last changed", f.last_changed],
    ];

    const history = el("div", {}, el("p", { class: "fine", text: "Reading this file’s history…" }));

    const box = $("inspector");
    box.replaceChildren(
      el("button", { type: "button", class: "close", "aria-label": "Close file details", text: "Close", onclick: () => closeInspector() }),
      el("h2", { id: "insp-title", tabindex: "-1", text: splitPath(path).base }),
      el("p", { class: "path", text: path }),
      el("div", { class: "pills" },
        el("span", { class: "pill", text: f.language }),
        el("span", { class: "pill", text: CATEGORY_TEXT[f.category] }),
        el("span", { class: "pill", text: LEVEL_TEXT[f.level] || f.level })),
      el("dl", { class: "stats" }, ...stats.map(([k, v, wide]) =>
        el("div", { class: "stat" + (wide ? " wide" : "") }, el("dt", { text: k }), el("dd", { text: v })))),
      el("p", { class: "fine", text: "Recent means the " + days + " days before the repository’s latest commit. The score points to where changes pile up. It is not a verdict on the code." }),
      linkList("Imports", state.imports.get(path), f.level === "deep" ? "No imports of other files in this repository were found." : "Imports aren’t traced for this kind of file."),
      linkList("Imported by", state.importedBy.get(path), "No traced file imports this one."),
      el("h3", { text: "History" }),
      history
    );
    box.hidden = false;
    requestAnimationFrame(() => box.classList.add("open"));
    $("insp-title").focus();
    loadHistory(path, history);
  }

  async function loadHistory(path, box) {
    try {
      const res = await fetch("/api/file-history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: state.data.overview.path, file: path }),
      });
      const data = await res.json();
      if (state.selected !== path) return;
      if (!res.ok) {
        box.replaceChildren(el("p", { class: "fine", text: data.error || "Could not read this file’s history." }));
        return;
      }
      box.replaceChildren(
        el("p", { class: "fine", text: "Commits per month, from the first to the latest change." }),
        sparkline(data.monthly),
        el("h3", { text: "Latest changes" }),
        el("ol", { class: "recent" }, ...data.recent.map((c) =>
          el("li", {},
            el("span", { class: "when", text: c.date }), " ",
            el("span", { text: c.author }),
            el("div", { class: "subj", text: c.subject }),
            el("div", { class: "delta", text: "+" + fmt(c.added) + " / −" + fmt(c.deleted) + " lines" }))))
      );
    } catch (err) {
      if (state.selected === path) {
        box.replaceChildren(el("p", { class: "fine", text: "Could not read this file’s history." }));
      }
    }
  }

  function sparkline(monthly) {
    const months = fillMonths(monthly);
    const n = months.length;
    const max = Math.max(1, ...months.map((m) => m.commits));
    const svg = svgEl("svg", {
      viewBox: "0 0 " + n + " 32",
      preserveAspectRatio: "none",
      class: "spark",
      role: "img",
      "aria-label": "Commits to this file per month",
    });
    months.forEach((m, i) => {
      if (!m.commits) return;
      const h = Math.max(1.5, (32 * m.commits) / max);
      svg.appendChild(svgEl("rect", { x: i, y: 32 - h, width: 0.85, height: h }));
    });
    return el("div", {}, svg,
      el("div", { class: "sparkaxis" },
        el("span", { text: monthLabel(months[0].month) }),
        el("span", { text: monthLabel(months[n - 1].month) })));
  }

  function closeInspector(immediate) {
    const box = $("inspector");
    state.selected = null;
    markSelected();
    box.classList.remove("open");
    const finish = () => { if (!state.selected) box.hidden = true; };
    if (immediate) {
      finish();
    } else {
      setTimeout(finish, 220);
      if (state.lastFocus) {
        try { state.lastFocus.focus(); } catch (err) { /* element may be gone */ }
      }
    }
    state.lastFocus = null;
  }

  // ---------- wiring ----------
  $("open-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const path = $("path").value.trim();
    if (!path) {
      setStatus("Type the folder of a Git repository first, for example ~/test-repos/click.", "error");
      return;
    }
    try { localStorage.setItem("gittime-capsule-path", path); } catch (err) { /* storage unavailable */ }
    excavate(path);
  });

  $("search").addEventListener("input", (e) => {
    state.filters.search = e.target.value;
    state.shown = PAGE_SIZE;
    renderTable();
  });
  $("language").addEventListener("change", (e) => {
    state.filters.language = e.target.value;
    state.shown = PAGE_SIZE;
    renderTable();
  });
  $("sort").addEventListener("change", (e) => {
    state.filters.sort = e.target.value;
    state.shown = PAGE_SIZE;
    renderTable();
  });
  $("more").addEventListener("click", () => {
    state.shown += PAGE_SIZE;
    renderTable();
  });
  $("sites-body").addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (tr && tr.dataset.path) openInspector(tr.dataset.path);
  });

  const strataSvg = $("strata-svg");
  strataSvg.addEventListener("mousemove", (e) => {
    const index = e.target.getAttribute ? e.target.getAttribute("data-i") : null;
    if (index === null) { hideTip(); return; }
    const m = state.months[Number(index)];
    if (!m) { hideTip(); return; }
    showTip(e.clientX, e.clientY, monthLabel(m.month), m.commits
      ? [fmt(m.commits) + " commits", "+" + fmt(m.added) + " / −" + fmt(m.deleted) + " lines"]
      : ["No commits this month"]);
  });
  strataSvg.addEventListener("mouseleave", hideTip);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selected) closeInspector();
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.data) renderStrata(); }, 150);
  });

  try {
    const saved = localStorage.getItem("gittime-capsule-path");
    if (saved) $("path").value = saved;
  } catch (err) { /* storage unavailable */ }
})();