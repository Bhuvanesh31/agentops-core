// Shared helpers + index-page logic for the verification UI.
// Pure read-only: every call is a GET against the JSON API, same origin.

async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json();
}

function showError(el, err) {
  // A verification tool must SHOW failures, never blank them out.
  el.innerHTML = "";
  const box = document.createElement("div");
  box.className = "error";
  box.textContent = `Request failed — ${err.message}`;
  el.appendChild(box);
}

function cell(value) {
  // NULL stays visible as an em-dash; never coerced to 0.
  return value === null || value === undefined ? "—" : String(value);
}

function renderTable(container, rows, columns, link) {
  container.innerHTML = "";
  const table = document.createElement("table");
  const headRow = table.createTHead().insertRow();
  for (const col of columns) {
    const th = document.createElement("th");
    th.textContent = col.label;
    headRow.appendChild(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const col of columns) {
      const td = tr.insertCell();
      const text = cell(row[col.key]);
      if (link && col.key === link.key) {
        const a = document.createElement("a");
        a.href = link.href(row);
        a.textContent = text;
        td.appendChild(a);
      } else {
        // textContent (never innerHTML): captured data is untrusted.
        td.textContent = text;
      }
    }
  }
  container.appendChild(table);
}

const OVERVIEW_COLUMNS = [
  { key: "project_id", label: "Project" },
  { key: "project_name", label: "Name" },
  { key: "run_count", label: "Runs" },
  { key: "input_tokens", label: "Input" },
  { key: "output_tokens", label: "Output" },
  { key: "cached_input_tokens", label: "Cached" },
  { key: "earliest", label: "Earliest" },
  { key: "latest_activity", label: "Latest" },
];

const RUN_COLUMNS = [
  { key: "run_id", label: "Run" },
  { key: "project_id", label: "Project" },
  { key: "repository_id", label: "Repo" },
  { key: "tool_id", label: "Tool" },
  { key: "status", label: "Status" },
  { key: "started_at", label: "Started" },
  { key: "input_tokens", label: "Input" },
  { key: "output_tokens", label: "Output" },
  { key: "cached_input_tokens", label: "Cached" },
  { key: "cost_source", label: "Cost src" },
];

const state = { limit: 50, offset: 0 };

async function loadRuns() {
  const el = document.getElementById("runs");
  const params = new URLSearchParams();
  for (const f of ["project_id", "repository_id", "status", "tool_id"]) {
    const v = document.getElementById(f).value.trim();
    if (v) params.set(f, v);
  }
  params.set("limit", state.limit);
  params.set("offset", state.offset);
  document.getElementById("page-info").textContent = `offset ${state.offset}`;
  try {
    const runs = await fetchJSON(`/runs?${params.toString()}`);
    if (runs.length === 0) {
      el.innerHTML = "";
      const p = document.createElement("p");
      p.textContent = "No runs match.";
      el.appendChild(p);
      return;
    }
    renderTable(el, runs, RUN_COLUMNS, {
      key: "run_id",
      href: (r) => `run.html?id=${encodeURIComponent(r.run_id)}`,
    });
  } catch (err) {
    showError(el, err);
  }
}

async function initIndex() {
  const overviewEl = document.getElementById("overview");
  try {
    const overview = await fetchJSON("/overview");
    renderTable(overviewEl, overview, OVERVIEW_COLUMNS);
    if (window.renderTokenChart) window.renderTokenChart(overview);
  } catch (err) {
    showError(overviewEl, err);
  }

  await loadRuns();

  document.getElementById("filter-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.offset = 0;
    loadRuns();
  });
  document.getElementById("prev").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadRuns();
  });
  document.getElementById("next").addEventListener("click", () => {
    state.offset += state.limit;
    loadRuns();
  });
}

const DETAIL_FIELDS = [
  "run_id", "project_id", "repository_id", "tool_id", "model", "status",
  "branch", "intent", "summary", "human", "session_id",
  "started_at", "ended_at",
  "input_tokens", "output_tokens", "cached_input_tokens",
  "cost_usd", "cost_source", "iteration_count", "tool_calls_count",
];

function renderDetail(container, run) {
  container.innerHTML = "";
  const dl = document.createElement("dl");
  for (const field of DETAIL_FIELDS) {
    const dt = document.createElement("dt");
    dt.textContent = field;
    const dd = document.createElement("dd");
    dd.textContent = cell(run[field]);
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  container.appendChild(dl);
}

function renderEvents(container, events) {
  container.innerHTML = "";
  if (events.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No events.";
    container.appendChild(p);
    return;
  }
  for (const ev of events) {
    const card = document.createElement("div");
    card.className = "event";

    const head = document.createElement("div");
    head.className = "event-head";
    const files = (ev.files_touched || []).join(", ");
    head.textContent =
      `${cell(ev.occurred_at)}  ${ev.event_type}  ${cell(ev.tool_name)}  ` +
      `[${ev.redaction_status}]  ${files}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "raw_payload";
    const pre = document.createElement("pre");
    try {
      pre.textContent = JSON.stringify(ev.raw_payload, null, 2);
    } catch (e) {
      // Defensive: never let one bad payload break the whole page.
      pre.textContent = String(ev.raw_payload);
    }
    details.appendChild(summary);
    details.appendChild(pre);

    card.appendChild(head);
    card.appendChild(details);
    container.appendChild(card);
  }
}

function renderCommits(container, commits) {
  container.innerHTML = "";
  if (commits.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No commits linked.";
    container.appendChild(p);
    return;
  }
  const table = document.createElement("table");
  const headRow = table.createTHead().insertRow();
  for (const label of ["SHA", "Message", "Author", "Committed at"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  const body = table.createTBody();
  for (const c of commits) {
    const tr = body.insertRow();
    const values = [
      c.commit_sha ? c.commit_sha.slice(0, 7) : "—",
      c.commit_message ?? "—",
      c.author_name ?? "—",
      c.committed_at ? new Date(c.committed_at).toLocaleString() : "—",
    ];
    values.forEach((text, i) => {
      const td = tr.insertCell();
      if (i === 0) {
        const code = document.createElement("code");
        code.textContent = text;
        td.appendChild(code);
      } else {
        td.textContent = text;  // never innerHTML — commit data is untrusted
      }
    });
  }
  container.appendChild(table);
}

async function initRun() {
  const id = new URLSearchParams(window.location.search).get("id");
  const detailEl = document.getElementById("detail");
  const eventsEl = document.getElementById("events");
  const commitsEl = document.getElementById("commits");
  if (!id) {
    showError(detailEl, new Error("missing ?id= in URL"));
    return;
  }
  try {
    const run = await fetchJSON(`/runs/${encodeURIComponent(id)}`);
    renderDetail(detailEl, run);
  } catch (err) {
    showError(detailEl, err);
  }
  try {
    const events = await fetchJSON(`/runs/${encodeURIComponent(id)}/events`);
    renderEvents(eventsEl, events);
  } catch (err) {
    showError(eventsEl, err);
  }
  try {
    const commits = await fetchJSON(`/runs/${encodeURIComponent(id)}/commits`);
    renderCommits(commitsEl, commits);
  } catch (err) {
    showError(commitsEl, err);
  }
}
