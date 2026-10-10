/* South Bay Water Quality: map + sidebar logic. No framework; MapLibre GL for rendering. */
(() => {
'use strict';

const STYLES = { dark: 'https://tiles.openfreemap.org/styles/dark', light: 'https://tiles.openfreemap.org/styles/positron' };
const RAMP = [0, '#2dd4bf', 0.25, '#a3e635', 0.5, '#facc15', 0.75, '#fb923c', 1.0, '#d946ef', 2.0, '#7c3aed'];
const KIND_COLOR = { no_report: '#ef4444', not_in_report: '#71717a', nd: '#2dd4bf', unconverted: '#a1a1aa', none: '#000000' };
const PRIMARY_GROUPS = new Set(['inorganic', 'radiological', 'disinfection', 'lead_copper', 'microbial', 'organic', 'pfas']);
const DEFAULT_ENABLED = ['arsenic', 'nitrate_n', 'chromium_6', 'tthm', 'haa5', 'lead', 'copper', 'uranium', 'gross_alpha', 'pfoa', 'pfos'];
const KEEP_UPPER = new Set(['CWSC', 'MWC', 'INC', 'LLC', 'RV', 'USD', 'JUSD', 'SD', 'UCSC', 'NASA', 'CSP', 'SCC', 'HKN', 'WC', 'CO', 'EVG', 'EDV', 'COY', 'NSJ', 'CAL', 'AM']);

const S = {
  site: null, geo: null, enabled: new Set(), solo: null, source: 'ccr', showNonCommunity: true, showLabels: true,
  search: '', theme: 'dark', hoverId: null, selected: null, map: null, ready: false, byFid: new Map(),
};
window.__sbw = S; // test hook

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function titleCase(name) {
  if (!name) return '';
  return name.split(/\s+/).map((w) => {
    const bare = w.replace(/[^A-Z0-9'.-]/gi, '');
    if (KEEP_UPPER.has(bare.toUpperCase().replace(/\./g, ''))) return w.toUpperCase();
    if (/^[A-Z]{1,2}\d/.test(w)) return w;
    return w.toLowerCase().replace(/(^|[\s'(-])([a-z])/g, (m, p, c) => p + c.toUpperCase());
  }).join(' ').replace(/\bOf\b/g, 'of').replace(/\bDe\b/g, 'de').replace(/\bAnd\b/g, 'and');
}
function fmt(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1).replace(/\.0$/, '');
  if (a >= 1) return v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  if (a === 0) return '0';
  return Number(v.toPrecision(3)).toString();
}
function pct(r) { return r == null ? '—' : (r * 100).toFixed(r * 100 >= 100 ? 0 : 1).replace(/\.0$/, '') + '%'; }
function rampColor(r) {
  if (r == null) return KIND_COLOR.unconverted;
  for (let i = 0; i < RAMP.length - 2; i += 2) {
    const [a, ca, b, cb] = [RAMP[i], RAMP[i + 1], RAMP[i + 2], RAMP[i + 3]];
    if (r <= a) return ca;
    if (r <= b) return mix(ca, cb, (r - a) / (b - a));
  }
  return RAMP[RAMP.length - 1];
}
function mix(h1, h2, t) {
  const p = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const [a, b] = [p(h1), p(h2)];
  return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
function stdLabel(a) {
  if (!a) return '';
  if (a.threshold_type === 'RANGE') return `range ${a.threshold_low}–${a.threshold}`;
  if (a.threshold_type === 'NONE' || a.threshold == null) return 'no standard';
  return `${a.threshold_type} ${fmt(a.threshold)} ${a.unit}`;
}
function analyte(id) { return S.site.analytes.find((a) => a.id === id); }
function activeIds() { return S.solo ? [S.solo] : [...S.enabled]; }
function countFor(a) { return S.source === 'ccr' ? a.n_ccr : a.n_lab; }

/* ---------- scoring ---------- */
function scoreSystem(sys) {
  const ids = activeIds();
  if (S.source === 'ccr') {
    const st = sys.report.status;
    if (st !== 'found') return { kind: st === 'not_required' ? 'not_required' : 'no_report', items: [] };
    if (!ids.length) return { kind: 'none', items: [] };
    const items = ids.map((id) => sys.values[id] && { id, ...sys.values[id] }).filter(Boolean);
    if (!items.length) return { kind: 'not_in_report', items: [] };
    return finish(items, items.every((it) => it.status === 'nd'));
  }
  const lab = sys.lab;
  if (!lab || !Object.keys(lab).length) return { kind: 'no_report', items: [] };
  if (!ids.length) return { kind: 'none', items: [] };
  const items = ids.map((id) => lab[id] && { id, ...lab[id], value: lab[id].max ?? lab[id].latest }).filter(Boolean);
  if (!items.length) return { kind: 'not_in_report', items: [] };
  return finish(items, items.every((it) => !it.n_detected));
}
function finish(items, allNd) {
  let ratio = null;
  for (const it of items) if (it.ratio != null && (ratio == null || it.ratio > ratio)) ratio = it.ratio;
  items.sort((x, y) => (y.ratio ?? -1) - (x.ratio ?? -1));
  return { kind: allNd ? 'nd' : ratio == null ? 'unconverted' : 'value', ratio, items };
}
function visible(sys) { return S.showNonCommunity || sys.classification === 'COMMUNITY'; }

function recolor() {
  if (!S.ready) return;
  const counts = { value: 0, gray: 0, red: 0 };
  for (const f of S.geo.features) {
    const sys = S.site.systems[f.properties.pwsid];
    const sc = scoreSystem(sys);
    f.properties.kind = sc.kind;
    f.properties.ratio = sc.ratio == null ? -1 : sc.ratio;
    f.properties.visible = visible(sys);
    if (f.properties.visible) {
      if (sc.kind === 'value' || sc.kind === 'nd' || sc.kind === 'unconverted') counts.value++;
      else if (sc.kind === 'not_in_report') counts.gray++;
      else if (sc.kind === 'no_report' || sc.kind === 'not_required') counts.red++;
    }
  }
  S.map.getSource('systems').setData(S.geo);
  $('#stat-value').textContent = counts.value;
  $('#stat-gray').textContent = counts.gray;
  $('#stat-red').textContent = counts.red;
  S.counts = counts;
  renderMode();
  renderLegend();
  writeHash();
}

/* ---------- map ---------- */
function hatchImage() {
  const c = document.createElement('canvas'); c.width = c.height = 24;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(239,68,68,0.18)'; g.fillRect(0, 0, 24, 24);
  g.strokeStyle = 'rgba(239,68,68,0.9)'; g.lineWidth = 3;
  for (let i = -24; i < 48; i += 8) { g.beginPath(); g.moveTo(i, 24); g.lineTo(i + 24, 0); g.stroke(); }
  return g.getImageData(0, 0, 24, 24);
}
function addLayers() {
  const map = S.map;
  if (!map.hasImage('hatch')) map.addImage('hatch', hatchImage(), { pixelRatio: 2 });
  if (!map.getSource('systems')) map.addSource('systems', { type: 'geojson', data: S.geo, promoteId: 'fid' });
  const hover = ['boolean', ['feature-state', 'hover'], false];
  const selected = ['boolean', ['feature-state', 'selected'], false];
  const vis = ['==', ['get', 'visible'], true];
  const color = ['case',
    ['==', ['get', 'kind'], 'no_report'], KIND_COLOR.no_report,
    ['==', ['get', 'kind'], 'not_in_report'], KIND_COLOR.not_in_report,
    ['==', ['get', 'kind'], 'nd'], KIND_COLOR.nd,
    ['==', ['get', 'kind'], 'unconverted'], KIND_COLOR.unconverted,
    ['==', ['get', 'kind'], 'none'], KIND_COLOR.none,
    ['interpolate', ['linear'], ['max', ['get', 'ratio'], 0], ...RAMP]];
  const opacity = ['case', hover, 0.88, selected, 0.8,
    ['==', ['get', 'kind'], 'none'], 0.0,
    ['==', ['get', 'kind'], 'nd'], 0.45,
    ['==', ['get', 'kind'], 'not_in_report'], 0.42,
    ['==', ['get', 'kind'], 'no_report'], 0.5, 0.62];
  const firstSymbol = map.getStyle().layers.find((l) => l.type === 'symbol' && /label|place|poi/i.test(l.id))?.id;
  map.addLayer({ id: 'sys-fill', type: 'fill', source: 'systems', filter: ['all', vis, ['!=', ['get', 'kind'], 'not_required']],
    layout: { 'fill-sort-key': ['get', 'sortkey'] }, paint: { 'fill-color': color, 'fill-opacity': opacity } }, firstSymbol);
  map.addLayer({ id: 'sys-hatch', type: 'fill', source: 'systems', filter: ['all', vis, ['==', ['get', 'kind'], 'not_required']],
    layout: { 'fill-sort-key': ['get', 'sortkey'] }, paint: { 'fill-pattern': 'hatch', 'fill-opacity': ['case', hover, 1, 0.75] } }, firstSymbol);
  map.addLayer({ id: 'sys-line', type: 'line', source: 'systems', filter: vis, paint: {
    'line-color': ['case', selected, '#38bdf8', hover, '#ffffff', S.theme === 'dark' ? '#e5e7eb' : '#0f172a'],
    'line-width': ['case', selected, 2.5, hover, 2, 0.7],
    'line-opacity': ['case', selected, 1, hover, 0.95, S.theme === 'dark' ? 0.35 : 0.45] } });
  const labelPaint = { 'text-color': S.theme === 'dark' ? '#f1f5f9' : '#0f172a', 'text-halo-color': S.theme === 'dark' ? '#0b1020' : '#ffffff', 'text-halo-width': 1.4 };
  map.addLayer({ id: 'sys-label-big', type: 'symbol', source: 'systems', minzoom: 9, filter: ['all', vis, ['>=', ['get', 'population'], 5000]],
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': ['interpolate', ['linear'], ['zoom'], 9, 10, 12, 13], 'text-max-width': 9, 'symbol-placement': 'point', 'text-allow-overlap': false, visibility: S.showLabels ? 'visible' : 'none' }, paint: labelPaint });
  map.addLayer({ id: 'sys-label-small', type: 'symbol', source: 'systems', minzoom: 12.2, filter: ['all', vis, ['<', ['get', 'population'], 5000]],
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-max-width': 8, visibility: S.showLabels ? 'visible' : 'none' }, paint: { ...labelPaint, 'text-color': S.theme === 'dark' ? '#cbd5e1' : '#334155' } });
}
function ringArea(ring) { let a = 0; for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]; return Math.abs(a / 2); }
function geomArea(g) {
  if (g.type === 'Polygon') return ringArea(g.coordinates[0]);
  if (g.type === 'MultiPolygon') return g.coordinates.reduce((s, p) => s + ringArea(p[0]), 0);
  return 0;
}
function prepareGeo() {
  for (const f of S.geo.features) {
    const sys = S.site.systems[f.properties.pwsid];
    f.properties.area = geomArea(f.geometry);
    f.properties.sortkey = -f.properties.area; // small systems draw on top
    f.properties.population = sys.population || 0;
    f.properties.label = titleCase(sys.name);
    S.byFid.set(f.properties.fid, f);
  }
}
function topFeatureAt(point) {
  const feats = S.map.queryRenderedFeatures(point, { layers: ['sys-fill', 'sys-hatch'] });
  if (!feats.length) return null;
  feats.sort((a, b) => (a.properties.area || 0) - (b.properties.area || 0));
  return feats[0];
}
function setHover(fid) {
  if (S.hoverId === fid) return;
  if (S.hoverId != null) S.map.setFeatureState({ source: 'systems', id: S.hoverId }, { hover: false });
  S.hoverId = fid;
  if (fid != null) S.map.setFeatureState({ source: 'systems', id: fid }, { hover: true });
}
function setSelected(fid) {
  if (S.selected != null) S.map.setFeatureState({ source: 'systems', id: S.selected }, { selected: false });
  S.selected = fid;
  if (fid != null) S.map.setFeatureState({ source: 'systems', id: fid }, { selected: true });
}
function initMap() {
  const map = new maplibregl.Map({ container: 'map', style: STYLES[S.theme], center: [-121.86, 37.26], zoom: 9.6, minZoom: 7, maxZoom: 16, attributionControl: { compact: true }, hash: false });
  S.map = map;
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right');
  map.on('style.load', () => { addLayers(); S.ready = true; recolor(); $('#loading').classList.add('hidden'); });
  map.on('error', (e) => { if (e?.error?.status === 404) return; console.warn('map error', e?.error?.message || e); });
  const tip = $('#tooltip');
  map.on('mousemove', (e) => {
    const f = topFeatureAt(e.point);
    if (!f) { setHover(null); tip.classList.add('hidden'); map.getCanvas().style.cursor = ''; return; }
    map.getCanvas().style.cursor = 'pointer';
    setHover(f.id);
    renderTooltip(S.site.systems[f.properties.pwsid]);
    const rect = map.getContainer().getBoundingClientRect();
    const x = e.point.x + rect.left + 16, y = e.point.y + rect.top + 16;
    tip.style.left = Math.min(x, rect.right - tip.offsetWidth - 12) + 'px';
    tip.style.top = Math.min(y, window.innerHeight - tip.offsetHeight - 12) + 'px';
    tip.classList.remove('hidden');
  });
  map.on('mouseout', () => { setHover(null); tip.classList.add('hidden'); });
  map.on('click', (e) => {
    const f = topFeatureAt(e.point);
    if (!f) { closeDetail(); return; }
    openDetail(f.properties.pwsid, f.id);
  });
}
function setTheme(theme) {
  S.theme = theme;
  document.body.dataset.theme = theme;
  $('#btn-theme').textContent = theme === 'dark' ? 'Light basemap' : 'Dark basemap';
  try { localStorage.setItem('sbw-theme', theme); } catch (_) { /* private mode */ }
  if (S.map) { S.ready = false; S.map.setStyle(STYLES[theme]); }
}

/* ---------- tooltip / detail ---------- */
function statusText(sys, sc) {
  if (sc.kind === 'no_report') return S.source === 'ccr' ? '<div class="tt-status red">No consumer report found</div>' : '<div class="tt-status red">No state lab results on file</div>';
  if (sc.kind === 'not_required') return '<div class="tt-status amber">Non-community system: no consumer report required</div>';
  if (sc.kind === 'not_in_report') return `<div class="tt-status gray">${S.source === 'ccr' ? 'Report exists; none of the selected analytes are in it' : 'Lab results exist; none for the selected analytes'}</div>`;
  if (sc.kind === 'none') return '<div class="tt-status gray">No analyte selected</div>';
  if (sc.kind === 'nd') return '<div class="tt-status">Selected analytes: not detected</div>';
  return '';
}
function renderTooltip(sys) {
  const sc = scoreSystem(sys);
  const tip = $('#tooltip');
  const yr = sys.report?.data_year ? ` · ${sys.report.data_year} report` : '';
  let html = `<h4>${esc(titleCase(sys.name))}</h4><div class="sub">${esc(sys.pwsid)} · ${esc((sys.classification || '').toLowerCase())} · pop ${sys.population?.toLocaleString() ?? '—'}${yr}</div>`;
  html += statusText(sys, sc);
  for (const it of sc.items.slice(0, 7)) {
    const a = analyte(it.id);
    const detected = S.source === 'ccr' ? it.status !== 'nd' : !!it.n_detected;
    const val = detected ? `${fmt(it.value)} ${a.unit}` : 'ND';
    const col = detected ? rampColor(it.ratio) : KIND_COLOR.nd;
    html += `<div class="tt-row"><span>${esc(a.name)}</span><span class="num" style="color:${col}">${val}${detected && it.ratio != null && a.threshold_type !== 'NONE' ? ` · ${pct(it.ratio)}` : ''}</span></div>`;
  }
  if (sc.items.length > 7) html += `<div class="sub">+${sc.items.length - 7} more</div>`;
  html += '<div class="sub" style="margin-top:6px">Click for the full report</div>';
  tip.innerHTML = html;
}
function openDetail(pwsid, fid) {
  const sys = S.site.systems[pwsid];
  setSelected(fid);
  const d = $('#detail'); const b = $('#detail-body');
  const r = sys.report || {};
  const pill = `<span class="pill ${r.status}">${r.status === 'found' ? `Report found · ${r.data_year ?? ''}` : r.status === 'not_required' ? 'No report required' : 'No report found'}</span>`;
  let html = `<h2>${esc(titleCase(sys.name))}</h2><p class="sub">${esc(sys.pwsid)} · ${esc(titleCase(sys.classification || ''))} · ${esc(titleCase(sys.city || ''))} · pop ${sys.population?.toLocaleString() ?? '—'} · ${sys.connections?.toLocaleString() ?? '—'} connections</p>${pill}`;
  html += '<div class="card"><h3>Consumer Confidence Report</h3>';
  if (r.status === 'found') {
    html += `<div class="kv"><span>Title</span><span>${esc(r.title || '—')}</span><span>Data year</span><span>${esc(r.data_year ?? '—')}</span><span>Document</span><span>${r.file ? `<a href="/${esc(r.file)}" target="_blank" rel="noopener">open saved copy</a>` : '—'}${r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">source</a>` : ''}</span><span>Retrieved</span><span>${esc(r.retrieved || '—')}</span></div>`;
    if (r.sources_described) html += `<p><b>Water sources:</b> ${esc(r.sources_described)}</p>`;
    if (r.violations?.length) html += '<p><b>Violations / exceedances disclosed:</b></p>' + r.violations.map((v) => `<div class="violation">${esc(v)}</div>`).join('');
    if (r.extraction_notes) html += `<p class="mono">${esc(r.extraction_notes)}</p>`;
  } else if (r.status === 'not_required') {
    html += '<p>Non-community water systems (parks, schools, hospitals, workplaces) are not required to publish an annual Consumer Confidence Report. Their monitoring still appears under <b>State lab results</b>.</p>';
  } else {
    html += '<p>No published report could be located for this system.</p>';
    if (r.search_log?.length) html += '<ul class="search-log">' + r.search_log.map((s) => `<li>${esc(s)}</li>`).join('') + '</ul>';
  }
  html += '</div>';
  const hasLab = sys.lab && Object.keys(sys.lab).length;
  html += `<div class="tabs"><button class="tab ${S.source === 'ccr' ? 'active' : ''}" data-tab="ccr">Report values</button><button class="tab ${S.source === 'lab' ? 'active' : ''}" data-tab="lab" ${hasLab ? '' : 'disabled title="no lab results on file"'}>State lab results${hasLab ? ` (${Object.keys(sys.lab).length})` : ''}</button></div>`;
  html += `<div id="tab-ccr" class="${S.source === 'ccr' ? '' : 'hidden'}">${renderCcrTable(sys)}</div><div id="tab-lab" class="${S.source === 'lab' ? '' : 'hidden'}">${renderLabTable(sys)}</div>`;
  b.innerHTML = html;
  b.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => {
    if (t.disabled) return;
    b.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    $('#tab-ccr').classList.toggle('hidden', t.dataset.tab !== 'ccr');
    $('#tab-lab').classList.toggle('hidden', t.dataset.tab !== 'lab');
  }));
  d.classList.remove('hidden');
}
function groupRows(rowsById) {
  const groups = {};
  for (const a of S.site.analytes) if (rowsById[a.id]) (groups[a.group] ||= []).push([a, rowsById[a.id]]);
  return groups;
}
function renderCcrTable(sys) {
  if (sys.report?.status !== 'found') return '';
  const ids = Object.keys(sys.values);
  if (!ids.length) return '<p class="sub">The report has no data table rows we could read.</p>';
  let html = '';
  const groups = groupRows(sys.values);
  for (const [g, rows] of Object.entries(groups)) {
    html += `<div class="card"><h3>${esc(S.site.groups[g] || g)}</h3><table class="vals"><thead><tr><th>Analyte</th><th>Level</th><th>Range (as printed)</th><th>Std · PHG</th><th>p.</th></tr></thead><tbody>`;
    for (const [a, v] of rows) {
      const active = S.enabled.has(a.id) || S.solo === a.id;
      for (const e of v.entries) {
        const det = e.detected !== false && e.value != null;
        const conv = e.value_converted;
        const ratio = det && conv != null ? (a.threshold_type === 'NONE' ? null : (a.threshold_type === 'RANGE' ? v.ratio : conv / a.threshold)) : null;
        const lvl = det ? `${fmt(e.value)} ${esc(e.unit_raw || '')}` : 'ND';
        const bar = det && ratio != null ? `<div class="bar"><i style="width:${Math.min(100, ratio * 100)}%;background:${rampColor(ratio)}"></i></div>` : '';
        const rng = e.range_low != null || e.range_high != null ? `${e.range_low == null ? 'ND' : fmt(e.range_low)} – ${fmt(e.range_high)}` : (e.average != null && e.value_basis !== 'average' ? `avg ${fmt(e.average)}` : '—');
        html += `<tr${active ? ' style="background:#38bdf80d"' : ''}><td><b>${esc(a.name)}</b>${e.source_group ? `<div class="sub" style="margin:0">${esc(e.source_group)}</div>` : ''}${e.notes ? `<div class="sub" style="margin:0">${esc(e.notes)}</div>` : ''}</td><td class="num">${lvl}${bar}</td><td class="num">${rng}</td><td class="num">${esc(e.mcl_printed ?? '—')}${e.phg_printed ? ` · ${esc(e.phg_printed)}` : ''}</td><td class="num">${e.page ?? ''}</td></tr>`;
      }
    }
    html += '</tbody></table></div>';
  }
  if (sys.other_rows?.length) {
    html += `<div class="card"><h3>Other rows in the report (${sys.other_rows.length})</h3><table class="vals"><tbody>`;
    for (const e of sys.other_rows) html += `<tr><td>${esc(e.raw_name)}${e.source_group ? `<div class="sub" style="margin:0">${esc(e.source_group)}</div>` : ''}</td><td class="num">${e.detected === false || e.value == null ? 'ND' : fmt(e.value) + ' ' + esc(e.unit_raw || '')}</td><td class="num">${esc(e.mcl_printed ?? '')}</td><td class="num">${e.page ?? ''}</td></tr>`;
    html += '</tbody></table></div>';
  }
  return html;
}
function renderLabTable(sys) {
  if (!sys.lab || !Object.keys(sys.lab).length) return '<p class="sub">No laboratory results for this system in the state database (2023 to present).</p>';
  let html = `<p class="sub">${sys.lab_meta?.n_rows?.toLocaleString() ?? ''} results on file · latest sample ${esc(sys.lab_meta?.latest_sample || '—')}. Max and latest values across all sampling points, including raw sources.</p>`;
  const groups = groupRows(sys.lab);
  for (const [g, rows] of Object.entries(groups)) {
    html += `<div class="card"><h3>${esc(S.site.groups[g] || g)}</h3><table class="vals"><thead><tr><th>Analyte</th><th>Max</th><th>Latest</th><th>Samples</th></tr></thead><tbody>`;
    for (const [a, v] of rows) {
      const det = !!v.n_detected;
      const bar = det && v.ratio != null && a.threshold_type !== 'NONE' ? `<div class="bar"><i style="width:${Math.min(100, v.ratio * 100)}%;background:${rampColor(v.ratio)}"></i></div>` : '';
      html += `<tr><td><b>${esc(a.name)}</b><div class="sub" style="margin:0">${esc(stdLabel(a))}</div></td><td class="num">${det ? `${fmt(v.max)} ${a.unit}` : 'ND'}${bar}</td><td class="num">${v.latest != null ? `${fmt(v.latest)} ${a.unit}` : 'ND'}<div class="sub" style="margin:0">${esc(v.latest_date || '')}</div></td><td class="num">${v.n_detected ?? 0}/${v.n ?? 0}${v.sampling_points ? `<div class="sub" style="margin:0">${v.sampling_points} pts</div>` : ''}</td></tr>`;
    }
    html += '</tbody></table></div>';
  }
  return html;
}
function closeDetail() { $('#detail').classList.add('hidden'); setSelected(null); }

/* ---------- sidebar ---------- */
function renderSidebar() {
  const root = $('#analytes');
  root.innerHTML = '';
  const q = S.search.trim().toLowerCase();
  const groups = {};
  for (const a of S.site.analytes) {
    if (q && !(a.name.toLowerCase().includes(q) || a.id.includes(q) || (S.site.groups[a.group] || '').toLowerCase().includes(q))) continue;
    (groups[a.group] ||= []).push(a);
  }
  for (const [g, list] of Object.entries(groups)) {
    const withData = list.filter((a) => countFor(a) > 0).length;
    const wrap = el('div', 'group' + (withData === 0 && !q ? ' collapsed' : ''));
    wrap.dataset.group = g;
    const head = el('div', 'group-head', `<span><span class="caret">▾</span>${esc(S.site.groups[g] || g)}</span><span class="count">${withData}/${list.length} with data</span>`);
    head.addEventListener('click', () => wrap.classList.toggle('collapsed'));
    wrap.appendChild(head);
    const rows = el('div', 'rows');
    for (const a of list) {
      const n = countFor(a);
      const row = el('div', 'row' + (n === 0 ? ' nodata' : '') + (S.solo === a.id ? ' solo' : ''));
      row.dataset.id = a.id; row.setAttribute('role', 'listitem');
      row.innerHTML = `<label class="switch-row" title="Toggle ${esc(a.name)} in the composite"><input type="checkbox" data-id="${a.id}" ${S.enabled.has(a.id) ? 'checked' : ''} aria-label="Enable ${esc(a.name)}"><span class="switch"></span></label>
        <div class="name" title="Click to solo ${esc(a.name)}"><span>${esc(a.name)}</span><span class="meta">${esc(a.unit)} · ${esc(stdLabel(a))}${a.phg != null ? ` · PHG ${fmt(a.phg)}` : ''}</span></div>
        <span class="badge${n ? '' : ' nodata'}" title="systems with this analyte in the current source">${n}</span>`;
      row.querySelector('input').addEventListener('change', (ev) => { ev.target.checked ? S.enabled.add(a.id) : S.enabled.delete(a.id); if (S.solo && S.solo !== a.id) { /* keep solo */ } recolor(); });
      row.querySelector('.name').addEventListener('click', () => { S.solo = S.solo === a.id ? null : a.id; renderSidebar(); recolor(); });
      rows.appendChild(row);
    }
    wrap.appendChild(rows);
    root.appendChild(wrap);
  }
  const banner = $('#solo-banner');
  banner.classList.toggle('hidden', !S.solo);
  if (S.solo) $('#solo-name').textContent = analyte(S.solo)?.name || S.solo;
  const labOk = (S.site.counts?.lab_systems || 0) > 0;
  const labBtn = document.querySelector('.seg-btn[data-source="lab"]');
  labBtn.disabled = !labOk; labBtn.title = labOk ? labBtn.title : 'State lab layer not built yet';
}
function renderMode() {
  const chip = $('#mode-chip');
  const src = S.source === 'ccr' ? 'Consumer reports' : 'State lab results';
  if (S.solo) { const a = analyte(S.solo); chip.innerHTML = `${esc(src)} · <b>${esc(a.name)}</b> · ${esc(stdLabel(a))}`; return; }
  const n = S.enabled.size;
  chip.innerHTML = n ? `${esc(src)} · <b>composite of ${n} analyte${n === 1 ? '' : 's'}</b> · worst fraction of standard` : `${esc(src)} · <b>no analyte selected</b>`;
}
function renderLegend() {
  const t = $('#legend-title'), note = $('#legend-note');
  const a = S.solo ? analyte(S.solo) : null;
  if (a && a.threshold_type === 'NONE') { t.textContent = `${a.name}: relative to the highest system (no standard)`; note.textContent = 'No California standard exists for this analyte; 100% = the highest value on the map.'; }
  else if (a && a.threshold_type === 'RANGE') { t.textContent = `${a.name}: distance outside ${a.threshold_low}–${a.threshold}`; note.textContent = 'Inside the recommended range reads 0; 100% = one full range-width outside it.'; }
  else if (a) { t.textContent = `${a.name} as % of ${a.threshold_type} (${fmt(a.threshold)} ${a.unit})`; note.textContent = a.notes || ''; }
  else { t.textContent = 'Worst fraction of standard among enabled analytes'; note.textContent = S.source === 'lab' ? 'Lab values are the maximum across all sampling points, including untreated sources.' : ''; }
}

/* ---------- state persistence ---------- */
function writeHash() {
  const p = new URLSearchParams();
  p.set('src', S.source);
  if (S.enabled.size) p.set('a', [...S.enabled].join(','));
  if (S.solo) p.set('solo', S.solo);
  if (!S.showNonCommunity) p.set('nc', '0');
  history.replaceState(null, '', '#' + p.toString());
}
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('src') === 'lab') S.source = 'lab';
  if (p.has('a')) { S.enabled = new Set(p.get('a').split(',').filter((id) => analyte(id))); }
  if (p.get('solo') && analyte(p.get('solo'))) S.solo = p.get('solo');
  if (p.get('nc') === '0') S.showNonCommunity = false;
}
function setSource(src) {
  S.source = src;
  document.querySelectorAll('.seg-btn').forEach((b) => { const on = b.dataset.source === src; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); });
  $('#stats .stat:nth-child(2) span:last-child').textContent = src === 'ccr' ? 'not in report' : 'not sampled';
  $('#stats .stat:nth-child(3) span:last-child').textContent = src === 'ccr' ? 'no report' : 'no lab data';
  renderSidebar(); recolor();
}

/* ---------- boot ---------- */
async function boot() {
  try { const t = localStorage.getItem('sbw-theme'); if (t === 'light' || t === 'dark') S.theme = t; } catch (_) { /* ignore */ }
  document.body.dataset.theme = S.theme;
  $('#btn-theme').textContent = S.theme === 'dark' ? 'Light basemap' : 'Dark basemap';
  let site, geo;
  try {
    [site, geo] = await Promise.all([fetch('/api/site').then((r) => { if (!r.ok) throw new Error(`site ${r.status}`); return r.json(); }),
      fetch('/api/boundaries').then((r) => { if (!r.ok) throw new Error(`boundaries ${r.status}`); return r.json(); })]);
  } catch (err) {
    $('#loading').innerHTML = `<div class="err">Could not load data: ${esc(err.message)}. Run the pipeline and reload.</div>`;
    return;
  }
  S.site = site; S.geo = geo;
  S.enabled = new Set(DEFAULT_ENABLED.filter((id) => analyte(id)));
  readHash();
  prepareGeo();
  const c = site.counts;
  $('#subtitle').textContent = `${site.region} · ${c.systems} systems · ${c.reports_found} reports found · generated ${site.generated.slice(0, 10)}`;
  $('#about-meta').textContent = `Built ${site.generated}. Boundaries: ${c.systems} systems (${c.community} community). Reports found: ${c.reports_found}; not found: ${c.reports_not_found}; not required: ${c.reports_not_required}. Lab layer: ${c.lab_systems} systems.`;
  setSource(S.source);
  initMap();
  // controls
  document.querySelectorAll('.seg-btn').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) setSource(b.dataset.source); }));
  $('#search').addEventListener('input', (e) => { S.search = e.target.value; renderSidebar(); });
  $('#btn-primary').addEventListener('click', () => { S.enabled = new Set(S.site.analytes.filter((a) => PRIMARY_GROUPS.has(a.group) && countFor(a) > 0).map((a) => a.id)); S.solo = null; renderSidebar(); recolor(); });
  $('#btn-all').addEventListener('click', () => { S.enabled = new Set(S.site.analytes.filter((a) => countFor(a) > 0).map((a) => a.id)); S.solo = null; renderSidebar(); recolor(); });
  $('#btn-none').addEventListener('click', () => { S.enabled = new Set(); S.solo = null; renderSidebar(); recolor(); });
  $('#solo-exit').addEventListener('click', () => { S.solo = null; renderSidebar(); recolor(); });
  $('#toggle-noncommunity').checked = S.showNonCommunity;
  $('#toggle-noncommunity').addEventListener('change', (e) => { S.showNonCommunity = e.target.checked; recolor(); });
  $('#toggle-labels').addEventListener('change', (e) => { S.showLabels = e.target.checked; for (const id of ['sys-label-big', 'sys-label-small']) if (S.map.getLayer(id)) S.map.setLayoutProperty(id, 'visibility', S.showLabels ? 'visible' : 'none'); });
  $('#btn-theme').addEventListener('click', () => setTheme(S.theme === 'dark' ? 'light' : 'dark'));
  $('#detail-close').addEventListener('click', closeDetail);
  $('#about-link').addEventListener('click', (e) => { e.preventDefault(); $('#about').classList.remove('hidden'); });
  $('#about .modal-close').addEventListener('click', () => $('#about').classList.add('hidden'));
  $('#about').addEventListener('click', (e) => { if (e.target.id === 'about') $('#about').classList.add('hidden'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeDetail(); $('#about').classList.add('hidden'); } });
}
boot();
})();
