const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const KEY = {Pass: "pass", Fail: "fail", Skip: "skip", Todo: "todo"};
let S = null, lastEvent = null, upUrl = "", sig = {list: "", detail: ""}, uploadId = null, sheets = {};

async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function upload(path, file) {
  const r = await fetch(path, {method: "POST", body: file});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, kind = "") {
  const d = document.createElement("div");
  d.className = "toast " + kind; d.textContent = msg;
  $("#toasts").appendChild(d); setTimeout(() => d.remove(), 4500);
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, "err"); } };

/* ---------- home ---------- */
async function loadSessions() {
  const {sessions} = await api("/api/sessions");
  const el = $("#sessionList");
  if (!sessions.length) { el.textContent = "Belum ada session."; return; }
  el.innerHTML = sessions.map(s => `<div class="srow"><div><b>${esc(s.id)}</b>
    <small>${s.broken ? "state rusak" : `${s.done}/${s.total} TC · ${s.images} gambar · ${s.doc_type === "sit" ? "SIT" : "Regresi"}`}</small></div>
    <div><button data-open="${esc(s.id)}">Lanjutkan</button> <button data-exp="${esc(s.id)}">Export</button> <button data-del="${esc(s.id)}">Hapus</button></div></div>`).join("");
}
$("#sessionList").addEventListener("click", guard(async e => {
  const o = e.target.dataset.open, d = e.target.dataset.del, x = e.target.dataset.exp;
  if (x) await exportSession(x);
  if (o) { await api("/api/sessions/open", {id: o}); await tick(); }
  if (d && confirm(`Hapus session "${d}" permanen, termasuk semua screenshot?`)) { await api("/api/sessions/delete", {id: d}); loadSessions(); }
}));
$("#fFile").addEventListener("change", guard(async () => {
  const f = $("#fFile").files[0]; $("#newErr").textContent = ""; $("#btnStart").disabled = true;
  if (!f) return;
  try { const j = await upload("/api/xlsx/inspect", f); uploadId = j.id; sheets = j.sheets; }
  catch (e) { $("#newErr").textContent = e.message; return; }
  $("#fSheet").innerHTML = Object.keys(sheets).map(n => `<option>${esc(n)}</option>`).join("");
  $("#fSheet").disabled = false; fillCols(); $("#btnStart").disabled = false;
}));
function fillCols() {
  const cols = sheets[$("#fSheet").value] || [];
  const pref = cols.findIndex(c => /ios/i.test(c));
  $("#fResult").innerHTML = `<option value="">(tanpa kolom result)</option>` +
    cols.map((c, i) => `<option ${i === pref ? "selected" : ""}>${esc(c)}</option>`).join("");
  $("#fResult").disabled = false;
}
$("#fSheet").addEventListener("change", fillCols);
$("#btnStart").addEventListener("click", guard(async () => {
  await api("/api/sessions", {id: uploadId, sheet: $("#fSheet").value, doc_type: $("#fDoc").value,
    result_col: $("#fResult").value, build: $("#fBuild").value, device: $("#fDevice").value});
  await tick();
}));

/* ---------- export / import ---------- */
async function exportSession(id) {
  const r = await api("/api/export", id ? {id} : {});
  const a = document.createElement("a");
  a.href = "/api/download?name=" + encodeURIComponent(r.name); a.download = r.name;
  document.body.appendChild(a); a.click(); a.remove();
  toast("Export: " + r.path + " — kirim hanya lewat kanal aman (berisi screenshot)");
}
$("#btnExport").onclick = guard(() => exportSession());
$("#btnImport").onclick = () => $("#fImport").click();
$("#fImport").onchange = guard(async () => {
  const f = $("#fImport").files[0]; $("#fImport").value = ""; if (!f) return;
  const r = await upload("/api/import", f);
  toast("Session diimpor: " + r.id); loadSessions();
});

/* ---------- setup dialog ---------- */
const openSetup = () => { $("#upUrl").value = upUrl; $("#setup").showModal(); };
$("#btnSetup").onclick = $("#btnSetupHome").onclick = openSetup;
$("#btnCopyUrl").onclick = guard(async () => { await navigator.clipboard.writeText(upUrl); toast("URL disalin"); });

