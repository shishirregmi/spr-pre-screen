/* Checks page: add, test, edit, switch off and delete custom checks; edit, remove and restore official list items. */
const C = { custom: [], official: [], editing: null, official_edit: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const d = (await res.json()).detail;
      msg = Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ") : d || msg;
    } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}
const send = (path, method, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3000);
}
const lines = (v) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
const patternLines = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);
const NO_SOURCE = "Custom check added in the app";

function formData() {
  if (C.official_edit) {
    const o = C.official_edit;
    return { name: $("#f-name").value, judgment: $("#f-judgment").checked, description: $("#f-desc").value,
      flag_when: $("#f-when").value, not_when: $("#f-not").value, positive_examples: patternLines($("#f-pos").value),
      negative_examples: patternLines($("#f-neg").value), keywords: lines($("#f-words").value),
      related_terms: o.related_terms || [], active: o.active !== false };
  }
  return {
    name: $("#f-name").value, list_no: +document.querySelector("input[name=list]:checked").value,
    judgment: $("#f-judgment").checked, description: $("#f-desc").value, flag_when: $("#f-when").value,
    not_when: $("#f-not").value, positive_examples: patternLines($("#f-pos").value),
    negative_examples: patternLines($("#f-neg").value), keywords: lines($("#f-words").value),
    patterns: patternLines($("#f-patterns").value), source_policy: $("#f-source").value,
    active: C.editing ? C.custom.find((c) => c.id === C.editing)?.active ?? true : true,
  };
}

function fillForm(c) {
  const official = !!c && c.origin !== "custom";
  C.official_edit = official ? c : null;
  $("#official-note").hidden = !official;
  ["#f-list-row", "#f-patterns-box", "#f-source-row"].forEach((s) => ($(s).hidden = official));
  $("#f-name").value = c?.name || "";
  document.querySelector(`input[name=list][value="${c?.list_no || 2}"]`).checked = true;
  $("#f-judgment").checked = !!c?.judgment;
  $("#f-desc").value = c?.description || "";
  $("#f-when").value = c?.flag_when || "";
  $("#f-not").value = c?.not_when || "";
  $("#f-pos").value = (c?.positive_examples || []).join("\n");
  $("#f-neg").value = (c?.negative_examples || []).join("\n");
  $("#f-words").value = (c?.keywords || []).join("\n");
  $("#f-patterns").value = (c?.patterns || []).map((p) => p.regex).filter(Boolean).join("\n");
  $("#f-source").value = c && c.source_policy !== NO_SOURCE ? c.source_policy : "";
  C.editing = official ? null : c?.id || null;
  $("#form-title").textContent = official ? `Edit List ${c.list_no} #${c.number} (${c.id})` : c ? `Edit ${c.id}` : "Add a custom check";
  $("#save").textContent = c ? "Save changes" : "Save check";
  $("#cancel").hidden = !c;
  if (c) $("#f-name").focus();
}

async function load() {
  try {
    const d = await api("/api/checks");
    C.custom = d.custom;
    C.official = d.official;
    $("#version").textContent = `rulebook ${d.rulebook_version}`;
  } catch (e) { toast(e.message, "error"); return; }
  render();
}

function colorNote(c) {
  if (c.judgment) return `<span class="pill yellow">Yellow at most</span>`;
  return c.list_no === 1 ? `<span class="pill red">Red when clear</span>` : `<span class="pill red">Red</span>/<span class="pill yellow">Yellow</span>`;
}

function officialActions(c) {
  if (c.active === false) {
    return `<div class="actions"><button data-act="putback">↩ Put back</button>${c.edited ? `<button class="ghost" data-act="restore">Restore original</button>` : ""}</div>`;
  }
  return `<div class="actions"><button data-act="edit">Edit</button><button class="danger ghost" data-act="remove">Remove</button>
    ${c.edited ? `<button class="ghost" data-act="restore" title="Undo every change to this item">Restore original</button>` : ""}</div>`;
}

function checkCard(c, editable) {
  const official = c.origin !== "custom";
  const words = (c.keywords || []).slice(0, 8).map((w) => `<span class="tag">${esc(w)}</span>`).join(" ");
  const pats = (c.patterns || []).filter((p) => p.regex).map((p) => `<code>${esc(p.regex)}</code>`).join(" ");
  const state = official && c.active === false ? ` <span class="tag removed">Removed: not screened</span>`
    : official && c.edited ? ` <span class="tag edited">Edited</span>` : "";
  return `<div class="check ${c.active === false ? (official ? "removed" : "off") : ""}" data-id="${esc(c.id)}">
    <div class="c-head"><div class="c-item">${esc(c.name)} <span class="num">· ${esc(c.id)} · List ${c.list_no}${c.number != null ? " #" + c.number : ""}</span>${state}</div>${colorNote(c)}</div>
    <div class="why">${esc(c.description)}</div>
    ${c.flag_when ? `<div class="small"><b>Flag when:</b> ${esc(c.flag_when)}</div>` : ""}
    ${c.not_when ? `<div class="small"><b>Not a finding:</b> ${esc(c.not_when)}</div>` : ""}
    ${words ? `<div class="small c-words"><b>Words:</b> ${words}</div>` : ""}
    ${pats ? `<div class="small"><b>Patterns:</b> ${pats}</div>` : ""}
    ${editable ? `<div class="actions">
      <label class="radio small"><input type="checkbox" data-act="toggle" ${c.active === false ? "" : "checked"}> In use</label>
      <button data-act="edit">Edit</button><button class="danger ghost" data-act="delete">Delete</button>
      <span class="muted small">${c.source_policy && c.source_policy !== NO_SOURCE ? "Source: " + esc(c.source_policy) : ""}</span></div>`
      : official ? officialActions(c) : ""}
  </div>`;
}

