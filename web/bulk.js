/* Bulk check page: upload a pile of papers as a batch, follow screening, and work the results by color. */
// batch: null = not chosen yet -> the most recent batch; "" = all papers
const B = { batches: [], batch: null, papers: [], files: [], filter: "ALL", q: "",
  sort: { key: "checked", dir: 1 }, poll: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = { get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} } };
const WORD = { RED: "Flag identified", YELLOW: "Potential flag", GREEN: "No flags found",
  INCOMPLETE: "Incomplete", SCREEN_FAILED: "Screen failed" };
const ORDER = { RED: 0, YELLOW: 1, INCOMPLETE: 2, SCREEN_FAILED: 2, GREEN: 3 };
const OK_EXT = /\.(pdf|docx|doc)$/i;
const CHUNK = 5;                                   // files per upload request

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 6000 : 3000);
}
const fmtSecs = (s) => (s == null ? "" : s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

async function init() {
  $("#pick-files").onchange = (e) => pick([...e.target.files]);
  $("#pick-folder").onchange = (e) => pick([...e.target.files]);
  $("#start").onclick = start;
  $("#batch").onchange = () => { B.batch = $("#batch").value; load(); };
  $("#q").oninput = () => { B.q = $("#q").value.toLowerCase(); render(); };
  $("#export").onclick = exportCsv;
  $("#delete").onclick = removeShown;
  document.querySelectorAll("th[data-sort]").forEach((th) => (th.onclick = () => {
    const k = th.dataset.sort;
    B.sort = { key: k, dir: B.sort.key === k ? -B.sort.dir : 1 };
    render();
  }));
  const drop = $("#drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", async (e) => { e.preventDefault(); drop.classList.remove("over"); pick(await droppedFiles(e.dataTransfer)); });
  await load();
}

// Folders dropped onto the page: walk them for PDF / Word files.
async function droppedFiles(dt) {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return [...dt.files];
  const out = [];
  const walk = (entry) => new Promise((resolve) => {
    if (entry.isFile) entry.file((f) => { out.push(f); resolve(); }, () => resolve());
    else if (entry.isDirectory) {
      const reader = entry.createReader();
      const readAll = () => reader.readEntries(async (ents) => {
        if (!ents.length) return resolve();
        for (const en of ents) await walk(en);
        readAll();
      }, () => resolve());
      readAll();
    } else resolve();
  });
  for (const en of entries) await walk(en);
  return out;
}

function pick(files) {
  const ok = files.filter((f) => OK_EXT.test(f.name));
  const skipped = files.length - ok.length;
  B.files = ok;
  $("#picked").textContent = ok.length ? `${ok.length} file(s) ready${skipped ? ` · ${skipped} skipped (not PDF or Word)` : ""}` : "No PDF or Word files found.";
  $("#start").disabled = !ok.length;
  if (!$("#batch-name").value && ok.length) {
    const folder = (ok[0].webkitRelativePath || "").split("/")[0];
    $("#batch-name").value = folder || `Batch ${new Date().toLocaleString()}`;
  }
}

async function start() {
  const files = B.files;
  if (!files.length) return;
  $("#start").disabled = true;
  let batch;
  try {
    batch = await api("/api/batches", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("#batch-name").value || `Batch ${new Date().toLocaleString()}` }) });
  } catch (e) { toast(e.message, "error"); $("#start").disabled = false; return; }
  B.batch = batch.id;                              // show only the batch just started
  $("#up-status").hidden = false;
  const bar = $("#up-bar");
  bar.max = files.length;
  let done = 0, failed = 0;
  for (let i = 0; i < files.length; i += CHUNK) {
    const fd = new FormData();
    files.slice(i, i + CHUNK).forEach((f) => fd.append("files", f));
    fd.append("batch", batch.id);
    try {
      const r = await api("/api/papers", { method: "POST", body: fd });
      failed += r.papers.filter((p) => p.error).length;
    } catch (e) { failed += Math.min(CHUNK, files.length - i); }
    done = Math.min(files.length, i + CHUNK);
    bar.value = done;
    $("#up-text").textContent = `Uploaded ${done} of ${files.length}${failed ? ` · ${failed} could not be read` : ""}`;
    if (i === 0) await load();                     // show the batch as soon as the first files are in
  }
  toast(`Batch “${batch.name}” uploaded; screening runs in the background.`);
  B.files = [];
  $("#picked").textContent = "";
  $("#batch-name").value = "";
  await load();
}

