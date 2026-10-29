// Dashboard for the self-learning email system. Polls the JSON API and renders
// three views: every contact's runs, the failures, and the GEPA optimizations.

const POLL_MS = 5000;
const DIFFS_URL = "https://esm.sh/@pierre/diffs@1.5.2";

const CRITERIA = {
  personalized: "Personalized",
  relevant_value: "Relevant value",
  clear_cta: "Clear CTA",
  concise: "Concise",
  tone: "Tone",
  subject_line: "Subject line",
  accurate: "Accurate",
};

const state = {
  tab: "contacts",
  stats: null,
  contacts: [],
  optimizations: [],
  versions: new Map(),
  open: new Set(), // expanded contact ids
  dataKey: null, // skip re-rendering when a poll returns identical data
  optimizationsKey: null, // re-render optimizations (and their diffs) only when they change
};

// --- helpers ----------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const fmt = (n, digits = 2) => (n == null ? "–" : Number(n).toFixed(digits));
const pct = (n) => (n == null ? "–" : `${Math.round(n * 100)}%`);

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const threshold = () => state.stats?.pass_threshold ?? 0.8;
const floor = () => state.stats?.criterion_floor ?? 0.5;

// --- data -------------------------------------------------------------------

let inFlight = false;

async function refresh() {
  if (inFlight) return; // never overlap polls; see /api/dashboard
  inFlight = true;
  try {
    const data = await getJSON("/api/dashboard");
    $(".live").classList.remove("error");
    const key = JSON.stringify(data);
    if (key === state.dataKey) return;
    state.dataKey = key;
    state.stats = data.stats;
    state.contacts = data.contacts;
    state.optimizations = data.optimizations;
    state.versions = new Map(data.versions.map((v) => [v.version, v]));
    render();
  } catch (err) {
    console.error(err);
    $(".live").classList.add("error");
  } finally {
    inFlight = false;
  }
}

// --- stats ------------------------------------------------------------------

function renderStats() {
  const c = state.contacts;
  const done = c.filter((x) => x.status !== "pending" && x.attempts.length);
  const firstPass = done.filter((x) => x.attempts[0].passed).length;
  const values = {
    total: c.length,
    sent: c.filter((x) => x.status === "sent").length,
    flagged: c.filter((x) => x.status === "flagged").length,
    pending: c.filter((x) => x.status === "pending").length,
    first_pass: done.length ? pct(firstPass / done.length) : "–",
    active_version: `v${state.stats.active_version}`,
    optimize_every: state.stats.optimize_every,
  };
  for (const el of document.querySelectorAll("[data-stat]")) {
    el.textContent = values[el.dataset.stat] ?? "–";
  }
  $('[data-count="contacts"]').textContent = c.length || "";
  $('[data-count="failed"]').textContent = values.flagged || "";
  $('[data-count="optimizations"]').textContent = state.optimizations.length || "";
}

// --- contacts ---------------------------------------------------------------

