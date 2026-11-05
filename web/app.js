/* SPR Pre-Screen reviewer UI (plain JS + PDF.js). All document text is inserted via esc()/textContent. */
// Served locally: the app makes no requests outside this machine.
pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.js";

const S = {
  papers: [], criteria: [], filter: "ALL", search: "", current: null, detail: null,
  tab: "open", selected: null, overriding: null, scale: 1.3, pdfFor: null, pollTimer: null, showLow: false,
  rewrites: {},            // suggested rewrites per finding (shown on its card)
  live: null, liveFor: null, liveTimer: null, follow: true,    // live view of a screening in progress
};
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isFinal = () => ["RELEASED", "RETURNED"].includes(S.detail?.summary?.state);

const WORD = { RED: "Flag identified", YELLOW: "Potential flag", GREEN: "No flags found",
  INCOMPLETE: "Incomplete screen", SCREEN_FAILED: "Screen failed" };
const EXPLAIN = {
  RED: "A List 1 item or a clear List 2 match was found. A reviewer decides what happens next.",
  YELLOW: "Something might apply and the tool is not sure. A reviewer should look.",
  GREEN: "Nothing found on List 1 or List 2. This is not a clearance: a reviewer still decides.",
  INCOMPLETE: "Part of the document could not be checked, so it is not called green. Review it manually.",
  SCREEN_FAILED: "The tool could not screen this document. Review it manually.",
};
const RANK = { red: { RED: 0, SCREEN_FAILED: 1, INCOMPLETE: 1, YELLOW: 2, GREEN: 3 },
  green: { GREEN: 0, YELLOW: 1, INCOMPLETE: 2, SCREEN_FAILED: 2, RED: 3 } };

// ---------------------------------------------------------------- plumbing

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const store = { get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} } };

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 6000 : 2800);
}

// ---------------------------------------------------------------- start

async function init() {
  $("#sort").value = store.get("spr-sort-v2", "checked");
  $("#sort").onchange = () => { store.set("spr-sort-v2", $("#sort").value); renderQueue(); };
  $("#search").oninput = () => { S.search = $("#search").value.toLowerCase(); renderQueue(); };
  $("#upload").onchange = (ev) => { upload([...ev.target.files]); ev.target.value = ""; };
  $("#help-btn").onclick = () => $("#help").showModal();
  $("#zoom-in").onclick = () => zoom(0.15);
  $("#zoom-out").onclick = () => zoom(-0.15);
  setupDrop();
  document.addEventListener("keydown", onKey);
  ["wheel", "touchmove"].forEach((ev) => $("#viewer").addEventListener(ev, () => {
    if (S.liveFor && S.follow) { S.follow = false; const f = $("#follow"); if (f) f.checked = false; }
  }, { passive: true }));
  try { S.criteria = (await api("/api/criteria")).criteria; } catch (_) {}
  const linked = new URLSearchParams(location.search).get("paper");   // opened from the bulk page
  if (linked) S.current = linked;
  await refresh(Boolean(linked));
}

function setupDrop() {
  const pane = $("#queue-pane"), zone = $("#dropzone");
  let depth = 0;
  pane.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; zone.hidden = false; });
  pane.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; zone.hidden = true; } });
  pane.addEventListener("dragover", (e) => e.preventDefault());
  pane.addEventListener("drop", (e) => { e.preventDefault(); depth = 0; zone.hidden = true; upload([...e.dataTransfer.files]); });
}

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  const msg = $("#upload-msg");
  msg.hidden = false;
  msg.textContent = `Uploading ${files.length} file(s)…`;
  try {
    const { papers } = await api("/api/papers", { method: "POST", body: fd });
    const ok = papers.filter((p) => p.id), bad = papers.filter((p) => p.error);
    msg.innerHTML = `${ok.length} uploaded and queued for screening.` +
      bad.map((b) => `<div class="pill RED">${esc(b.filename)}: ${esc(b.error)}</div>`).join("");
    if (ok.length === 1) S.current = ok[0].id;
    setTimeout(() => { msg.hidden = true; }, 6000);
    await refresh();
  } catch (e) { msg.textContent = `Upload failed: ${e.message}`; }
}

async function refresh(reloadPdf = false) {
  try { S.papers = await api("/api/papers"); } catch (e) { toast(e.message, "error"); return; }
  renderQueue();
  if (S.current) await openPaper(S.current, reloadPdf);
  clearTimeout(S.pollTimer);
  if (S.papers.some((p) => p.state === "UPLOADED" || p.state === "SCREENING")) S.pollTimer = setTimeout(refresh, 2000);
}

// ---------------------------------------------------------------- queue

const open = (p) => !["RELEASED", "RETURNED"].includes(p.state);

function visiblePapers() {
  const mode = $("#sort").value;
  let list = S.papers.filter((p) => !S.search || (p.title || "").toLowerCase().includes(S.search));
  if (S.filter === "DONE") list = list.filter((p) => !open(p));
  else if (S.filter !== "ALL") list = list.filter((p) => open(p) && (p.status === S.filter ||
    (S.filter === "INCOMPLETE" && p.status === "SCREEN_FAILED")));
  list = [...list];
  const inQueue = (p) => p.state === "UPLOADED" || p.state === "SCREENING";
  if (mode === "checked") list.sort((a, b) => inQueue(b) - inQueue(a) || (b.screened_at || "").localeCompare(a.screened_at || ""));
  else if (mode === "newest") list.sort((a, b) => b.uploaded_at.localeCompare(a.uploaded_at));
  else list.sort((a, b) => (!open(a)) - (!open(b)) || (RANK[mode][a.status] ?? 9) - (RANK[mode][b.status] ?? 9) ||
    (mode === "red" ? (b.flag_count || 0) - (a.flag_count || 0) : (a.flag_count || 0) - (b.flag_count || 0)));
  return list;
}