async function load() {
  try {
    B.batches = await api("/api/batches");
    const sel = $("#batch");
    sel.innerHTML = B.batches.map((b) => `<option value="${esc(b.id)}">${esc(b.name)} · ${b.papers} paper${b.papers === 1 ? "" : "s"} · ${esc(new Date(b.created_at).toLocaleString())}</option>`).join("")
      + `<option value="">All papers (every batch)</option>`;
    if (B.batch === null || (B.batch && !B.batches.some((b) => b.id === B.batch))) B.batch = B.batches[0]?.id || "";
    sel.value = B.batch;
    B.papers = await api("/api/papers" + (B.batch ? `?batch=${encodeURIComponent(B.batch)}` : ""));
  } catch (e) { toast(e.message, "error"); return; }
  render();
  clearTimeout(B.poll);
  if (B.papers.some((p) => p.state === "UPLOADED" || p.state === "SCREENING")) B.poll = setTimeout(load, 3000);
}

const waiting = (p) => p.state === "UPLOADED" || p.state === "SCREENING";
const statusOf = (p) => (waiting(p) ? "QUEUED" : p.status === "SCREEN_FAILED" ? "INCOMPLETE" : p.status || "INCOMPLETE");

function render() {
  const P = B.papers;
  const n = (s) => P.filter((p) => statusOf(p) === s).length;
  const done = P.filter((p) => !waiting(p));
  const avg = done.length ? done.reduce((a, p) => a + (p.seconds || 0), 0) / done.length : null;
  const left = P.length - done.length;
  $("#tiles").innerHTML = [
    ["Papers", P.length, ""], ["Screened", done.length, ""], ["In queue", left, left ? "busy" : ""],
    ["Red", n("RED"), "RED"], ["Yellow", n("YELLOW"), "YELLOW"], ["Green", n("GREEN"), "GREEN"], ["Incomplete", n("INCOMPLETE"), "INCOMPLETE"],
  ].map(([label, v, cls]) => `<div class="tile ${cls}"><div class="tile-n">${v}</div><div class="tile-l">${label}${cls === "busy" ? ' <span class="spinner"></span>' : ""}</div></div>`).join("");
  $("#overall").max = Math.max(1, P.length);
  $("#overall").value = done.length;
  $("#overall-text").textContent = P.length ? (left ? `${done.length} of ${P.length} screened${avg ? ` · about ${fmtSecs(avg * left)} left` : ""}`
    : `All ${P.length} screened${avg ? ` · ${fmtSecs(avg)} per paper on average` : ""}`) : "No papers in this batch yet.";

  const filters = [["ALL", "All", P.length], ["RED", "Red", n("RED")], ["YELLOW", "Yellow", n("YELLOW")], ["GREEN", "Green", n("GREEN")],
    ["INCOMPLETE", "Incomplete", n("INCOMPLETE")], ["QUEUED", "In queue", left]];
  $("#filters").innerHTML = filters.map(([k, l, c]) => `<button class="chip ${B.filter === k ? "active" : ""}" data-f="${k}">${l}<span class="n">${c}</span></button>`).join("");
  document.querySelectorAll("#filters .chip").forEach((b) => (b.onclick = () => { B.filter = b.dataset.f; render(); }));

  let rows = P.filter((p) => (B.filter === "ALL" || statusOf(p) === B.filter) &&
    (!B.q || `${p.title} ${p.filename}`.toLowerCase().includes(B.q)));
  const { key, dir } = B.sort;
  // "checked": most recently checked first, papers still in the queue on top
  const checkedKey = (p) => (waiting(p) ? "9" : "1") + (p.screened_at || "");
  const val = (p) => ({ status: ORDER[statusOf(p)] ?? 5, title: (p.title || "").toLowerCase(), flags: -(p.flag_count || 0) * 100 - (p.red_count || 0),
    pages: p.pages || 0, seconds: p.seconds || 0, review: p.to_review ? p.reviewed / p.to_review : 2, checked: checkedKey(p) }[key]);
  const flip = key === "checked" ? -1 : 1;
  rows = [...rows].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * dir * flip);
  document.querySelectorAll("th[data-sort]").forEach((th) => th.classList.toggle("sorted", th.dataset.sort === key));

  $("#rows").innerHTML = rows.map((p) => {
    const st = statusOf(p);
    const pill = st === "QUEUED"
      ? `<span class="pill pending"><span class="spinner"></span> ${p.state === "SCREENING" ? "Screening" : `Queued #${p.queue_position ?? ""}`}</span>`
      : `<span class="pill ${esc(st)}">${esc(WORD[st] || st)}</span>`;
    const items = p.items || [];
    const chips = items.slice(0, 3).map((i) => `<span class="pill ${esc(i.color)}" title="${esc(i.name)}">L${i.list} #${i.number} ${esc(i.name.length > 26 ? i.name.slice(0, 25) + "…" : i.name)}</span>`).join(" ")
      + (items.length > 3 ? ` <span class="muted small">+${items.length - 3}</span>` : "");
    const review = { RELEASED: "Released", RETURNED: "Returned" }[p.state]
      || (p.to_review ? `<progress value="${p.reviewed}" max="${p.to_review}"></progress> <span class="muted small">${p.reviewed}/${p.to_review}</span>` : (st === "GREEN" ? "Needs sign-off" : ""));
    return `<tr class="${esc(st)}">
      <td>${pill}</td>
      <td class="t-paper"><div class="t-title">${esc(p.title)}</div><div class="muted small">${esc(p.filename)}${p.source_format !== "pdf" ? ` · ${esc((p.source_format || "").toUpperCase())}` : ""}</div></td>
      <td>${p.flag_count ? `${p.red_count ? `<span class="pill red">${p.red_count} red</span> ` : ""}${p.yellow_count ? `<span class="pill yellow">${p.yellow_count} yellow</span>` : ""}` : (st === "QUEUED" ? "" : `<span class="muted">none</span>`)}</td>
      <td class="t-items">${chips || ""}</td>
      <td class="num">${p.pages ?? ""}</td>
      <td class="num">${fmtSecs(p.seconds)}</td>
      <td class="num" title="${esc(p.screened_at || "")}">${waiting(p) ? "" : esc(ago(p.screened_at))}</td>
      <td>${review}</td>
      <td><a class="btn small-btn" href="/?paper=${encodeURIComponent(p.id)}">Open</a></td></tr>`;
  }).join("") || `<tr><td colspan="9" class="empty-list">${P.length ? "No papers match." : "Upload a batch to get started."}</td></tr>`;
}

