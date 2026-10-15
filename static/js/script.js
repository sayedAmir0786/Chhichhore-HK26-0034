/* EcoShield AI frontend. Plain JavaScript: talks to the Flask API with fetch(). */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Escape text before putting it in HTML (prevents script injection from user-entered text).
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
const CATEGORY_LABELS = {deforestation: "Deforestation", climate_change: "Climate change", biodiversity: "Biodiversity", forest_fires: "Forest fires"};
const sampleBadge = flag => flag ? '<span class="badge sample">Sample</span>' : "";
const levelBadge = level => `<span class="badge ${esc(level)}">${esc(level)}</span>`;

async function api(url, options) {
  let res;
  try { res = await fetch(url, options); }
  catch (e) { throw new Error("Cannot reach the server. Is the Flask app running?"); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || "Request failed (" + res.status + ")");
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}
function setStatus(el, text, kind = "") { el.textContent = text; el.className = "status " + kind; }

/* ---------- charts (Chart.js, with a fallback message if the CDN is unavailable) ---------- */
const charts = {};
function drawChart(id, config) {
  const canvas = document.getElementById(id);
  const fallback = canvas.parentElement.querySelector(".chart-fallback");
  if (typeof Chart === "undefined") { canvas.hidden = true; fallback.hidden = false; return; }
  canvas.hidden = false; fallback.hidden = true;
  Chart.defaults.color = "#9fb8aa";
  Chart.defaults.borderColor = "#25392f";
  if (charts[id]) charts[id].destroy();
  config.options = Object.assign({responsive: true, maintainAspectRatio: false}, config.options || {});
  charts[id] = new Chart(canvas, config);
}
const CAT_COLORS = {High: "#ef4444", Medium: "#f59e0b", Low: "#38bdf8"};
const RESULT_COLORS = ["#10b981", "#ef4444", "#f59e0b"];
function barChart(id, items, label) {
  drawChart(id, {type: "bar", data: {labels: items.map(i => i.month), datasets: [{label, data: items.map(i => i.count), backgroundColor: "#10b981"}]},
    options: {plugins: {legend: {display: false}}, scales: {y: {beginAtZero: true, ticks: {precision: 0}}}}});
}
function doughnut(id, labels, values, colors) {
  drawChart(id, {type: "doughnut", data: {labels, datasets: [{data: values, backgroundColor: colors, borderWidth: 0}]}, options: {plugins: {legend: {position: "bottom"}}}});
}

/* ---------- navigation ---------- */
const loaded = {};
function showSection(name) {
  if (!$("#section-" + name)) name = "overview";
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "section-" + name));
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.section === name));
  $("#sidebar").classList.remove("open");
  history.replaceState(null, "", "#" + name);
  const loaders = {overview: loadDashboard, forest: loadForestHistory, fire: loadFire, claims: loadClaimHistory, reports: loadReports};
  if (loaders[name]) loaders[name]();
  if (name === "fire" && fireMap) setTimeout(() => fireMap.invalidateSize(), 50);
}
$$(".nav-btn").forEach(b => b.addEventListener("click", () => showSection(b.dataset.section)));
$("#menu-toggle").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

/* ---------- details dialog ---------- */
function showDialog(title, html) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = html;
  $("#detail-dialog").showModal();
}
$("#dialog-close").addEventListener("click", () => $("#detail-dialog").close());
function detailList(pairs) {
  return "<dl>" + pairs.filter(p => p[1] !== null && p[1] !== undefined && p[1] !== "")
    .map(p => `<dt>${esc(p[0])}</dt><dd>${p[2] ? p[1] : esc(p[1])}</dd>`).join("") + "</dl>";
}
function sourceLink(title, url) {
  if (!title) return "";
  return url && /^https?:\/\//.test(url) ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : esc(title);
}