/* ---------- work screen ---------- */
$("#btnHome").onclick = guard(async () => { await api("/api/sessions/close", {}); sig = {list: "", detail: ""}; await tick(); loadSessions(); });
$("#fStatus").onchange = $("#fSearch").oninput = () => { sig.list = ""; renderList(); };
const live = () => S.cases.filter(c => !c.removed).sort((a, b) => a.row - b.row);
function visible() {
  const st = $("#fStatus").value, q = $("#fSearch").value.toLowerCase();
  return live().filter(t => (st === "all" || t.status === st) && (!q || (t.tc_no + " " + t.title + " " + t.module).toLowerCase().includes(q)));
}
function renderList() {
  const vis = visible(), gone = S.cases.filter(c => c.removed);
  const s = JSON.stringify([vis.map(t => [t.tc_no, t.status, t.images.length]), gone.map(t => t.tc_no), S.active]);
  if (s === sig.list) return; sig.list = s;
  let html = "", mod = null;
  const item = (t, cls) => `<div class="tc ${cls} ${t.tc_no === S.active ? "on" : ""}" data-no="${esc(t.tc_no)}"><span class="dot s-${KEY[t.status]}"></span>
    <span class="t">${esc(t.tc_no)} · ${esc(t.title)}</span><span class="badge">${t.images.length ? "🖼 " + t.images.length : ""}</span></div>`;
  for (const t of vis) {
    if (t.module !== mod) { mod = t.module; html += `<div class="mod">${esc(mod)}</div>`; }
    html += item(t, "");
  }
  if (gone.length) html += `<div class="mod">Dihapus dari Excel</div>` + gone.map(t => `<div class="tc removed"><span class="dot s-skip"></span><span class="t">${esc(t.tc_no)} · ${esc(t.title)}</span></div>`).join("");
  const el = $("#tcList"), top = el.scrollTop;
  el.innerHTML = html || `<p class="muted" style="padding:12px">Tidak ada TC.</p>`; el.scrollTop = top;
}
$("#tcList").addEventListener("click", guard(async e => {
  const r = e.target.closest(".tc[data-no]"); if (r) await setActive(r.dataset.no);
}));
const setActive = async no => { await api("/api/active", {no}); await tick(); };

function renderDetail() {
  const t = S.cases.find(x => x.tc_no === S.active && !x.removed);
  const s = JSON.stringify(t ? [t.tc_no, t.status, t.images] : null);
  const ta = $("#remark");
  if (s === sig.detail) { if (ta && document.activeElement !== ta && t && ta.value !== t.remark) ta.value = t.remark; return; }
  sig.detail = s;
  if (!t) { $("#detail").innerHTML = `<p class="muted">Pilih TC di kiri, lalu screenshot lewat iPhone.</p>`; return; }
  const seg = ["Pass", "Fail", "Skip", "Todo"].map(k =>
    `<button class="${KEY[k]} ${t.status === k ? "on" : ""}" data-st="${k}">${k}${{Pass: " (P)", Fail: " (F)", Skip: " (S)"}[k] || ""}</button>`).join("");
  const shots = t.images.map((n, i) => `<div class="shot"><img src="/shots/${encodeURIComponent(n)}" data-big="${esc(n)}">
    <div class="bar"><span><button data-sh="-1" data-n="${esc(n)}" ${i === 0 ? "disabled" : ""}>◀</button>
    <button data-sh="1" data-n="${esc(n)}" ${i === t.images.length - 1 ? "disabled" : ""}>▶</button></span>
    <span><button data-mv="${esc(n)}">Pindah</button> <button data-rm="${esc(n)}">Hapus</button></span></div></div>`).join("");
  $("#detail").innerHTML = `<h2>${esc(S.headings[t.tc_no] || t.title)}</h2><div class="muted">${esc(t.tc_no)} · ${esc(t.module)}${t.type ? " · " + esc(t.type) : ""}</div>
    ${t.steps ? `<div class="kv"><b>Steps</b>${esc(t.steps)}</div>` : ""}${t.expected ? `<div class="kv"><b>Expected</b>${esc(t.expected)}</div>` : ""}
    <div class="seg">${seg}</div><label>Remark<textarea id="remark" rows="3">${esc(t.remark)}</textarea></label>
    <div class="shots">${shots || `<span class="muted">Belum ada gambar. Jalankan Shortcut di iPhone.</span>`}</div>`;
  let timer; $("#remark").oninput = e => { clearTimeout(timer); timer = setTimeout(() => api("/api/remark", {no: t.tc_no, remark: e.target.value}).catch(x => toast(x.message, "err")), 400); };
}
$("#detail").addEventListener("click", guard(async e => {
  const d = e.target.dataset, no = S.active, t = S.cases.find(x => x.tc_no === no);
  if (d.st) await api("/api/status", {no, status: d.st});
  else if (d.rm) await api("/api/image/delete", {no, name: d.rm});
  else if (d.sh) {
    const names = [...t.images], i = names.indexOf(d.n), j = i + +d.sh;
    [names[i], names[j]] = [names[j], names[i]];
    await api("/api/image/reorder", {no, names});
  }
  else if (d.mv) { const to = prompt("Pindah ke No TC:"); if (!to) return; await api("/api/image/move", {no, name: d.mv, to: to.trim()}); }
  else if (d.big) { $("#lbImg").src = "/shots/" + encodeURIComponent(d.big); $("#lightbox").showModal(); return; }
  else return;
  await tick();
}));

