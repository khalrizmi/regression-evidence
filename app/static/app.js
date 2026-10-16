'use strict';

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const HOTKEY_STATUS = {p: 'Pass', f: 'Fail', s: 'Skip'};

let state = null;          // last /api/state payload with a session
let stateKey = '';         // serialized session part; skip identical renders
let lastEvent = null;      // null until first poll, so old events are not toasted
let pollTimer = null;
let view = 'work';
let newUpload = null;      // {upload_id, sheets, result_columns}
let deviceList = [];
const DOC_TYPE = {regression: 'Regresi', sit: 'SIT'};
let renderedActive = null;
let remarkTimer = null;
const collapsed = new Set();

/* ---------- helpers ---------- */
async function api(path, body) {
  const opts = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function upload(path, file) {
  const res = await fetch(path, {method: 'POST', headers: {'X-Filename': encodeURIComponent(file.name)}, body: file});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(text, kind = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = text;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 6000 : 2500);
}

async function guard(fn) {
  try { await fn(); } catch (e) { toast(e.message, 'error'); }
}

function dialog(html, okText = 'OK', showCancel = true) {
  return new Promise((resolve) => {
    const d = $('#dialog');
    $('#dialog-body').innerHTML = html;
    $('#dialog-ok').textContent = okText;
    $('#dialog-cancel').hidden = !showCancel;
    d.returnValue = '';
    d.onclose = () => resolve(d.returnValue === 'ok');
    d.showModal();
  });
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch (_) { /* fallback below */ }
  const ta = document.createElement('textarea');
  ta.value = text;
  document.body.append(ta);
  ta.select();
  document.execCommand('copy');
  ta.remove();
}

/* ---------- home ---------- */
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

async function showHome() {
  stopPolling();
  $('#work').hidden = true;
  $('#home').hidden = false;
  const {sessions} = await api('/api/sessions');
  $('#session-list').innerHTML = sessions.length ? sessions.map((s) => s.broken
    ? `<li><span><b>${esc(s.id)}</b><br><small class="error">state.json rusak</small></span>
         <span><button class="btn" data-restore="${esc(s.id)}">Pulihkan backup</button>
         <button class="btn danger" data-delete="${esc(s.id)}">Hapus</button></span></li>`
    : `<li><span><b>${DOC_TYPE[s.doc_type]} · ${esc(s.platform)} · ${esc(s.sheet)}</b> · build ${esc(s.build)}<br>
         <small class="muted">${s.done}/${s.total} selesai · terakhir ${esc(s.opened.replace('T', ' '))}</small></span>
         <span><button class="btn primary" data-open="${esc(s.id)}">Lanjutkan</button>
         <button class="btn danger" data-delete="${esc(s.id)}" data-images="${s.images}">Hapus</button></span></li>`).join('')
    : '<li class="muted">Belum ada session.</li>';
  loadDevices();
}

async function loadDevices() {
  await guard(async () => {
    const d = await api('/api/devices');
    deviceList = d.devices;
    $('#device-select').innerHTML = d.devices.length
      ? d.devices.map((x) => `<option value="${esc(x.serial)}">${esc(x.label || x.serial)}</option>`).join('')
      : `<option value="">${d.adb ? '(tidak ada device adb terhubung)' : '(adb tidak ditemukan — capture nonaktif)'}</option>`;
    fillBuild();
  });
}

function fillBuild() {
  const dev = deviceList.find((x) => x.serial === $('#device-select').value);
  if (dev && dev.build && !$('#build-input').value) $('#build-input').value = dev.build;
}

function fillResultColumns() {
  const cols = (newUpload?.result_columns || {})[$('#platform-select').value] || [];
  $('#result-select').innerHTML = cols.map((c) => `<option>${esc(c)}</option>`).join('') + '<option value="">(tanpa kolom result)</option>';
}

async function inspectSheet() {
  $('#new-error').textContent = '';
  try {
    const info = await api('/api/inspect', {upload_id: newUpload.upload_id, sheet: $('#sheet-select').value});
    newUpload.result_columns = info.result_columns;
    $('#sheet-info').textContent = `${info.count} TC · ${info.modules} modul`;
  } catch (e) {
    newUpload.result_columns = {};
    $('#sheet-info').textContent = '';
    $('#new-error').textContent = e.message;
  }
  fillResultColumns();
}

$('#session-list').addEventListener('click', (e) => guard(async () => {
  const {open, restore, delete: del, images} = e.target.dataset;
  if (del) {
    const shots = images ? `${images} screenshot, ` : '';
    if (!confirm(`Hapus permanen session ${del}?\n\nSemua isinya (${shots}dokumen di out/) ikut terhapus `
      + 'dan tidak bisa dikembalikan. Pastikan dokumen sudah di-upload.')) return;
    await api('/api/sessions/delete', {id: del});
    toast('Session dihapus');
    await showHome();
  }
  if (open) { await api('/api/sessions/open', {id: open}); await enterWork(); }
  if (restore) { await api('/api/sessions/restore', {id: restore}); await enterWork(); }
}));

$('#import-file').addEventListener('change', (e) => guard(async () => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const {id} = await upload('/api/import', file);
  await api('/api/sessions/open', {id});
  await enterWork();
}));