async function removeShown() {
  const batch = B.batches.find((b) => b.id === B.batch);
  const what = batch ? `the batch “${batch.name}” and its ${batch.papers} paper(s)` : `all ${B.papers.length} paper(s), in every batch`;
  if (!confirm(`Delete ${what}? Their files, findings, reviews and decisions are removed. The history log keeps a record that they were deleted. This cannot be undone.`)) return;
  try {
    const r = batch ? await api(`/api/batches/${encodeURIComponent(batch.id)}`, { method: "DELETE" })
      : await api("/api/papers/delete", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: B.papers.map((p) => p.id) }) });
    const skipped = Object.keys(r.skipped || {}).length;
    toast(`Deleted ${r.deleted} paper(s)${skipped ? `; ${skipped} being screened right now were kept, try again shortly` : ""}.`, skipped ? "error" : "");
    if (!skipped) B.batch = null;
    load();
  } catch (e) { toast(e.message, "error"); }
}

function exportCsv() {
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const head = ["File", "Title", "Result", "Red findings", "Yellow findings", "List items found", "Pages", "Seconds", "Checked at", "Review state", "Reviewed", "To review"];
  const lines = B.papers.map((p) => [p.filename, p.title, statusOf(p), p.red_count ?? "", p.yellow_count ?? "",
    (p.items || []).map((i) => `List ${i.list} #${i.number} ${i.name} (${i.color})`).join("; "), p.pages ?? "",
    p.seconds ?? "", p.screened_at ?? "", p.state, p.reviewed, p.to_review].map(cell).join(","));
  const name = (B.batches.find((b) => b.id === B.batch)?.name || "all-papers").replace(/[^\w.-]+/g, "_");
  const blob = new Blob([[head.map(cell).join(","), ...lines].join("\r\n")], { type: "text/csv" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `spr-${name}.csv` });
  a.click();
  URL.revokeObjectURL(a.href);
}

init().catch((e) => toast(e.message, "error"));