/* ================= OVERVIEW ================= */
async function loadDashboard() {
  try {
    const d = await api("/api/dashboard?days=" + $("#days-filter").value);
    $("#stat-forest").textContent = d.stats.forest_records;
    $("#stat-changes").textContent = d.stats.potential_changes;
    $("#stat-fire").textContent = d.stats.fire_hotspots;
    $("#stat-claims").textContent = d.stats.claims_analyzed;
    const banner = $("#dash-mode");
    banner.hidden = false;
    banner.className = "banner " + (d.fire_mode === "live" ? "live" : "demo");
    banner.textContent = d.fire_note;
    $("#alerts-list").innerHTML = d.alerts.length ? d.alerts.map(a =>
      `<li>${a.type === "fire" ? "&#128293;" : "&#127795;"} ${esc(a.text)}${sampleBadge(a.sample)}<small>${esc(a.date)}</small></li>`).join("")
      : '<li class="empty">No alerts for this period.</li>';
    $("#activity-list").innerHTML = d.activity.length ? d.activity.map(a =>
      `<li><strong>${esc(a.kind)}</strong>${sampleBadge(a.sample)}<br>${esc(a.text)}<small>${esc(a.time)}</small></li>`).join("")
      : '<li class="empty">No activity for this period.</li>';
    barChart("chart-forest-activity", d.charts.forest_by_month, "Comparisons");
    const fc = d.charts.fire_by_category;
    doughnut("chart-fire-dist", ["High", "Medium", "Low"], [fc.High, fc.Medium, fc.Low], [CAT_COLORS.High, CAT_COLORS.Medium, CAT_COLORS.Low]);
    const cr = d.charts.claims_by_result;
    doughnut("chart-claims", ["Supported", "Contradicted", "Unverified"], [cr.supported, cr.contradicted, cr.unverified], RESULT_COLORS);
  } catch (e) { toast(e.message, true); }
}
$("#days-filter").addEventListener("change", loadDashboard);
$("#btn-refresh-dashboard").addEventListener("click", () => { loadDashboard(); toast("Dashboard refreshed"); });

/* ================= FOREST ================= */
function bindPreview(inputId, imgId) {
  $(inputId).addEventListener("change", e => {
    const img = $(imgId), file = e.target.files[0];
    if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
    if (file) { img.dataset.url = URL.createObjectURL(file); img.src = img.dataset.url; img.hidden = false; }
    else img.hidden = true;
  });
}
bindPreview("#file-before", "#preview-before");
bindPreview("#file-after", "#preview-after");

function renderForestResult(r) {
  const images = r.before_url ? `<div class="img-grid">
      <figure><img src="${esc(r.before_url)}" alt="Earlier image"><figcaption>Earlier image (${esc(r.date_before)})</figcaption></figure>
      <figure><img src="${esc(r.after_url)}" alt="Later image"><figcaption>Later image (${esc(r.date_after)})</figcaption></figure>
      <figure><img src="${esc(r.diff_url)}" alt="Difference image"><figcaption>Difference (red = changed pixels)</figcaption></figure></div>`
    : '<p class="muted">This is a sample record, so no images are stored.</p>';
  $("#forest-result").innerHTML = `<div class="card"><div class="result-top"><div><h3>${esc(r.location)}${sampleBadge(r.is_sample)}</h3>
    <p class="muted">${esc(r.date_before)} to ${esc(r.date_after)} &middot; analysed ${esc(r.created_at)}</p></div>
    <div><span class="big-number">${Number(r.change_percent).toFixed(1)}%</span> ${levelBadge(r.level)}<br><small class="muted">of pixels changed</small></div></div>
    ${images}<p>${esc(r.interpretation)}</p>
    <p class="muted"><em>Basic image comparison only; not validated satellite-based forest classification.</em></p></div>`;
  $("#forest-result").scrollIntoView({behavior: "smooth", block: "start"});
}

$("#forest-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target, status = $("#forest-status"), button = $("#forest-submit");
  if (!form.checkValidity()) { setStatus(status, "Please fill in every field and choose both images.", "error"); return; }
  button.disabled = true;
  setStatus(status, "Analysing images...");
  try {
    const result = await api("/api/forest/compare", {method: "POST", body: new FormData(form)});
    setStatus(status, "Analysis saved.", "ok");
    renderForestResult(result);
    loadForestHistory();
  } catch (err) { setStatus(status, err.message, "error"); }
  button.disabled = false;
});