function renderQueue() {
  const n = (f) => S.papers.filter(f).length;
  const chips = [["ALL", "All", S.papers.length], ["RED", "Red", n((p) => open(p) && p.status === "RED")],
    ["YELLOW", "Yellow", n((p) => open(p) && p.status === "YELLOW")], ["GREEN", "Green", n((p) => open(p) && p.status === "GREEN")],
    ["INCOMPLETE", "Incomplete", n((p) => open(p) && ["INCOMPLETE", "SCREEN_FAILED"].includes(p.status))],
    ["DONE", "Decided", n((p) => !open(p))]];
  $("#status-chips").innerHTML = chips.map(([k, label, c]) =>
    `<button class="chip ${S.filter === k ? "active" : ""}" data-f="${k}" role="tab">${label}<span class="n">${c}</span></button>`).join("");
  document.querySelectorAll("#status-chips .chip").forEach((b) => (b.onclick = () => { S.filter = b.dataset.f; renderQueue(); }));

  const list = visiblePapers();
  $("#queue").innerHTML = list.map((p) => {
    const inProgress = p.state === "UPLOADED" || p.state === "SCREENING";
    const status = inProgress ? `<span class="pill pending"><span class="spinner"></span> ${p.state === "UPLOADED" ? `In queue${p.queue_position ? " #" + p.queue_position : ""}` : `${p.progress?.percent ?? 0}%`}</span>`
      : `<span class="pill ${esc(p.status || "grey")}">${esc(p.status ? WORD[p.status] : p.state)}</span>`;
    const counts = p.flag_count ? `${p.red_count} red · ${p.yellow_count} yellow` : p.flag_count === 0 ? "No flags" : "";
    const decided = { RELEASED: "Released", RETURNED: "Returned" }[p.state];
    const fmt = p.source_format && p.source_format !== "pdf" ? `<span class="tag">${esc(p.source_format.toUpperCase())}</span>` : "";
    return `<li class="q-item ${p.id === S.current ? "active" : ""}" data-id="${esc(p.id)}">
      <div class="q-bar ${esc(p.status || "")}"></div>
      <div class="q-body">
        <div class="q-title" title="${esc(p.title)}">${esc(p.title)}</div>
        <div class="q-meta">${status}<span>${p.state === "SCREENING" ? esc(p.progress?.label || "Starting") : counts}</span>${fmt}${decided ? `<span class="tag">${decided}</span>` : ""}</div>
        ${p.state === "SCREENING" ? `<div class="q-live"><div class="lb-fill" data-pct="${p.progress?.percent ?? 0}"></div></div>` : ""}
        ${p.to_review ? `<progress class="q-progress" value="${p.reviewed}" max="${p.to_review}" title="${p.reviewed} of ${p.to_review} reviewed"></progress>` : ""}
      </div></li>`;
  }).join("") || `<li class="empty-list">${S.papers.length ? "No papers match." : "No papers yet. Upload or drop files here."}</li>`;
  fillWidths($("#queue"));
  document.querySelectorAll(".q-item").forEach((li) => (li.onclick = () => { S.selected = null; S.tab = "open"; openPaper(li.dataset.id, true); }));
}

// ---------------------------------------------------------------- paper

async function openPaper(id, reloadPdf) {
  const changed = S.current !== id;
  S.current = id;
  try { S.detail = await api(`/api/papers/${id}`); } catch (e) { toast(e.message, "error"); return; }
  document.querySelectorAll(".q-item").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  renderPanel();
  if (busy(S.detail.summary.state)) startLive();
  else if (S.liveFor) stopLive();
  if (changed || reloadPdf || S.pdfFor !== id) await renderPdf();
  else drawHighlights();
}

function zoom(delta) {
  S.scale = Math.min(2.4, Math.max(0.7, +(S.scale + delta).toFixed(2)));
  if (S.current) renderPdf();
}

async function renderPdf() {
  if (S.pdfLoading === S.current) return;
  S.pdfLoading = S.current;
  try { await drawPdf(); } finally { S.pdfLoading = null; }
}

async function drawPdf() {
  const viewer = $("#viewer");
  viewer.innerHTML = '<div class="empty muted">Loading document…</div>';
  let pdf;
  try {
    // isEvalSupported:false closes the pdf.js font-eval path used by malicious PDFs (CVE-2024-4367).
    pdf = await pdfjsLib.getDocument({ url: `/api/papers/${S.current}/file`, isEvalSupported: false, enableXfa: false }).promise;
  } catch (_) {
    viewer.innerHTML = '<div class="empty muted">The document is still being prepared…</div>';
    S.pdfFor = null;
    return;
  }
  S.pdfFor = S.current;
  viewer.innerHTML = "";
  $("#viewer-toolbar").hidden = false;
  $("#zoom-label").textContent = `${Math.round(S.scale * 100)}%`;
  $("#page-info").textContent = `${pdf.numPages} page${pdf.numPages === 1 ? "" : "s"}`;
  for (let n = 1; n <= pdf.numPages; n++) {
    const page = await pdf.getPage(n);
    const vp = page.getViewport({ scale: S.scale });
    const wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.id = `page-${n}`;
    wrap.style.width = vp.width + "px";
    wrap.style.height = vp.height + "px";
    const label = document.createElement("span");
    label.className = "page-label";
    label.textContent = `Page ${n}`;
    const canvas = document.createElement("canvas");
    canvas.width = vp.width;
    canvas.height = vp.height;
    wrap.append(label, canvas);
    viewer.appendChild(wrap);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
  }
  drawHighlights();
  if (S.live) drawLive();
}