function render() {
  $("#custom").innerHTML = C.custom.length ? C.custom.map((c) => checkCard(c, true)).join("")
    : `<div class="empty-list">No custom checks yet. Add one with the form; it is used on every paper screened from then on.</div>`;
  const group = (n) => C.official.filter((c) => c.list_no === n);
  const summary = (n) => {
    const g = group(n), off = g.filter((c) => c.active === false).length, ed = g.filter((c) => c.edited && c.active !== false).length;
    return `List ${n} · ${g.length - off} in use${off ? ` · ${off} removed` : ""}${ed ? ` · ${ed} edited` : ""}`;
  };
  const opened = new Set([...document.querySelectorAll("#official details[open]")].map((d) => d.dataset.list));
  $("#official").innerHTML = [1, 2].map((n) => `<details class="box" data-list="${n}" ${opened.size ? (opened.has(String(n)) ? "open" : "") : n === 1 ? "open" : ""}><summary>${summary(n)}</summary>
    ${group(n).map((c) => checkCard(c, false)).join("")}</details>`).join("");
  document.querySelectorAll("#official .check").forEach((el) => {
    const c = C.official.find((x) => x.id === el.dataset.id);
    const on = (act, fn) => { const b = el.querySelector(`[data-act="${act}"]`); if (b) b.onclick = fn; };
    on("edit", () => { fillForm(c); window.scrollTo({ top: 0, behavior: "smooth" }); });
    on("remove", () => removeOfficial(c));
    on("putback", () => putBack(c));
    on("restore", () => restoreOfficial(c));
  });
  document.querySelectorAll("#custom .check").forEach((el) => {
    const c = C.custom.find((x) => x.id === el.dataset.id);
    el.querySelector('[data-act="edit"]').onclick = () => { fillForm(c); window.scrollTo({ top: 0, behavior: "smooth" }); };
    el.querySelector('[data-act="delete"]').onclick = () => remove(c);
    el.querySelector('[data-act="toggle"]').onchange = (e) => toggle(c, e.target.checked);
  });
}

function bodyOf(c, active) {
  return { name: c.name, list_no: c.list_no, judgment: c.judgment, description: c.description, flag_when: c.flag_when,
    not_when: c.not_when, positive_examples: c.positive_examples, negative_examples: c.negative_examples,
    keywords: c.keywords, patterns: (c.patterns || []).map((p) => p.regex).filter(Boolean),
    source_policy: c.source_policy === NO_SOURCE ? "" : c.source_policy, active };
}

async function toggle(c, active) {
  try {
    await send(`/api/checks/${encodeURIComponent(c.id)}`, "PUT", bodyOf(c, active));
    toast(active ? `${c.id} is in use` : `${c.id} switched off`);
    load();
  } catch (e) { toast(e.message, "error"); load(); }
}

async function remove(c) {
  if (!confirm(`Delete the check “${c.name}”? Papers already screened keep their findings until screened again.`)) return;
  try {
    await api(`/api/checks/${encodeURIComponent(c.id)}`, { method: "DELETE" });
    if (C.editing === c.id) fillForm(null);
    toast("Check deleted");
    load();
  } catch (e) { toast(e.message, "error"); }
}

const label = (c) => `List ${c.list_no} #${c.number} “${c.name}”`;

async function removeOfficial(c) {
  if (!confirm(`Remove ${label(c)} from screening? Papers screened from now on are not checked for it. You can put it back at any time.`)) return;
  try {
    await api(`/api/lists/${encodeURIComponent(c.id)}`, { method: "DELETE" });
    if (C.official_edit?.id === c.id) fillForm(null);
    toast(`${c.id} removed from screening`);
    load();
  } catch (e) { toast(e.message, "error"); }
}

async function putBack(c) {
  const body = { name: c.name, description: c.description, flag_when: c.flag_when, not_when: c.not_when,
    keywords: c.keywords, related_terms: c.related_terms, positive_examples: c.positive_examples,
    negative_examples: c.negative_examples, judgment: c.judgment, active: true };
  try {
    await send(`/api/lists/${encodeURIComponent(c.id)}`, "PUT", body);
    toast(`${c.id} is screened again`);
    load();
  } catch (e) { toast(e.message, "error"); }
}