$('#xlsx-file').addEventListener('change', (e) => guard(async () => {
  newUpload = null;
  $('#sheet-select').innerHTML = '';
  $('#sheet-info').textContent = '';
  const file = e.target.files[0];
  if (!file) return;
  newUpload = await upload('/api/upload/xlsx', file);
  $('#sheet-select').innerHTML = newUpload.sheets.map((s) => `<option>${esc(s)}</option>`).join('');
  await inspectSheet();
}));

$('#sheet-select').addEventListener('change', () => newUpload && inspectSheet());
$('#platform-select').addEventListener('change', fillResultColumns);
$('#device-select').addEventListener('change', () => { $('#build-input').value = ''; fillBuild(); });

$('#new-form').addEventListener('submit', (e) => {
  e.preventDefault();
  guard(async () => {
    if (!newUpload) throw new Error('Pilih file Excel dulu');
    await api('/api/sessions/create', {
      upload_id: newUpload.upload_id,
      sheet: $('#sheet-select').value,
      platform: $('#platform-select').value,
      doc_type: $('#doctype-select').value,
      result_column: $('#result-select').value,
      build: $('#build-input').value,
      device: $('#device-select').value,
    });
    await enterWork();
  });
});

/* ---------- work ---------- */
async function enterWork() {
  $('#home').hidden = true;
  $('#work').hidden = false;
  stateKey = '';
  renderedActive = null;
  setView('work');
  await refresh();
  stopPolling();
  pollTimer = setInterval(() => refresh().catch(() => {}), 1000);
}

async function refresh() {
  const d = await api('/api/state?since=' + (lastEvent ?? 0));
  if (lastEvent !== null) d.events.forEach((ev) => toast(ev.text, ev.kind === 'shot' ? 'ok' : ev.kind));
  lastEvent = d.last_event;
  if (!d.session) { await showHome(); return; }
  const key = JSON.stringify([d.session, d.adb, d.headings]);
  if (key === stateKey) return;
  stateKey = key;
  state = d;
  render();
}

function activeCase() {
  return state.session.cases.find((c) => c.tc_no === state.session.active);
}

function render() {
  const s = state.session;
  $('#work-title').innerHTML = `<b>${esc(s.sheet)}</b> · ${DOC_TYPE[s.doc_type || 'regression']} · ${esc(s.platform)} · build ${esc(s.build)}`;
  $('#progress').textContent = `${state.progress.done}/${state.progress.total} selesai`;
  const connected = state.adb.state === 'connected';
  const preparing = ['connecting', 'loading', 'waiting'].includes(state.adb.state);
  const via = state.adb.via ? ` (${state.adb.via})` : '';
  $('#adb-status').textContent = connected ? `● ${state.adb.serial || 'device'} terhubung${via}`
    : preparing ? `● ${state.adb.serial || 'HP'} menyiapkan…${via}` : '● capture nonaktif';
  $('#adb-status').className = 'pill ' + (connected ? 'ok' : 'bad');
  $('#banner').hidden = connected || !state.adb.message;
  $('#banner').textContent = state.adb.message || '';
  fillFilters();
  renderList();
  renderDetail();
  if (view === 'preview') renderPreview();
}

function fillSelect(sel, label, values) {
  const el = $(sel);
  const current = el.value;
  const html = `<option value="">${label}</option>` + values.map((v) => `<option>${esc(v)}</option>`).join('');
  if (el.dataset.html === html) return;
  el.innerHTML = html;
  el.dataset.html = html;
  el.value = values.includes(current) ? current : '';
}

function fillFilters() {
  const cases = state.session.cases.filter((c) => !c.removed);
  fillSelect('#filter-module', 'Semua modul', [...new Set(cases.map((c) => c.module))]);
  fillSelect('#filter-type', 'Semua type', [...new Set(cases.map((c) => c.type).filter(Boolean))]);
}