function drawHighlights() {
  document.querySelectorAll(".hl").forEach((e) => e.remove());
  document.querySelectorAll(".page-wrap").forEach((e) => e.classList.remove("page-flag"));
  const r = S.detail?.result;
  if (!r) return;
  const add = (page, box, cls, id) => {
    const wrap = document.getElementById(`page-${page}`);
    if (!wrap) return;
    const d = document.createElement("div");
    d.className = `hl ${cls}${id === S.selected ? " selected" : ""}`;
    d.dataset.flag = id;
    const [x0, y0, x1, y1] = box;
    Object.assign(d.style, { left: x0 * S.scale + "px", top: y0 * S.scale + "px",
      width: (x1 - x0) * S.scale + "px", height: (y1 - y0) * S.scale + "px" });
    d.title = "Show this finding";
    d.onclick = () => { if (!id.startsWith("m-")) select(id, false); };
    wrap.appendChild(d);
  };
  for (const f of r.flags) {
    if (f.low_confidence && !S.showLow) continue;
    for (const o of f.occurrences) {
      if (o.location_exact) o.bboxes.forEach((b) => add(o.page, b, f.low_confidence ? "low" : f.color, f.id));
      else document.getElementById(`page-${o.page}`)?.classList.add("page-flag");
    }
  }
  for (const m of S.detail.manual_flags || []) (m.bboxes || []).forEach((b) => add(m.page, b, "manual", "m-" + m.id));
}

// ---------------------------------------------------------------- findings

function foundBy(f) {
  const m = f.methods || [];
  if (m.includes("structure")) return "Structure check";
  if (m.includes("llm")) return "Model reading";
  if (m.includes("regex")) return "Identifier";
  if (m.includes("exact") && !f.uncertain_reason) return "Marking";
  return "Named term";
}

function reasonOf(f) {
  // the model's reason without the bracketed answer details (those go under "Details")
  const r = (f.llm_reason || "").replace(/\s*\[[^\]]*\]\s*(\(reading[^)]*\))?\s*$/, "").trim();
  return r || f.explanation || "";
}

function answersOf(f) {
  const m = /\[([^\]]*)\]/.exec(f.llm_reason || "");
  return m ? m[1] : "";
}

function tabFlags() {
  const r = S.detail?.result;
  if (!r) return [];
  const disp = (f) => f.disposition;
  const main = r.flags.filter((f) => !f.low_confidence);
  return {
    open: main.filter((f) => !disp(f)), all: main, red: main.filter((f) => f.color === "red"),
    yellow: main.filter((f) => f.color === "yellow"), done: main.filter(disp), low: r.flags.filter((f) => f.low_confidence),
  }[S.tab] || main;
}