async function restoreOfficial(c) {
  if (!confirm(`Restore ${label(c)} to the official wording? Every change made here is undone and it is screened again.`)) return;
  try {
    await api(`/api/lists/${encodeURIComponent(c.id)}/restore`, { method: "POST" });
    if (C.official_edit?.id === c.id) fillForm(null);
    toast(`${c.id} restored to the official wording`);
    load();
  } catch (e) { toast(e.message, "error"); }
}

async function save(ev) {
  ev.preventDefault();
  if (C.official_edit) {
    try {
      const r = await send(`/api/lists/${encodeURIComponent(C.official_edit.id)}`, "PUT", formData());
      toast(`Saved ${r.item.id}. It applies to papers screened from now on; use “Re-screen open papers” for the rest.`);
      fillForm(null);
      load();
    } catch (e) { toast(e.message, "error"); }
    return;
  }
  try {
    const r = C.editing ? await send(`/api/checks/${encodeURIComponent(C.editing)}`, "PUT", formData())
      : await send("/api/checks", "POST", formData());
    toast(`${C.editing ? "Saved" : "Added"} ${r.check.id}. It applies to papers screened from now on; use “Re-screen open papers” for the rest.`);
    fillForm(null);
    $("#try-out").innerHTML = "";
    load();
  } catch (e) { toast(e.message, "error"); }
}

async function tryIt() {
  const text = $("#try-text").value;
  if (!text.trim()) { toast("Paste some text to test on.", "error"); return; }
  const body = { ...formData(), patterns: C.official_edit ? [] : formData().patterns };
  delete body.related_terms;
  if (body.name.length < 3) body.name = "Test";
  if (body.description.length < 10) body.description = "Test of words and patterns";
  try {
    const r = await send("/api/checks/try", "POST", { check: body, text });
    const words = r.words.length ? r.words.map((w) => `<span class="tag">${esc(w.word)} ×${w.count}</span>`).join(" ") : "none";
    const pats = r.patterns.map((p) => `<div><code>${esc(p.pattern)}</code>: ${p.matches.length
      ? p.matches.map((m) => `<div class="quote">${esc(m.context)}</div>`).join("") : "no match"}</div>`).join("");
    $("#try-out").innerHTML = `<div><b>Words found:</b> ${words}</div>${pats}`;
  } catch (e) { toast(e.message, "error"); }
}

async function rescreen() {
  if (!confirm("Screen every paper that has no decision yet again, with the current checks? Accept/override choices on those papers are cleared.")) return;
  try {
    const r = await api("/api/papers/rescreen-open", { method: "POST" });
    toast(`${r.queued} paper(s) queued for screening.`);
  } catch (e) { toast(e.message, "error"); }
}

async function draft() {
  const idea = $("#draft-idea").value.trim();
  if (idea.length < 5) { toast("Describe what to catch first.", "error"); return; }
  const btn = $("#draft");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Drafting… (about 10–30 s)`;
  $("#draft-notes").innerHTML = "";
  try {
    const r = await send("/api/checks/draft", "POST", { idea });
    fillForm(null);
    const d = r.draft;
    $("#f-name").value = d.name || "";
    document.querySelector(`input[name=list][value="${d.list_no === 1 ? 1 : 2}"]`).checked = true;
    $("#f-judgment").checked = !!d.judgment;
    $("#f-desc").value = d.description || "";
    $("#f-when").value = d.flag_when || "";
    $("#f-not").value = d.not_when || "";
    $("#f-pos").value = (d.positive_examples || []).join("\n");
    $("#f-neg").value = (d.negative_examples || []).join("\n");
    $("#f-words").value = (d.keywords || []).join("\n");
    $("#f-patterns").value = (d.patterns || []).join("\n");
    if ((d.patterns || []).length) $("#f-patterns-box").open = true;
    $("#f-source").value = "";
    const notes = [`Filled from your description. Read it, adjust anything, then <b>Save check</b>.`, ...r.notes.map(esc)];
    if (r.overlaps_with) {
      notes.push(`<button type="button" class="ghost small-btn" id="edit-overlap">Edit ${esc(r.overlaps_with)} instead</button>`);
    }
    $("#draft-notes").innerHTML = notes.map((n) => `<div class="note">${n}</div>`).join("");
    const ov = $("#edit-overlap");
    if (ov) ov.onclick = () => fillForm(C.official.find((c) => c.id === r.overlaps_with));
    $("#try-text").value ||= (d.positive_examples || []).concat(d.negative_examples || []).join("\n");
  } catch (e) { toast(e.message, "error"); }
  btn.disabled = false;
  btn.textContent = "Generate and fill the form";
}

$("#form").onsubmit = save;
$("#draft").onclick = draft;
$("#cancel").onclick = () => fillForm(null);
$("#try").onclick = tryIt;
$("#rescreen").onclick = rescreen;
load();