function visibleCases() {
  const q = $('#search').value.trim().toLowerCase();
  const mod = $('#filter-module').value;
  const st = $('#filter-status').value;
  const type = $('#filter-type').value;
  return state.session.cases
    .filter((c) => !c.removed)
    .sort((a, b) => a.row - b.row)
    .filter((c) => (!mod || c.module === mod)
      && (!type || c.type === type)
      && (!st || (st === 'open' ? c.status === 'Todo' || (c.status !== 'Skip' && !c.images.length) : c.status === st))
      && (!q || `${c.tc_no} ${state.headings[c.tc_no]}`.toLowerCase().includes(q)));
}

function tcItem(c, heading, removed = false) {
  const cls = `tc ${c.tc_no === state.session.active ? 'active' : ''} ${removed ? 'removed' : ''}`;
  return `<button class="${cls}" ${removed ? 'disabled' : `data-tc="${esc(c.tc_no)}"`}>
    <span class="tc-head">${esc(heading)}</span>
    <span class="tc-meta"><small class="muted">${esc(c.tc_no)}</small>
      <span class="badge">📷 ${c.images.length}</span><span class="status ${c.status}">${c.status}</span></span>
  </button>`;
}

function renderList() {
  const groups = new Map();
  visibleCases().forEach((c) => {
    if (!groups.has(c.module)) groups.set(c.module, []);
    groups.get(c.module).push(c);
  });
  let html = '';
  for (const [mod, cases] of groups) {
    const done = cases.filter((c) => c.status !== 'Todo').length;
    html += `<details data-mod="${esc(mod)}" ${collapsed.has(mod) ? '' : 'open'}>
      <summary>${esc(mod)} <small class="muted">${done}/${cases.length}</small></summary>
      ${cases.map((c) => tcItem(c, state.headings[c.tc_no])).join('')}</details>`;
  }
  const removed = state.session.cases.filter((c) => c.removed);
  if (removed.length) {
    html += `<details data-mod="__removed"><summary>Dihapus dari Excel <small class="muted">${removed.length}</small></summary>
      ${removed.map((c) => tcItem(c, c.title, true)).join('')}</details>`;
  }
  $('#tc-list').innerHTML = html || '<p class="muted pad">Tidak ada TC yang cocok dengan filter.</p>';
  $('#tc-list .active')?.scrollIntoView({block: 'nearest'});
}

function renderDetail() {
  const c = activeCase();
  $('#detail-empty').hidden = !!c;
  $('#detail-body').hidden = !c;
  if (!c) { renderedActive = null; return; }
  $('#detail-head').innerHTML = `<div class="muted">${esc(c.module)} · ${esc(c.tc_no)}${c.type ? ' · ' + esc(c.type) : ''}</div>
    <h2>${esc(state.headings[c.tc_no] || c.title)}</h2>
    ${c.expected ? `<h4>Expected Result</h4><pre>${esc(c.expected)}</pre>` : ''}
    ${c.steps ? `<h4>Steps</h4><pre>${esc(c.steps)}</pre>` : ''}`;
  document.querySelectorAll('#status-buttons [data-status]').forEach((b) => b.classList.toggle('on', b.dataset.status === c.status));
  const remark = $('#remark');
  if (renderedActive !== c.tc_no || document.activeElement !== remark) remark.value = c.remark;
  renderedActive = c.tc_no;

  const targets = state.session.cases.filter((x) => !x.removed && x.tc_no !== c.tc_no).sort((a, b) => a.row - b.row);
  const options = '<option value="">Pindah ke…</option>'
    + targets.map((x) => `<option value="${esc(x.tc_no)}">${esc(x.module)} · ${esc(state.headings[x.tc_no])}</option>`).join('');
  $('#images').innerHTML = c.images.length ? c.images.map((name, i) => `<figure data-name="${esc(name)}">
      <img src="/shots/${encodeURIComponent(name)}" alt="${esc(name)}" data-zoom="1">
      <figcaption>
        <button class="btn sm" data-act="left" ${i === 0 ? 'disabled' : ''}>◀</button>
        <button class="btn sm" data-act="right" ${i === c.images.length - 1 ? 'disabled' : ''}>▶</button>
        <button class="btn sm danger" data-act="delete">Hapus</button>
        <select data-act="move">${options}</select>
      </figcaption></figure>`).join('')
    : '<p class="muted">Belum ada screenshot. Ambil screenshot di HP — gambar otomatis masuk ke TC ini.</p>';
}

async function setActive(tc) {
  await guard(async () => { await api('/api/active', {tc_no: tc}); await refresh(); });
}

async function setStatus(status) {
  const c = activeCase();
  if (!c) return;
  await guard(async () => { await api('/api/status', {tc_no: c.tc_no, status}); await refresh(); });
}