function card(f) {
  const d = f.disposition;
  const canAct = !isFinal();
  // a possible List 1 match is red (guide: anything found on List 1), but says it is possible
  const possibleRed = f.color === "red" && f.list_no === 1 && f.uncertain_reason;
  const pill = `<span class="pill ${esc(f.color)}" ${possibleRed ? `title="${esc(f.uncertain_reason)}"` : ""}>${f.color === "red" ? (possibleRed ? "Red · Possible List 1" : "Red · Flag") : f.llm_verdict === "topic" ? "Yellow · Topic" : "Yellow · Possible"}</span>`;
  const more = f.occurrences.length > 1 ? ` · ${f.occurrences.length} places` : "";
  let actions = "";
  if (S.overriding === f.id) {
    actions = `<div class="override-form"><textarea id="ov-reason" rows="2" placeholder="Why is this not a concern? (required)"></textarea>
      <div class="actions"><button class="primary" data-act="ov-save">Save override</button><button class="ghost" data-act="ov-cancel">Cancel</button></div></div>`;
  } else if (d) {
    actions = `<div class="actions"><span class="state ${esc(d.action)}">${d.action === "accepted" ? "✔ Accepted as a concern" : "✎ Overridden"}</span>
      <span class="muted small">by ${esc(d.reviewer)}${d.reason ? ` — “${esc(d.reason)}”` : ""}</span>
      ${canAct ? `<button class="ghost" data-act="change">Change</button>` : ""}
      ${d.action === "accepted" ? `<button class="ghost" data-act="rewrite" title="Suggest replacement wording for the author">✨ Suggest rewrites</button>` : ""}</div>`;
  } else if (canAct) {
    actions = `<div class="actions"><button data-act="accepted" title="A">✔ Accept</button><button data-act="override" title="O">✎ Override…</button>
      <button class="ghost" data-act="rewrite" title="Suggest replacement wording for the author">✨ Suggest rewrites</button></div>`;
  } else if (!f.low_confidence) {
    actions = `<div class="actions"><span class="pill pending">Awaiting reviewer</span></div>`;
  }
  const why = reasonOf(f);
  const also = (f.also || []).map((a) => {
    const r = reasonOf({ llm_reason: a.reason });
    return `<li><span class="pill ${esc(a.color)}">${a.color === "red" ? "Red" : "Yellow"}</span> ${esc(a.criterion_name)}
      <span class="num">· List ${a.list_no}${a.criterion_number != null ? " #" + a.criterion_number : ` ${esc(a.criterion_id)}`}</span>
      ${r && r !== why ? `<div class="muted small">${esc(r)}</div>` : ""}</li>`;
  }).join("");
  const unsure = f.uncertain_reason && !f.llm_reason ? f.uncertain_reason : "";
  const details = [
    ["Found by", foundBy(f)], ["Model's answers", answersOf(f)], ["Verdict", f.llm_verdict?.replace("_", " ")],
    ["Evidence", f.strength], ["Section", f.section], ["Source", f.source_policy],
    ["Not counted because", f.low_confidence_reason], ["Note", unsure],
  ].filter(([, v]) => v);
  return `<div class="card ${esc(f.color)} ${f.low_confidence ? "low" : ""} ${d ? "done" : ""} ${S.selected === f.id ? "selected" : ""}" data-id="${esc(f.id)}">
    <div class="c-head"><div class="c-item">${esc(f.criterion_name)} <span class="num">· List ${f.list_no}${f.criterion_number != null ? " #" + f.criterion_number : ""}</span>${/^C-\d+$/.test(f.criterion_id) ? ` <span class="tag custom" title="A check added on the Checks page">Custom ${esc(f.criterion_id)}</span>` : ""}</div>${pill}</div>
    <div class="quote ${f.masked ? "masked" : ""}">${esc(f.text.length > 380 ? f.text.slice(0, 380) + "…" : f.text)}${f.masked ? ` <button class="ghost" data-act="reveal" title="Show the full value (recorded in the audit log)">Reveal</button>` : ""}</div>
    ${f.llm_verdict === "topic" ? `<div class="topic-note">The paper discusses this List 2 topic in ${f.topic_passages} place${f.topic_passages === 1 ? "" : "s"} (page${f.topic_pages.length === 1 ? "" : "s"} ${f.topic_pages.join(", ")}), all public or general. One decision covers the topic; the first passage is shown.</div>`
      : why ? `<div class="why"><b>Why:</b> ${esc(why)}</div>` : ""}
    ${also ? `<div class="also"><b>Also matches ${f.also.length} more list item${f.also.length > 1 ? "s" : ""}</b> <span class="muted small">(one decision covers them all)</span><ul>${also}</ul></div>` : ""}
    <div class="c-foot"><span class="tag">${esc(foundBy(f))}</span><span class="page-link" data-act="goto">Page ${f.page}${more}</span>
      ${f.evidence_status !== "verified" ? `<span class="pill RED">Evidence not verified</span>` : ""}</div>
    ${details.length ? `<details class="more"><summary>Details</summary><dl>${details.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></details>` : ""}
    ${rewriteBlock(f)}
    ${actions}</div>`;
}

function rewriteBlock(f) {
  const r = S.rewrites[f.id];
  if (!r || f.low_confidence) return "";
  if (r.loading) return `<div class="rewrites"><span class="spinner"></span> Writing suggestions with the local model…</div>`;
  if (r.error) return `<div class="rewrites"><div class="note warn">${esc(r.error)}</div></div>`;
  const list = r.suggestions.map((s, i) => `<li><div class="rw-text">${esc(s.text)}</div>
      <div class="rw-foot"><span class="muted small">${esc(s.note)}</span><button class="ghost small-btn" data-act="copy" data-i="${i}">Copy</button></div></li>`).join("");
  return `<div class="rewrites"><div class="small"><b>Suggested rewrites</b> <span class="muted">for the author; the paper is not changed</span></div>
    <div class="small muted rw-orig">Now: “${esc(r.original)}”</div>
    ${list ? `<ol>${list}</ol>` : `<div class="note">No suggestion passed the re-check. Try again, or rewrite by hand.</div>`}
    ${r.dropped?.length ? `<div class="muted small">Left out after the re-check: ${esc(Object.entries(r.dropped.reduce((m, w) => ({ ...m, [w]: (m[w] || 0) + 1 }), {}))
      .map(([w, n]) => `${n} because ${w}`).join("; "))}.</div>` : ""}
    <button class="ghost small-btn" data-act="rewrite">↻ Try again</button></div>`;
}

function renderPanel() {
  const P = $("#panel");
  const { summary: s, result: r, manual_flags: manual, decision } = S.detail;
  if (!r) {
    const failed = s.state === "SCREEN_FAILED";
    P.innerHTML = `<div class="p-head"><div class="p-title">${esc(s.title)}</div>
      <div class="verdict ${failed ? "SCREEN_FAILED" : "pending"}"><div><div class="verdict-word">${failed ? "Screen failed" : "Screening…"}</div>
      <div class="verdict-sub">${failed ? esc(EXPLAIN.SCREEN_FAILED + " " + (s.error || "")) : "List 1, then List 2, then the passages are read in context. Watch it on the document."}</div></div></div></div>
      <div class="p-body">${failed ? "" : `<div id="live-panel" class="live-panel"></div>`}</div>${failed ? decisionBar(s, r, decision) : ""}`;
    wirePanel();
    if (!failed) renderLivePanel();
    return;
  }
  const main = r.flags.filter((f) => !f.low_confidence);
  const low = r.flags.filter((f) => f.low_confidence);
  const issues = [
    ...r.coverage.pages.filter((p) => !p.ok).map((p) => `Page ${p.page}: ${p.error}`),
    ...r.coverage.pages.filter((p) => p.ok && p.images_unread).map((p) =>
      `Page ${p.page}: ${p.images_unread} picture(s) may contain text that was not read (OCR not available). Check them by eye.`),
    ...r.coverage.detectors.filter((d) => !d.ok).map((d) =>
      d.method === "llm_judge" ? `Model reading: ${d.error}` : `${d.method} check (List ${d.list_no}): ${d.error}`),
  ];
  const checked = (r.lists_checked || []).map((l) => `List ${l.list} (${l.items} items)`).join(" → ");
  const review = r.coverage.detectors.find((d) => d.method === "llm_judge" && d.ok);
  const reviewNote = review?.error ? ` (${review.error})` : "";
  const counts = { open: main.filter((f) => !f.disposition).length, all: main.length, red: r.red_count,
    yellow: r.yellow_count, done: main.filter((f) => f.disposition).length, low: low.length };
  const tabs = [["open", "To review"], ["all", "All"], ["red", "Red"], ["yellow", "Yellow"], ["done", "Reviewed"], ["low", "Not counted"]];
  const flags = tabFlags();
  const byList = (n) => flags.filter((f) => f.list_no === n);
  const section = (n) => {
    const fs = byList(n);
    return fs.length ? `<div class="list-head">List ${n}<span class="muted">${fs.length}</span></div>${fs.map(card).join("")}` : "";
  };
  const emptyMsg = { open: main.length ? "Every finding has been reviewed. Make the decision below." : "No findings to review.",
    low: "Nothing here. Passing mentions the model set aside would appear here." }[S.tab] || "Nothing in this view.";

  P.innerHTML = `
    <div class="p-head">
      <div class="p-title">${esc(s.title)}</div>
      <div class="verdict ${esc(r.status)}">
        <div><div class="verdict-word">${esc(WORD[r.status])}</div><div class="verdict-sub">${esc(EXPLAIN[r.status])}</div></div>
        <div class="counts">${r.red_count ? `<span class="pill red">${r.red_count} red</span>` : ""}${r.yellow_count ? `<span class="pill yellow">${r.yellow_count} yellow</span>` : ""}</div>
      </div>
      ${issues.map((i) => `<div class="note warn">⚠ ${esc(i)}</div>`).join("")}
      <div class="meta-line"><span>Checked ${esc(checked)}</span>
        ${review ? `<span title="${esc(reviewNote)}">Model read ${review.candidates} part(s) in ${review.seconds}s</span>` : ""}
        ${["docx", "doc"].includes(r.source_format) ? `<a href="#" id="dl-original">Download original Word file</a>` : ""}
        ${!isFinal() ? `<a href="#" id="rescreen">Screen again</a>` : ""}
        <a href="#" id="delete-paper" class="danger-link" title="Remove this paper and its screening results">Delete paper</a></div>
    </div>
    <div class="tabs" role="tablist">${tabs.map(([k, label]) => `<button class="tab ${S.tab === k ? "active" : ""}" data-tab="${k}">${label} ${counts[k] ?? ""}</button>`).join("")}</div>
    <div class="p-body" id="p-body">
      ${flags.length ? section(1) + section(2) : `<div class="empty-list">${emptyMsg}</div>`}
      ${S.tab === "all" || S.tab === "open" ? manualBlock(manual) : ""}
      <details class="box" id="audit-box"><summary>History</summary><ul class="audit" id="audit"></ul></details>
    </div>
    ${decisionBar(s, r, decision)}`;
  wirePanel();
}

function manualBlock(manual) {
  const final = isFinal();
  const list = manual.map((m) => `<div class="card"><div class="c-head"><div class="c-item">${esc(m.criterion_id || "Reviewer note")} <span class="num">· added by ${esc(m.reviewer)}</span></div><span class="pill grey">Added</span></div>
    <div class="quote">${esc(m.quote)}</div>${m.note ? `<div class="why">${esc(m.note)}</div>` : ""}
    <div class="c-foot"><span class="tag">Page ${m.page}</span>${m.location_exact ? "" : `<span class="muted">quote not found on the page</span>`}</div></div>`).join("");
  if (final) return list ? `<div class="list-head">Added by reviewers</div>${list}` : "";
  const pages = S.detail.result?.page_sizes?.length || 1;
  const opts = S.criteria.map((c) => `<option value="${esc(c.id)}">List ${c.list_no} #${c.number} ${esc(c.name)}</option>`).join("");
  return `${list ? `<div class="list-head">Added by reviewers</div>${list}` : ""}
    <details class="box"><summary>＋ Add a finding the tool missed</summary><div class="form-grid">
      <select id="m-crit"><option value="">List item (optional)</option>${opts}</select>
      <textarea id="m-quote" rows="2" placeholder="Paste the exact words from the page"></textarea>
      <div class="form-row"><select id="m-page">${Array.from({ length: pages }, (_, i) => `<option>${i + 1}</option>`).join("")}</select>
      <input id="m-note" type="text" placeholder="Note (optional)"></div>
      <button class="primary" id="m-add">Add finding</button></div></details>`;
}

function decisionBar(s, r, decision) {
  if (decision) {
    return `<div class="decision"><div class="d-final">${decision.outcome === "released" ? "✔ Released" : "↩ Returned to author"} by ${esc(decision.reviewer)}</div>
      <div class="muted small">${esc(decision.at)}${decision.note ? " — " + esc(decision.note) : ""}</div></div>`;
  }
  const pending = s.to_review - s.reviewed;
  const needNote = !r || r.completeness !== "complete";
  return `<div class="decision">
    <div class="d-progress"><span>${s.reviewed} of ${s.to_review} reviewed</span><progress value="${s.reviewed}" max="${Math.max(1, s.to_review)}"></progress></div>
    <label class="d-attest"><input type="checkbox" id="attest"> <span>I have personally reviewed this paper. The tool's result is not an approval.</span></label>
    ${needNote ? `<textarea id="note" rows="2" placeholder="Required: how were the parts the tool could not check reviewed?"></textarea>` : `<input id="note" type="text" placeholder="Decision note (optional)">`}
    <div class="d-row"><button class="primary" id="release" ${pending > 0 ? "disabled title='Review every finding first'" : ""}>Release</button>
    <button id="return">Return to author</button></div></div>`;
}

function wirePanel() {
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
    S.tab = b.dataset.tab;
    S.showLow = S.tab === "low";
    renderPanel();
    drawHighlights();
  }));
  document.querySelectorAll(".card[data-id]").forEach((c) => {
    c.onclick = (e) => { if (!e.target.closest("button, textarea, details, a")) select(c.dataset.id, true); };
    c.querySelectorAll("[data-act]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      if (b.dataset.act === "copy") return copyRewrite(c.dataset.id, +b.dataset.i, b);
      act(c.dataset.id, b.dataset.act);
    }));
  });
  $("#m-add") && ($("#m-add").onclick = addManual);
  $("#release") && ($("#release").onclick = () => decide("released"));
  $("#return") && ($("#return").onclick = () => decide("returned"));
  $("#dl-original") && ($("#dl-original").onclick = downloadOriginal);
  $("#rescreen") && ($("#rescreen").onclick = async (e) => {
    e.preventDefault();
    try { await post(`/api/papers/${S.current}/rescreen`, {}); toast("Screening again…"); refresh(); } catch (err) { toast(err.message, "error"); }
  });
  $("#delete-paper") && ($("#delete-paper").onclick = deletePaper);
  const box = $("#audit-box");
  if (box) box.ontoggle = () => box.open && loadAudit();
  const ta = $("#ov-reason");
  if (ta) { ta.focus(); ta.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) act(S.overriding, "ov-save"); if (e.key === "Escape") act(S.overriding, "ov-cancel"); }; }
}