/* ---------- Sync / Preview / Copy / Generate ---------- */
$("#btnSync").onclick = () => $("#fSync").click();
$("#fSync").onchange = guard(async () => {
  const f = $("#fSync").files[0]; $("#fSync").value = ""; if (!f) return;
  const d = await upload("/api/sync/inspect", f);
  if (!d.added.length && !d.changed.length && !d.removed.length) { toast("Tidak ada perubahan."); return; }
  const msg = `Perubahan dari Excel baru:\n+ ${d.added.length} TC baru: ${d.added.join(", ") || "-"}\n~ ${d.changed.length} TC berubah: ${d.changed.map(c => c.tc_no).join(", ") || "-"}\n− ${d.removed.length} TC dihapus: ${d.removed.join(", ") || "-"}\n\nStatus, remark, dan gambar tetap. Terapkan?`;
  if (!confirm(msg)) return;
  await api("/api/sync/apply", {id: d.id}); toast("Sinkron selesai"); await tick();
});
$("#btnPreview").onclick = guard(async () => {
  const p = await api("/api/preview");
  $("#pvTitle").textContent = p.file;
  const imgs = c => `<div class="imgs">${c.images.map(n => `<img src="/shots/${encodeURIComponent(n)}" style="width:${c.width_cm}cm">`).join("")}</div>`;
  $("#pvBody").className = "pv";
  $("#pvBody").innerHTML = p.layout.map(m => p.doc_type === "sit"
    ? m.cases.map(c => `<table><tr><td>No. TC</td><td>${esc(c.label)}</td></tr><tr><td>Scenario</td><td>${esc(c.title)}</td></tr>
        <tr><td>Category</td><td>${esc(c.type)}</td></tr><tr><td>Steps</td><td>${esc(c.steps)}</td></tr>
        <tr><td>Expected Result</td><td>${esc(c.expected)}</td></tr><tr><td>Screen Capture</td><td>${imgs(c)}</td></tr></table>`).join("")
    : `<h3>${esc(m.module)}</h3>` + m.cases.map(c => `<h4>${esc(c.heading)}</h4>${imgs(c)}`).join("")).join("") || "<p class='muted'>Belum ada isi.</p>";
  $("#preview").showModal();
});
async function copyCol(kind) {
  const r = await api("/api/results?kind=" + kind);
  await navigator.clipboard.writeText(r.text); toast("Tersalin. " + r.hint);
}
$("#btnResult").onclick = guard(() => copyCol("result"));
$("#btnRemark").onclick = guard(() => copyCol("remark"));
$("#btnGen").onclick = guard(async () => {
  const w = await api("/api/precheck");
  const msg = [w.no_image.length && `${w.no_image.length} TC belum punya gambar`, w.todo.length && `${w.todo.length} TC masih Todo`].filter(Boolean);
  if (msg.length && !confirm(msg.join(", ") + ".\nLanjut generate?")) return;
  const r = await api("/api/generate", {}); toast("Tersimpan: " + r.path);
});

document.addEventListener("keydown", guard(async e => {
  if (!S || !S.id || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.querySelector("dialog[open]")) return;
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    e.preventDefault(); const v = visible(); if (!v.length) return;
    let i = v.findIndex(t => t.tc_no === S.active); i = e.key === "ArrowDown" ? Math.min(v.length - 1, i + 1) : Math.max(0, i < 0 ? 0 : i - 1);
    await setActive(v[i].tc_no);
  } else if (S.active && "pfs".includes(e.key.toLowerCase()) && !e.metaKey && !e.ctrlKey) {
    await api("/api/status", {no: S.active, status: {p: "Pass", f: "Fail", s: "Skip"}[e.key.toLowerCase()]}); await tick();
  } else if (e.key === "Delete" && S.active) {
    const t = S.cases.find(x => x.tc_no === S.active);
    if (t && t.images.length) { await api("/api/image/delete", {no: t.tc_no, name: t.images[t.images.length - 1]}); await tick(); }
  }
}));

/* ---------- polling ---------- */
async function tick() {
  const st = await api("/api/state");
  upUrl = st.upload_url;
  const first = lastEvent === null;
  for (const ev of st.events) if (!first && ev.id > lastEvent) toast(ev.msg, ev.kind === "warn" ? "warn" : "");
  lastEvent = st.events.length ? st.events[st.events.length - 1].id : (lastEvent ?? 0);
  S = st.session;
  const has = !!S;
  $("#home").classList.toggle("hidden", has); $("#work").classList.toggle("hidden", !has);
  if (!has) return;
  const cnt = k => live().filter(c => c.status === k).length;
  $("#wTitle").textContent = S.id;
  $("#wProg").textContent = `${S.progress.done}/${S.progress.total} · ✓${cnt("Pass")} ✗${cnt("Fail")} ⤼${cnt("Skip")} · ${live().reduce((n, c) => n + c.images.length, 0)} gambar`;
  renderList(); renderDetail();
}
loadSessions().catch(() => {});
tick().catch(e => toast(e.message, "err"));
setInterval(() => tick().catch(() => {}), 1000);