async function imageAction(path, body) {
  await guard(async () => { await api(path, body); await refresh(); });
}

$('#tc-list').addEventListener('click', (e) => {
  const b = e.target.closest('[data-tc]');
  if (b) setActive(b.dataset.tc);
});

$('#tc-list').addEventListener('toggle', (e) => {
  const mod = e.target.dataset?.mod;
  if (!mod) return;
  if (e.target.open) collapsed.delete(mod); else collapsed.add(mod);
}, true);

['#search', '#filter-module', '#filter-status', '#filter-type'].forEach((sel) => $(sel).addEventListener('input', () => state && renderList()));

$('#status-buttons').addEventListener('click', (e) => {
  const status = e.target.dataset.status;
  if (status) setStatus(status);
});

$('#remark').addEventListener('input', () => {
  clearTimeout(remarkTimer);
  const tc = state.session.active;
  const text = $('#remark').value;
  remarkTimer = setTimeout(() => guard(() => api('/api/remark', {tc_no: tc, remark: text})), 500);
});

$('#images').addEventListener('click', (e) => {
  const fig = e.target.closest('figure');
  if (!fig) return;
  if (e.target.dataset.zoom) {
    $('#lightbox img').src = e.target.src;
    $('#lightbox').hidden = false;
    return;
  }
  const c = activeCase();
  const name = fig.dataset.name;
  const act = e.target.dataset.act;
  if (act === 'delete') imageAction('/api/image/delete', {tc_no: c.tc_no, name});
  if (act === 'left' || act === 'right') {
    const names = [...c.images];
    const i = names.indexOf(name);
    const j = act === 'left' ? i - 1 : i + 1;
    [names[i], names[j]] = [names[j], names[i]];
    imageAction('/api/image/reorder', {tc_no: c.tc_no, names});
  }
});

$('#images').addEventListener('change', (e) => {
  if (e.target.dataset.act !== 'move' || !e.target.value) return;
  const name = e.target.closest('figure').dataset.name;
  imageAction('/api/image/move', {tc_no: activeCase().tc_no, name, to: e.target.value});
});

$('#lightbox').addEventListener('click', () => { $('#lightbox').hidden = true; });

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { $('#lightbox').hidden = true; return; }
  if ($('#work').hidden || view !== 'work' || $('#dialog').open || !state) return;
  if (e.target.closest?.('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
  const key = e.key;
  if (key === 'ArrowDown' || key === 'ArrowUp') {
    e.preventDefault();
    const list = visibleCases();
    if (!list.length) return;
    const i = list.findIndex((c) => c.tc_no === state.session.active);
    const next = i < 0 ? 0 : Math.min(Math.max(i + (key === 'ArrowDown' ? 1 : -1), 0), list.length - 1);
    setActive(list[next].tc_no);
  } else if (HOTKEY_STATUS[key.toLowerCase()]) {
    setStatus(HOTKEY_STATUS[key.toLowerCase()]);
  } else if (key === 'Delete' || key === 'Backspace') {
    const c = activeCase();
    if (c && c.images.length) imageAction('/api/image/delete', {tc_no: c.tc_no, name: c.images[c.images.length - 1]});
  }
});

/* ---------- preview ---------- */
function setView(v) {
  view = v;
  $('#work-view').hidden = v !== 'work';
  $('#preview-view').hidden = v !== 'preview';
  $('#tab-work').classList.toggle('active', v === 'work');
  $('#tab-preview').classList.toggle('active', v === 'preview');
  if (v === 'preview') renderPreview();
}

async function renderPreview() {
  await guard(async () => {
    const {layout, doc_type: docType} = await api('/api/preview');
    const images = (c) => `<div class="pv-images">${c.images.map((n) =>
      `<img src="/shots/${encodeURIComponent(n)}" style="width:${c.width_cm}cm" alt="">`).join('')}</div>`;
    const lines = (text) => (text || '').split('\n').filter((l) => l.trim()).map((l) => `<p>${esc(l)}</p>`).join('');
    const sitCase = (c) => `
      <table class="pv-sit">
        <tr><th>No. TC</th><td><span class="pv-link" data-tc="${esc(c.tc_no)}">${esc(c.label)}</span></td></tr>
        <tr><th>Scenario</th><td>${esc(c.title)}</td></tr>
        <tr><th>Category</th><td>${esc(c.type)}</td></tr>
        <tr><th>Steps</th><td>${lines(c.steps)}</td></tr>
        <tr><th>Expected Result</th><td>${lines(c.expected)}</td></tr>
        <tr><th>Screen Capture</th><td>${images(c)}</td></tr>
      </table>`;
    const body = docType === 'sit'
      ? layout.flatMap((m) => m.cases).map(sitCase).join('')
      : layout.map((m) => `<h1>${esc(m.module)}</h1>` + m.cases.map((c) => `
      <section class="pv-case">
        <h2 data-tc="${esc(c.tc_no)}">${esc(c.heading)}</h2>
        ${images(c)}
      </section>`).join('')).join('');
    const page = docType === 'sit' ? 'page sit' : 'page';
    $('#preview-view').innerHTML = `<div class="${page}">${body || '<p class="muted">Belum ada isi.</p>'}</div>`;
  });
}

