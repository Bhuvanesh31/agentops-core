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