async function loadForestHistory() {
  const body = $("#forest-history");
  body.innerHTML = '<tr><td colspan="6" class="empty">Loading...</td></tr>';
  try {
    const rows = await api("/api/forest");
    body.innerHTML = rows.length ? rows.map(r => `<tr>
      <td>${esc(r.location)}${sampleBadge(r.is_sample)}</td><td>${esc(r.date_before)} &rarr; ${esc(r.date_after)}</td>
      <td>${Number(r.change_percent).toFixed(1)}%</td><td>${levelBadge(r.level)}</td><td>${esc(r.created_at)}</td>
      <td><button class="btn small" data-view="${r.id}">Inspect</button> <button class="btn small danger" data-del="${r.id}">Delete</button></td></tr>`).join("")
      : '<tr><td colspan="6" class="empty">No saved analyses yet. Upload two images above to create one.</td></tr>';
  } catch (e) { body.innerHTML = `<tr><td colspan="6" class="status error">${esc(e.message)}</td></tr>`; }
}
$("#forest-history").addEventListener("click", async e => {
  const view = e.target.dataset.view, del = e.target.dataset.del;
  try {
    if (view) renderForestResult(await api("/api/forest/" + view));
    if (del && confirm("Delete this analysis and its stored images?")) {
      await api("/api/forest/" + del, {method: "DELETE"});
      toast("Analysis deleted");
      $("#forest-result").innerHTML = "";
      loadForestHistory();
    }
  } catch (err) { toast(err.message, true); }
});

/* ================= FIRE ================= */
let fireMap = null, markerLayer = null, markers = {};
function initMap() {
  const el = $("#fire-map");
  if (typeof L === "undefined") { el.textContent = "Map unavailable: Leaflet could not be loaded (check your internet connection). The hotspot list below still works."; return; }
  if (fireMap) return;
  el.textContent = "";
  fireMap = L.map("fire-map").setView([22, 79], 4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 18, attribution: "&copy; OpenStreetMap contributors"}).addTo(fireMap);
  markerLayer = L.layerGroup().addTo(fireMap);
}

function fireQuery(refresh) {
  const params = new URLSearchParams(new FormData($("#fire-filters")));
  for (const [k, v] of [...params]) if (!v) params.delete(k);
  if (refresh) params.set("_", Date.now());
  return params.toString();
}