function select(id, scroll) {
  S.selected = id;
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("selected", c.dataset.id === id));
  document.querySelectorAll(".hl").forEach((h) => h.classList.toggle("selected", h.dataset.flag === id));
  document.querySelector(`.card[data-id="${id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (scroll) {
    const f = S.detail.result.flags.find((x) => x.id === id);
    const target = document.querySelector(`.hl[data-flag="${id}"]`) || document.getElementById(`page-${f?.page}`);
    target?.scrollIntoView({ block: "center", behavior: "smooth" });
    document.querySelectorAll(`.hl[data-flag="${id}"]`).forEach((h) => { h.classList.remove("flash"); void h.offsetWidth; h.classList.add("flash"); });
  }
}

async function act(id, what) {
  if (what === "goto") return select(id, true);
  if (what === "rewrite") {
    S.rewrites[id] = { loading: true };
    renderPanel();
    try {
      S.rewrites[id] = await post(`/api/papers/${S.current}/flags/${id}/rewrites`, {});
    } catch (e) { S.rewrites[id] = { error: e.message }; }
    if (S.detail) renderPanel();
    return;
  }
  if (what === "copy") return;
  if (what === "override" || what === "change") { S.overriding = id; S.selected = id; return renderPanel(); }
  if (what === "ov-cancel") { S.overriding = null; return renderPanel(); }
  if (what === "reveal") {
    try {
      const { text } = await post(`/api/papers/${S.current}/flags/${id}/reveal`, {});
      const q = document.querySelector(`.card[data-id="${id}"] .quote`);
      q.textContent = text;
      q.classList.remove("masked");
      toast("Value shown (recorded in the history)");
    } catch (e) { toast(e.message, "error"); }
    return;
  }
  let action = what, reason = null;
  if (what === "ov-save") {
    reason = ($("#ov-reason")?.value || "").trim();
    if (!reason) { toast("An override needs a reason.", "error"); return; }
    action = "overridden";
  }
  try {
    await post(`/api/papers/${S.current}/flags/${id}/disposition`, { action, reason });
    S.overriding = null;
    toast(action === "accepted" ? "Accepted as a concern" : "Overridden");
    const next = nextOpen(id);
    await refresh();
    if (next) select(next, true);
  } catch (e) { toast(e.message, "error"); }
}

function nextOpen(afterId) {
  const list = (S.detail?.result?.flags || []).filter((f) => !f.low_confidence && !f.disposition);
  const i = list.findIndex((f) => f.id === afterId);
  return (list[i + 1] || list.find((f) => f.id !== afterId) || {}).id;
}

async function addManual() {
  const quote = $("#m-quote").value.trim();
  if (!quote) { toast("Paste the words from the page.", "error"); return; }
  try {
    await post(`/api/papers/${S.current}/manual-flags`, { quote, page: +$("#m-page").value || 1,
      criterion_id: $("#m-crit").value || null, note: $("#m-note").value.trim() || null });
    toast("Finding added");
    await refresh();
  } catch (e) { toast(e.message, "error"); }
}

async function decide(outcome) {
  if (!$("#attest").checked) { toast("Confirm that you personally reviewed the paper.", "error"); return; }
  try {
    await post(`/api/papers/${S.current}/decision`, { outcome, attested: true, note: $("#note").value.trim() || null });
    toast(outcome === "released" ? "Released" : "Returned to author");
    await refresh();
  } catch (e) { toast(e.message, "error"); }
}

async function copyRewrite(id, i, btn) {
  const text = S.rewrites[id]?.suggestions?.[i]?.text || "";
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch (_) { toast("Copy failed; select the text instead.", "error"); }
}

async function deletePaper(e) {
  e.preventDefault();
  if (!confirm(`Delete “${S.detail.summary.title}”? Its file, findings, reviews and decision are removed. The history log keeps a record that it was deleted. This cannot be undone.`)) return;
  try {
    await api(`/api/papers/${S.current}`, { method: "DELETE" });
    const list = visiblePapers();
    const i = list.findIndex((p) => p.id === S.current);
    const next = (list[i + 1] || list[i - 1] || {}).id;
    location.href = next ? `/?paper=${encodeURIComponent(next)}` : "/";
  } catch (err) { toast(err.message, "error"); }
}

async function downloadOriginal(e) {
  e.preventDefault();
  const blob = await (await api(`/api/papers/${S.current}/original`)).blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: S.detail.summary.filename });
  a.click();
  URL.revokeObjectURL(a.href);
}

async function loadAudit() {
  try {
    const rows = await api(`/api/papers/${S.current}/audit`);
    const chain = await api("/api/audit/verify");
    $("#audit").innerHTML = `<li class="${chain.chain_intact ? "muted" : ""}">${chain.chain_intact ? "History is intact (hash chain verified)" : "⚠ History hash chain is BROKEN"}</li>` +
      rows.map((a) => `<li><b>${esc(a.action.replaceAll("_", " "))}</b> · ${esc(a.actor)} · <span class="muted">${esc(a.at)}</span>${a.payload?.reason ? ` — ${esc(a.payload.reason)}` : ""}${a.payload?.why ? ` — ${esc(a.payload.why)}` : ""}</li>`).join("");
  } catch (e) { toast(e.message, "error"); }
}

// ---------------------------------------------------------------- keyboard

function onKey(e) {
  if (e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "?") { $("#help").showModal(); return; }
  if (k === "n" || k === "p") {
    const list = visiblePapers();
    const i = list.findIndex((p) => p.id === S.current);
    const next = list[k === "n" ? i + 1 : Math.max(0, i - 1)];
    if (next) { S.selected = null; openPaper(next.id, true); }
    return;
  }
  const flags = [...document.querySelectorAll(".card[data-id]")].map((c) => c.dataset.id);
  if (!flags.length) return;
  const i = flags.indexOf(S.selected);
  if (k === "j") select(flags[Math.min(flags.length - 1, i + 1)], true);
  else if (k === "k") select(flags[Math.max(0, i - 1)], true);
  else if (k === "a" && S.selected) act(S.selected, "accepted");
  else if (k === "o" && S.selected) { e.preventDefault(); act(S.selected, "override"); }
}


// ---------------------------------------------------------------- live screening view
// While a paper is screened: a progress bar over the document, the passage being read highlighted on its page,
// finished pages ticked, early findings marked, and a step list, page map and activity feed in the side panel.

const busy = (state) => state === "UPLOADED" || state === "SCREENING";
// The page's CSP blocks inline style attributes; progress widths are set through the CSSOM instead.
const fillWidths = (root) => root.querySelectorAll("[data-pct]").forEach((e) => { e.style.width = `${e.dataset.pct}%`; });
const clock = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;

function startLive() {
  if (S.liveFor === S.current && S.liveTimer) return;
  stopLive();
  S.liveFor = S.current;
  S.follow = true;
  pollLive();
}

function stopLive() {
  clearTimeout(S.liveTimer);
  S.liveTimer = null;
  S.liveFor = null;
  S.live = null;
  document.querySelectorAll(".live-el").forEach((e) => e.remove());
  S.liveEls?.clear();
  S.liveHtml = null;
  $("#live-bar")?.remove();
}

async function pollLive() {
  const id = S.liveFor;
  let snap;
  try { snap = await api(`/api/papers/${id}/progress`); } catch (_) { snap = null; }
  if (id !== S.current || id !== S.liveFor) return;
  if (snap && !busy(snap.state)) {           // finished: load the result
    stopLive();
    await refresh(true);
    toast("Screening finished");
    return;
  }
  S.live = snap;
  if (snap && snap.stage && snap.stage !== "extract" && S.pdfFor !== id) await renderPdf();   // Word file now converted
  drawLive();
  renderLivePanel();
  S.liveTimer = setTimeout(pollLive, 800);
}

function drawLive() {
  // Marks are kept by key and updated in place, so animations (the scan sweep, a new finding popping in) are not
  // restarted by every update.
  const L = S.live;
  liveBar(L);
  const els = (S.liveEls ||= new Map());
  const keep = new Set();
  const at = (page) => document.getElementById(`page-${page}`);
  const mark = (key, wrap, cls, b) => {
    keep.add(key);
    let d = els.get(key);
    if (!d || !d.isConnected) {
      d = document.createElement("div");
      wrap.appendChild(d);
      els.set(key, d);
    }
    if (d.className !== `live-el ${cls}`) d.className = `live-el ${cls}`;
    if (b) Object.assign(d.style, { left: b[0] * S.scale - 4 + "px", top: b[1] * S.scale - 3 + "px",
      width: (b[2] - b[0]) * S.scale + 8 + "px", height: (b[3] - b[1]) * S.scale + 6 + "px" });
    return d;
  };
  if (L && !L.queued) {
    for (const [page, state] of Object.entries(L.pages || {})) {
      const wrap = at(page);
      if (!wrap) continue;
      mark(`badge-${page}`, wrap, `live-badge ${state}`).textContent = { done: "✓ Checked", reading: "Reading…", waiting: "Waiting" }[state];
      if (state === "waiting") mark(`dim-${page}`, wrap, "live-dim");
    }
    let first = null;
    for (const r of L.reading || []) {
      const wrap = at(r.page);
      if (!wrap || !r.band) continue;
      const key = `band-${r.page}-${r.band.map(Math.round).join(",")}`;
      const band = mark(key, wrap, "scan-band", r.band);
      const text = r.items?.length ? `Checking: ${r.items.join(", ")}` : "Skimming for list items…";
      let tag = band.firstChild;
      if (!tag) { tag = band.appendChild(Object.assign(document.createElement("div"), { className: "scan-tag" })); }
      if (tag.textContent !== text) tag.textContent = text;
      first = first || { key, band };
    }
    (L.found || []).forEach((f, i) => {
      const wrap = at(f.page);
      if (!wrap) return;
      f.boxes.forEach((b, j) => {
        mark(`find-${i}-${j}`, wrap, `live-find ${f.color}`, b).title = `Possible finding: ${f.label} (being checked)`;
      });
    });
    if (first && S.follow && S.liveScrolled !== first.key) {       // follow each new passage once
      S.liveScrolled = first.key;
      first.band.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
  for (const [key, d] of els) if (!keep.has(key)) { d.remove(); els.delete(key); }
}

function liveBar(L) {
  let bar = $("#live-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "live-bar";
    bar.className = "live-bar";
    $(".viewer-pane").insertBefore(bar, $("#viewer"));
  }
  if (!L) { bar.innerHTML = `<div class="lb-row"><span class="spinner"></span> Starting…</div>`; return; }
  if (L.queued) {
    bar.innerHTML = `<div class="lb-row"><span class="spinner"></span><b>Waiting in the queue</b><span class="muted">position ${L.position ?? "?"}; papers are screened one at a time</span></div>`;
    return;
  }
  const eta = L.eta != null ? ` · about ${clock(L.eta)} left` : "";
  bar.innerHTML = `<div class="lb-row"><span class="spinner"></span><b>${esc(L.label)}</b>
      <span class="muted">${L.chunks_total ? `${L.chunks_done} of ${L.chunks_total} passage groups read · ` : ""}${clock(L.elapsed)} elapsed${eta}</span>
      <div class="spacer"></div><span class="lb-pct">${L.percent}%</span>
      <label class="lb-follow" title="Scroll to the passage being read"><input type="checkbox" id="follow" ${S.follow ? "checked" : ""}> Follow along</label></div>
    <div class="lb-track"><div class="lb-fill" data-pct="${L.percent}"></div></div>`;
  $("#follow").onchange = (e) => { S.follow = e.target.checked; };
  fillWidths(bar);
}

function renderLivePanel() {
  const L = S.live;
  const box = $("#live-panel");
  if (!box) return;
  if (!L || L.queued) {
    box.innerHTML = `<div class="note">${L?.queued ? `Waiting in the queue (position ${L.position ?? "?"}). Papers are screened one at a time, in upload order.` : "Starting…"}</div>`;
    return;
  }
  const icon = { done: "✓", active: '<span class="spinner"></span>', waiting: "○" };
  const steps = L.stages.map((st) => `<li class="${st.state}"><span class="st-icon">${icon[st.state]}</span>${esc(st.label)}
      ${st.id === "reading" && L.chunks_total ? `<span class="muted small">${L.chunks_done}/${L.chunks_total}</span>` : ""}</li>`).join("");
  const finds = {};
  (L.found || []).forEach((f) => { finds[f.page] = finds[f.page] === "red" || f.color === "red" ? "red" : "yellow"; });
  const pages = Array.from({ length: L.pages_total || 0 }, (_, i) => i + 1).map((n) => {
    const st = L.pages?.[n] || "none";
    return `<button class="pm-cell ${st} ${finds[n] ? "has-" + finds[n] : ""}" data-page="${n}" title="Page ${n}: ${{ done: "checked", reading: "being read", waiting: "waiting", none: "no passages to read (front matter, references or no text)" }[st]}${finds[n] ? "; possible finding" : ""}">${n}</button>`;
  }).join("");
  const seen = (S.liveSeen ||= new Set());
  const feed = (L.log || []).map((l) => {
    const key = `${l.t}|${l.msg}`;
    const fresh = !seen.has(key);
    seen.add(key);
    return `<li class="${esc(l.kind)}${fresh ? " new" : ""}"><span class="t">${clock(l.t)}</span>${esc(l.msg)}</li>`;
  }).join("");
  const html = `
    <div class="live-pct"><div class="lb-track"><div class="lb-fill" data-pct="${L.percent}"></div></div><b>${L.percent}%</b></div>
    <ol class="steps">${steps}</ol>
    ${pages ? `<div class="list-head">Pages</div><div class="page-map">${pages}</div>
      <div class="pm-legend muted small"><span class="pm-cell done"></span>checked <span class="pm-cell reading"></span>reading <span class="pm-cell waiting"></span>waiting <span class="pm-cell has-red"></span>possible finding</div>` : ""}
    <div class="list-head">Activity</div><ul class="feed">${feed}</ul>`;
  if (html.replaceAll(" new\"", "\"") === S.liveHtml && box.firstChild) return;
  S.liveHtml = html.replaceAll(" new\"", "\"");
  box.innerHTML = html;
  fillWidths(box);
  box.querySelectorAll(".pm-cell[data-page]").forEach((b) => (b.onclick = () => {
    S.follow = false;
    document.getElementById(`page-${b.dataset.page}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
    drawLive();
  }));
}

init().catch((e) => toast(e.message, "error"));
