// Per-project token bar chart, drawn with Chart.js (loaded from CDN).
// Display-only: a NULL token total plots as a 0-height bar. This does not
// alter the underlying data (the tables still show NULL as "—").
window.renderTokenChart = function renderTokenChart(overview) {
  const canvas = document.getElementById("token-chart");
  if (!canvas || typeof Chart === "undefined") return;
  const labels = overview.map((p) => p.project_id);
  const input = overview.map((p) => p.input_tokens || 0);
  const output = overview.map((p) => p.output_tokens || 0);
  new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Input tokens", data: input },
        { label: "Output tokens", data: output },
      ],
    },
    options: { responsive: true, scales: { y: { beginAtZero: true } } },
  });
};