async function loadFire(refresh = false) {
  initMap();
  const tbody = $("#fire-list");
  tbody.innerHTML = '<tr><td colspan="6" class="empty">Loading...</td></tr>';
  try {
    const d = await api("/api/fire?" + fireQuery(refresh));
    const banner = $("#fire-mode");
    banner.hidden = false;
    banner.className = "banner " + (d.mode === "live" ? "live" : "demo");
    banner.textContent = (d.mode === "live" ? "LIVE DATA. " : "DEMONSTRATION MODE. ") + d.note;
    $("#fire-count").textContent = `Showing ${d.count} of ${d.total} observed fire hotspot records.`;
    tbody.innerHTML = d.records.length ? d.records.map(r => `<tr class="click" data-id="${esc(r.id)}">
      <td>${esc(r.id)}</td><td>${esc(r.date)}</td><td>${esc(r.location)}</td><td>${esc(r.latitude)}, ${esc(r.longitude)}</td>
      <td>${esc(r.confidence)}</td><td>${levelBadge(r.category)}</td></tr>`).join("")
      : '<tr><td colspan="6" class="empty">No hotspot records match these filters.</td></tr>';
    if (markerLayer) {
      markerLayer.clearLayers(); markers = {};
      d.records.forEach(r => {
        const m = L.circleMarker([r.latitude, r.longitude], {radius: 7, color: CAT_COLORS[r.category], fillColor: CAT_COLORS[r.category], fillOpacity: .6});
        m.bindTooltip(`${r.location} (${r.date})`);
        m.on("click", () => showFireDetail(r.id));
        m.addTo(markerLayer); markers[r.id] = m;
      });
      if (d.records.length) fireMap.fitBounds(L.featureGroup(Object.values(markers)).getBounds().pad(0.2));
    }
    const months = {}; const cats = {High: 0, Medium: 0, Low: 0};
    d.records.forEach(r => { months[r.date.slice(0, 7)] = (months[r.date.slice(0, 7)] || 0) + 1; cats[r.category]++; });
    barChart("chart-fire-month", Object.keys(months).sort().map(m => ({month: m, count: months[m]})), "Hotspots");
    doughnut("chart-fire-cat", ["High", "Medium", "Low"], [cats.High, cats.Medium, cats.Low], [CAT_COLORS.High, CAT_COLORS.Medium, CAT_COLORS.Low]);
    if (refresh) toast("Fire data refreshed");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="status error">Could not load fire data: ${esc(e.message)}</td></tr>`;
    $("#fire-count").textContent = "";
  }
}
async function showFireDetail(id) {
  try {
    const d = await api("/api/fire/" + encodeURIComponent(id));
    const r = d.record;
    $("#fire-detail").innerHTML = `<h3>${esc(r.id)} ${levelBadge(r.category)}${sampleBadge(r.is_sample)}</h3>` + detailList([
      ["Location", r.location], ["Date", r.date], ["Latitude", r.latitude], ["Longitude", r.longitude],
      ["Confidence", r.confidence], ["Radiative power", r.frp ? r.frp + " MW" : ""], ["Source", r.source]]) +
      '<p class="muted">An observed satellite hotspot; it does not predict future fires.</p>';
    if (fireMap && markers[id]) fireMap.setView(markers[id].getLatLng(), Math.max(fireMap.getZoom(), 8));
  } catch (e) { toast(e.message, true); }
}
$("#fire-list").addEventListener("click", e => { const tr = e.target.closest("tr[data-id]"); if (tr) showFireDetail(tr.dataset.id); });
$("#fire-filters").addEventListener("submit", e => { e.preventDefault(); loadFire(); });
$("#fire-reset").addEventListener("click", () => { $("#fire-filters").reset(); loadFire(); });
$("#fire-refresh").addEventListener("click", () => loadFire(true));

/* ================= CLAIMS ================= */
function renderClaimResult(c) {
  $("#claim-result").innerHTML = `<div class="card"><div class="result-top"><h3>Result</h3><span class="badge ${esc(c.assessment)}">${esc(c.assessment_label)}</span></div>
    ${detailList([["Submitted claim", c.claim_text], ["Category", CATEGORY_LABELS[c.category] || c.category], ["Explanation", c.explanation],
      ["Matching evidence", c.matched_claim], ["Match score", c.matched_claim ? c.match_score : ""],
      ["Source", sourceLink(c.source_title, c.source_url), true], ["Analysed", c.created_at]])}
    <p class="muted"><em>Basic evidence matching, not a trained AI model. Always check the source.</em></p></div>`;
}
$("#claim-form").addEventListener("submit", async e => {
  e.preventDefault();
  const status = $("#claim-status");
  const payload = {claim: e.target.claim.value, category: e.target.category.value};
  setStatus(status, "Checking...");
  try {
    renderClaimResult(await api("/api/claims/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}));
    setStatus(status, "Saved to history.", "ok");
    loadClaimHistory();
  } catch (err) { setStatus(status, err.message, "error"); }
});
async function loadClaimHistory() {
  const box = $("#claim-history");
  try {
    const rows = await api("/api/claims");
    box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Category</th><th>Result</th><th>Checked</th><th></th></tr></thead><tbody>` +
      rows.map(c => `<tr><td>${esc(c.claim_text)}${sampleBadge(c.is_sample)}</td><td>${esc(CATEGORY_LABELS[c.category] || c.category)}</td>
        <td><span class="badge ${esc(c.assessment)}">${esc(c.assessment_label)}</span></td><td>${esc(c.created_at)}</td>
        <td><button class="btn small" data-view="${c.id}">Inspect</button> <button class="btn small danger" data-del="${c.id}">Delete</button></td></tr>`).join("") + "</tbody></table></div>"
      : '<p class="empty">No claims checked yet.</p>';
    box._rows = rows;
  } catch (e) { box.innerHTML = `<p class="status error">${esc(e.message)}</p>`; }
}
$("#claim-history").addEventListener("click", async e => {
  const view = e.target.dataset.view, del = e.target.dataset.del;
  if (view) { const c = $("#claim-history")._rows.find(r => String(r.id) === view); if (c) { renderClaimResult(c); $("#claim-result").scrollIntoView({behavior: "smooth"}); } }
  if (del && confirm("Delete this claim record?")) {
    try { await api("/api/claims/" + del, {method: "DELETE"}); toast("Claim record deleted"); $("#claim-result").innerHTML = ""; loadClaimHistory(); }
    catch (err) { toast(err.message, true); }
  }
});