function contactTable(contacts, emptyText) {
  if (!contacts.length) return `<div class="empty">${esc(emptyText)}</div>`;
  const rows = contacts.map((c) => {
    const p = c.profile;
    const meta = [p.role, p.company].filter(Boolean).join(" · ") || p.email;
    const pills = c.attempts
      .map((a, i) => `<span class="pill ${a.passed ? "pass" : "fail"}" title="Attempt ${i + 1}">${fmt(a.score)}</span>`)
      .join("");
    const open = state.open.has(c.id);
    return `
      <tr class="row${open ? " open" : ""}" data-id="${c.id}">
        <td class="id">#${c.id}</td>
        <td><div class="who"><span class="name">${esc(p.name)}</span><span class="meta">${esc(meta)}</span></div></td>
        <td><span class="badge ${esc(c.status)}">${c.status === "pending" ? "running" : esc(c.status)}</span></td>
        <td><div class="pills">${pills || '<span class="meta">–</span>'}</div></td>
        <td class="version hide-sm">${c.program_version == null ? "–" : `v${c.program_version}`}</td>
        <td class="num hide-sm">${esc(ago(c.completed_at || c.created_at))}</td>
      </tr>
      ${open ? `<tr class="detail"><td colspan="6">${contactDetail(c)}</td></tr>` : ""}`;
  });
  return `
    <table class="table">
      <thead><tr>
        <th>ID</th><th>Contact</th><th>Status</th><th>Attempt scores</th>
        <th class="hide-sm">Prompt</th><th class="num hide-sm">Updated</th>
      </tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
}

function contactDetail(c) {
  const p = c.profile;
  const profile = [p.email, p.notes].filter(Boolean).map(esc).join(" — ");
  if (!c.attempts.length) {
    return `<div class="detail-profile">${profile}</div><div class="empty">No attempts yet.</div>`;
  }
  const attempts = c.attempts.map((a, i) => attemptCard(a, i + 1)).join("");
  return `<div class="detail-profile">${profile}</div><div class="attempts">${attempts}</div>`;
}

function attemptCard(a, n) {
  const criteria = Object.entries(CRITERIA)
    .map(([key, label]) => {
      const v = a.criteria?.[key];
      if (v == null) return "";
      const low = v < Math.max(threshold(), floor());
      return `
        <div class="criterion">
          <span class="label">${label}</span>
          <span class="bar"><span class="fill${low ? " low" : ""}" style="width:${v * 100}%"></span><span class="mark" style="left:${threshold() * 100}%"></span></span>
          <span class="val">${fmt(v)}</span>
        </div>`;
    })
    .join("");
  return `
    <article class="attempt ${a.passed ? "pass" : "fail"}">
      <div class="attempt-head">
        <span>Attempt ${n}</span>
        <span><strong>${fmt(a.score)}</strong> · ${a.passed ? "passed" : "failed"}</span>
      </div>
      <div class="email">
        <div class="subject">${esc(a.subject)}</div>
        <div class="body">${esc(a.body)}</div>
      </div>
      <div class="criteria">${criteria}</div>
      <div class="feedback${a.passed ? " ok" : ""}">${esc(a.feedback)}</div>
    </article>`;
}

function renderContacts() {
  $("#panel-contacts").innerHTML = `
    <section>
      <h2>All contacts</h2>
      ${contactTable(state.contacts, "No contacts yet. Run scripts/seed_contacts.py to submit some.")}
    </section>`;
}

// --- failed -----------------------------------------------------------------

function renderFailed() {
  const flagged = state.contacts.filter((c) => c.status === "flagged");
  const failedAttempts = state.contacts.flatMap((c) => c.attempts).filter((a) => !a.passed);
  const cutoff = Math.max(threshold(), floor());
  const counts = Object.keys(CRITERIA).map((key) => ({
    key,
    n: failedAttempts.filter((a) => (a.criteria?.[key] ?? 1) < cutoff).length,
  }));
  counts.sort((a, b) => b.n - a.n);
  const max = Math.max(1, ...counts.map((c) => c.n));

  const breakdown = failedAttempts.length
    ? `<p class="note">How often each criterion scored below ${fmt(cutoff)} across ${failedAttempts.length} failed attempts, including attempts that were later fixed by a retry. This is the feedback GEPA learns from.</p>
       <div class="breakdown">${counts
         .map(
           (c) => `
          <div class="breakdown-row">
            <span class="label">${CRITERIA[c.key]}</span>
            <span class="bar"><span class="fill" style="width:${(c.n / max) * 100}%"></span></span>
            <span class="val">${c.n} · ${pct(c.n / failedAttempts.length)}</span>
          </div>`,
         )
         .join("")}</div>`
    : `<div class="empty">No failed attempts yet.</div>`;

  $("#panel-failed").innerHTML = `
    <section><h2>Why attempts fail</h2>${breakdown}</section>
    <section><h2>Flagged contacts</h2>${contactTable(flagged, "Nothing flagged. Every contact passed within 3 attempts.")}</section>`;
}

// --- optimizations ----------------------------------------------------------

let diffsModule; // undefined: not tried, null: failed to load
async function loadDiffs() {
  if (diffsModule === undefined) {
    diffsModule = await import(DIFFS_URL).catch((err) => {
      console.warn("Falling back to built-in diff:", err);
      return null;
    });
  }
  return diffsModule;
}

function renderOptimizations() {
  const key = JSON.stringify(state.optimizations.map((o) => o.id)) + state.versions.size;
  if (key === state.optimizationsKey) return;
  state.optimizationsKey = key;

  const panel = $("#panel-optimizations");
  if (!state.optimizations.length) {
    const every = state.stats.optimize_every;
    const done = state.contacts.filter((c) => c.status !== "pending").length;
    panel.innerHTML = `<div class="empty">No optimizations yet. GEPA runs after every ${every} completed contacts (${done % every}/${every} toward the next).</div>`;
    return;
  }

  panel.innerHTML = `<div class="runs">${state.optimizations.map(runCard).join("")}</div>`;
  for (const o of state.optimizations) {
    const el = panel.querySelector(`[data-diff="${o.id}"]`);
    if (el) renderDiff(el, o);
  }
}

function runCard(o) {
  const r = o.result;
  const s = r.scores;
  const from = `v${r.base_version ?? "?"}`;
  const title = o.status === "promoted" ? `${from} → v${r.version}` : from;
  const delta = s ? s.candidate - s.base : null;
  const scores = s
    ? `<div class="run-scores">
         <div class="score-block"><span class="k">Current</span><span class="v">${fmt(s.base)}</span></div>
         <div class="score-block"><span class="k">Candidate</span><span class="v ${delta > 0 ? "up" : "down"}">${fmt(s.candidate)}</span></div>
         <div class="score-block"><span class="k">Train / test</span><span class="v">${s.n_train}/${s.n_test}</span></div>
       </div>`
    : "";
  const body = s
    ? `<div class="run-body">
         <div class="diff-label">Prompt ${from} → ${o.status === "promoted" ? `v${r.version}` : "rejected candidate"}</div>
         <div class="diff" data-diff="${o.id}"></div>
       </div>`
    : `<div class="run-body run-meta">${esc(r.detail || "")}</div>`;
  return `
    <article class="run ${esc(o.status)}">
      <header class="run-head">
        <span class="run-title">Batch ${o.batch} · ${title}</span>
        <span class="badge ${esc(o.status)}">${esc(o.status)}</span>
        <span class="run-meta">${esc(ago(o.created_at))}</span>
        ${scores}
      </header>
      ${body}
    </article>`;
}

async function renderDiff(el, o) {
  const r = o.result;
  const before = state.versions.get(r.base_version)?.instructions ?? "";
  const after = r.instructions ?? state.versions.get(r.version)?.instructions ?? "";
  const diffs = await loadDiffs();
  if (!diffs) {
    el.innerHTML = fallbackDiff(before, after);
    return;
  }
  const instance = new diffs.FileDiff({
    theme: { dark: "pierre-dark", light: "pierre-light" },
    themeType: "system",
    diffStyle: window.innerWidth < 900 ? "unified" : "split",
    overflow: "wrap",
    lineDiffType: "word",
    hunkSeparators: "simple",
    expandUnchanged: true,
  });
  instance.render({
    oldFile: { name: `prompt-v${r.base_version}.txt`, contents: before.endsWith("\n") ? before : `${before}\n` },
    newFile: { name: `prompt-${r.version ? `v${r.version}` : "candidate"}.txt`, contents: after.endsWith("\n") ? after : `${after}\n` },
    containerWrapper: el,
  });
}

// Word-level LCS diff, used only if @pierre/diffs can't be loaded.
function fallbackDiff(a, b) {
  const x = a.split(/(\s+)/);
  const y = b.split(/(\s+)/);
  if (x.length * y.length > 4e6) {
    return `<div class="diff-fallback"><pre>${esc(a)}</pre><pre>${esc(b)}</pre></div>`;
  }
  const dp = Array.from({ length: x.length + 1 }, () => new Uint32Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let left = "", right = "", i = 0, j = 0;
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) {
      left += esc(x[i]); right += esc(y[j]); i++; j++;
    } else if (j < y.length && (i >= x.length || dp[i][j + 1] >= dp[i + 1][j])) {
      right += `<ins>${esc(y[j++])}</ins>`;
    } else {
      left += `<del>${esc(x[i++])}</del>`;
    }
  }
  return `<div class="diff-fallback"><pre>${left}</pre><pre>${right}</pre></div>`;
}

// --- diagram ----------------------------------------------------------------

// Short model names for node labels: "deepseek/deepseek-v4.1-flash-1017" -> "deepseek-v4.1-flash-1017".
const shortModel = (id) => (id || "").split("/").pop();

function svgBox(x, y, w, h, title, lines = [], cls = "") {
  const text = lines
    .map((line, i) => `<text class="dg-line" x="${x + 14}" y="${y + 46 + i * 17}">${esc(line)}</text>`)
    .join("");
  return `<g class="dg-node ${cls}">
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4"/>
    <text class="dg-title" x="${x + 14}" y="${y + 25}">${esc(title)}</text>${text}
  </g>`;
}

function svgDiamond(cx, cy, w, h, label) {
  const pts = `${cx},${cy - h / 2} ${cx + w / 2},${cy} ${cx},${cy + h / 2} ${cx - w / 2},${cy}`;
  return `<g class="dg-node dg-decision">
    <polygon points="${pts}"/>
    <text class="dg-title" x="${cx}" y="${cy + 5}" text-anchor="middle">${esc(label)}</text>
  </g>`;
}

function svgArrow(d, label, lx, ly, anchor = "start", cls = "") {
  const text = label
    ? `<text class="dg-label" x="${lx}" y="${ly}" text-anchor="${anchor}">${esc(label)}</text>`
    : "";
  return `<path class="dg-arrow ${cls}" d="${d}" marker-end="url(#dg-head)"/>${text}`;
}

function renderDiagram() {
  const s = state.stats;
  const c = state.contacts;
  const done = c.filter((x) => x.status !== "pending").length;
  const every = s.optimize_every;
  const v = s.active_version;
  const latest = state.optimizations[0];
  const latestLine = latest?.result?.scores
    ? `last: ${latest.status} ${fmt(latest.result.scores.base)}→${fmt(latest.result.scores.candidate)}`
    : "no optimization yet";
  const count = (status) => c.filter((x) => x.status === status).length;

  const svg = `
  <svg class="diagram" viewBox="0 0 1120 680" role="img" aria-label="How emails are generated, judged and used to optimize the prompt">
    <defs>
      <marker id="dg-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path class="dg-head" d="M0,0 L10,5 L0,10 z"/>
      </marker>
    </defs>

    <text class="dg-lane" x="20" y="34">EMAIL WORKFLOW · ONE RUN PER CONTACT</text>
    ${svgBox(20, 60, 150, 96, "New contact", ["POST /api/contacts", `${c.length} received`])}
    ${svgBox(200, 60, 160, 96, "Load prompt", [`active: v${v}`, "pinned for the run"], "dg-live")}
    ${svgBox(390, 60, 200, 96, "Generate email", [shortModel(s.generation_model), "DSPy program"])}
    ${svgBox(615, 60, 160, 96, "Judge with Jev", ["7 rubric questions", "logs every attempt"])}
    ${svgDiamond(855, 108, 110, 84, "Pass?")}
    ${svgBox(950, 40, 150, 62, "Send", [`${count("sent")} sent`], "dg-good")}
    ${svgBox(950, 122, 150, 62, "Flag", [`${count("flagged")} flagged`], "dg-bad")}
    ${svgArrow("M170,108 H198")}
    ${svgArrow("M360,108 H388")}
    ${svgArrow("M590,108 H613")}
    ${svgArrow("M775,108 H798")}
    ${svgArrow("M910,98 H928 V71 H948", "yes", 922, 86, "end")}
    ${svgArrow("M910,118 H928 V153 H948", `no ×${s.max_attempts}`, 878, 172)}
    ${svgArrow("M855,150 V200 H490 V158", `no → retry with Jev's feedback (up to ${s.max_attempts} attempts)`, 672, 218, "middle", "dg-loop")}
    <text class="dg-note" x="855" y="44" text-anchor="middle">avg ≥ ${fmt(s.pass_threshold)}, each ≥ ${fmt(s.criterion_floor)}</text>

    ${svgArrow("M1025,184 V258", "+1 completion", 1035, 228)}
    <g class="dg-node dg-store">
      <rect x="20" y="260" width="1080" height="64" rx="4"/>
      <text class="dg-title" x="34" y="286">Postgres</text>
      <text class="dg-line" x="34" y="308">contacts · attempts (JSONB) · completions counter · prompt versions · optimization runs</text>
    </g>

    <text class="dg-lane" x="1100" y="374" text-anchor="end">OPTIMIZATION WORKFLOW · EVERY ${every} COMPLETIONS</text>
    ${svgArrow("M95,324 V398", `${every}th`, 105, 366)}
    ${svgArrow("M280,324 V398", "read", 290, 366)}
    ${svgBox(20, 400, 150, 96, `Every ${every}th`, [`${done} completed`, `next in ${every - (done % every)}`], "dg-live")}
    ${svgBox(200, 400, 160, 96, "Collect data", ["first attempts", "recent + history"])}
    ${svgBox(390, 400, 150, 96, "Split contacts", ["train / val / test", `val ≤ ${s.gepa_valset_size}`])}
    ${svgBox(570, 400, 210, 96, "GEPA", ["generate → Jev → read", "feedback → rewrite prompt", `cap: ${s.gepa_max_metric_calls ?? "auto"} scored emails`])}
    ${svgBox(810, 400, 150, 96, "Compare", ["current vs new", "on held-out test"])}
    ${svgDiamond(1040, 448, 110, 84, "Better?")}
    ${svgArrow("M170,448 H198")}
    ${svgArrow("M360,448 H388")}
    ${svgArrow("M540,448 H568")}
    ${svgArrow("M780,448 H808")}
    ${svgArrow("M960,448 H983")}
    <text class="dg-note" x="1030" y="514" text-anchor="end">by ≥ ${fmt(s.min_improvement)} · else keep v${v}</text>
    ${svgArrow("M1040,490 V538", "yes", 1050, 520)}
    ${svgBox(880, 540, 220, 80, `Activate v${v + 1}`, ["save prompt + version row", latestLine], "dg-good")}

    ${svgArrow("M990,620 V650 H8 V48 H340 V58", "", 0, 0, "start", "dg-loop")}
    <text class="dg-label" x="560" y="668" text-anchor="middle">new runs load the newest prompt · runs already in progress keep theirs</text>
  </svg>`;

  $("#panel-diagram").innerHTML = `
    <p class="note">Live view of the system. Numbers update with the dashboard.</p>
    <div class="diagram-wrap">${svg}</div>`;
}

// --- shell ------------------------------------------------------------------

function render() {
  if (!state.stats) return;
  renderStats();
  renderContacts();
  renderFailed();
  renderOptimizations();
  renderDiagram();
}

function selectTab(tab) {
  state.tab = tab;
  for (const btn of document.querySelectorAll("[data-tab]")) {
    btn.setAttribute("aria-selected", String(btn.dataset.tab === tab));
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.hidden = panel.id !== `panel-${tab}`;
  }
  history.replaceState(null, "", `#${tab}`);
}

document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) return selectTab(tab.dataset.tab);
  const row = e.target.closest("tr.row");
  if (row) {
    const id = Number(row.dataset.id);
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    renderContacts();
    renderFailed();
  }
});

const TABS = ["contacts", "failed", "optimizations", "diagram"];
const tabFromHash = () => (TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "contacts");
window.addEventListener("hashchange", () => selectTab(tabFromHash()));
selectTab(tabFromHash());
refresh();
setInterval(() => document.visibilityState === "visible" && refresh(), POLL_MS);