$('#tab-work').addEventListener('click', () => setView('work'));
$('#tab-preview').addEventListener('click', () => setView('preview'));
$('#preview-view').addEventListener('click', (e) => {
  const h = e.target.closest('[data-tc]');
  if (h) { setView('work'); setActive(h.dataset.tc); }
});

/* ---------- header actions ---------- */
$('#back-btn').addEventListener('click', () => guard(async () => {
  await api('/api/sessions/close', {});
  await showHome();
}));

$('#generate-btn').addEventListener('click', () => guard(async () => {
  const w = await api('/api/warnings');
  const list = (title, items) => items.length
    ? `<h4>${title} (${items.length})</h4><ul class="scroll">${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  if (w.no_image.length || w.todo.length) {
    const ok = await dialog(`<h3>Generate DOCX?</h3>${list('TC belum ada screenshot', w.no_image)}${list('TC belum ditandai (Todo)', w.todo)}`, 'Tetap generate');
    if (!ok) return;
  }
  toast('Membuat DOCX…');
  const r = await api('/api/generate', {});
  if (await dialog(`<h3>DOCX selesai</h3><p>${esc(r.name)}</p>`, 'Buka folder')) await api('/api/open-folder', {});
}));

async function copyColumn(kind) {
  await guard(async () => {
    const r = await api('/api/results?kind=' + kind);
    if (!r.count) throw new Error('Tidak ada TC');
    await copyText(r.text);
    const s = state.session;
    const col = kind === 'result' ? (s.result_column || `Result ${s.platform}`) : 'Remark';
    await dialog(`<h3>${kind === 'result' ? 'Result' : 'Remark'} tersalin (${r.count} baris)</h3>
      <p>Klik sel kolom <b>${esc(col)}</b> baris <b>${r.first_row}</b> di Google Sheet, lalu tekan Ctrl+V (Mac: Cmd+V).</p>`, 'OK', false);
  });
}

$('#copy-result-btn').addEventListener('click', () => copyColumn('result'));
$('#copy-remark-btn').addEventListener('click', () => copyColumn('remark'));

$('#sync-file').addEventListener('change', (e) => guard(async () => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const up = await upload('/api/upload/xlsx', file);
  const d = await api('/api/sync/preview', {upload_id: up.upload_id});
  if (!d.added.length && !d.changed.length && !d.removed.length) { toast('Tidak ada perubahan'); return; }
  const li = (xs) => xs.map((x) => `<li>${esc(x)}</li>`).join('');
  const ok = await dialog(`<h3>Perubahan dari Excel</h3>
    ${d.added.length ? `<h4>TC baru (${d.added.length})</h4><ul class="scroll">${li(d.added)}</ul>` : ''}
    ${d.changed.length ? `<h4>Berubah (${d.changed.length})</h4><ul class="scroll">${li(d.changed.map((c) => `${c.tc_no}: ${c.fields.join(', ')}`))}</ul>` : ''}
    ${d.removed.length ? `<h4>Hilang dari Excel (${d.removed.length})</h4><ul class="scroll">${li(d.removed)}</ul>
      <p class="muted">Screenshot TC yang hilang tetap disimpan.</p>` : ''}`, 'Terapkan');
  if (!ok) return;
  await api('/api/sync/apply', {upload_id: up.upload_id});
  toast('Sinkron selesai', 'ok');
  await refresh();
}));

$('#export-btn').addEventListener('click', () => guard(async () => {
  const ok = await dialog(`<h3>Export session</h3>
    <p class="error">File .zip berisi screenshot yang bisa memuat data pribadi/sensitif. Kirim hanya lewat kanal yang aman.</p>`, 'Download .zip');
  if (ok) window.location.href = '/api/export';
}));

/* ---------- boot ---------- */
(async function boot() {
  try {
    const d = await api('/api/state?since=0');
    lastEvent = d.last_event;
    if (d.session) await enterWork(); else await showHome();
  } catch (e) {
    toast(e.message, 'error');
  }
})();