/* ================= REPORTS ================= */
let reportData = null;
function reportQuery() {
  const params = new URLSearchParams(new FormData($("#report-filters")));
  for (const [k, v] of [...params]) if (!v) params.delete(k);
  return params;
}
function updateExportLinks() {
  const q = reportQuery().toString();
  ["forest", "fire", "claims"].forEach(t => { $("#export-" + t).href = `/api/reports/export/csv?type=${t}` + (q ? "&" + q : ""); });
  $("#export-pdf").href = "/api/reports/export/pdf" + (q ? "?" + q : "");
}
async function loadReports() {
  updateExportLinks();
  const empty = cols => `<tr><td colspan="${cols}" class="empty">No records match these filters.</td></tr>`;
  try {
    const d = reportData = await api("/api/reports?" + reportQuery().toString());
    $("#report-meta").textContent = `Report generated ${d.generated_at}. Fire data: ${d.fire_mode === "live" ? "live NASA FIRMS" : "sample demonstration data"}.`;
    $("#report-forest").innerHTML = d.forest.length ? d.forest.map(r => `<tr class="click" data-kind="forest" data-id="${r.id}"><td>${r.id}</td><td>${esc(r.location)}${sampleBadge(r.is_sample)}</td>
      <td>${Number(r.change_percent).toFixed(1)}%</td><td>${esc(r.created_at)}</td><td>${esc(r.source)}</td></tr>`).join("") : empty(5);
    $("#report-fire").innerHTML = d.fire.length ? d.fire.map(r => `<tr class="click" data-kind="fire" data-id="${esc(r.id)}"><td>${esc(r.id)}</td><td>${esc(r.date)}</td>
      <td>${esc(r.location)}</td><td>${levelBadge(r.category)}</td><td>${esc(r.source)}</td></tr>`).join("") : empty(5);
    $("#report-claims").innerHTML = d.claims.length ? d.claims.map(r => `<tr class="click" data-kind="claims" data-id="${r.id}"><td>${r.id}</td><td>${esc(r.claim_text)}</td>
      <td><span class="badge ${esc(r.assessment)}">${esc(r.assessment_label)}</span></td><td>${esc(r.created_at)}</td></tr>`).join("") : empty(4);
  } catch (e) { toast(e.message, true); }
}
$("#report-filters").addEventListener("submit", e => { e.preventDefault(); loadReports(); });
document.addEventListener("click", e => {
  const tr = e.target.closest("tr[data-kind]");
  if (!tr || !reportData) return;
  const {kind, id} = tr.dataset;
  const r = reportData[kind].find(x => String(x.id) === id);
  if (!r) return;
  if (kind === "forest") showDialog("Forest comparison #" + r.id, detailList([["Location", r.location], ["Image dates", r.date_before + " to " + r.date_after],
    ["Changed pixels", r.change_percent.toFixed(1) + "% (" + r.level + ")"], ["Analysed", r.created_at], ["Source", r.source], ["Note", r.interpretation]]));
  if (kind === "fire") showDialog("Hotspot " + r.id, detailList([["Location", r.location], ["Date", r.date], ["Latitude", r.latitude], ["Longitude", r.longitude],
    ["Confidence", r.confidence], ["Alert category", r.category], ["Source", r.source]]));
  if (kind === "claims") showDialog("Claim analysis #" + r.id, detailList([["Claim", r.claim_text], ["Category", CATEGORY_LABELS[r.category] || r.category],
    ["Result", r.assessment_label], ["Explanation", r.explanation], ["Matching evidence", r.matched_claim], ["Source", sourceLink(r.source_title, r.source_url), true], ["Analysed", r.created_at]]));
});

/* ---------- start ---------- */
showSection((location.hash || "#overview").slice(1));
