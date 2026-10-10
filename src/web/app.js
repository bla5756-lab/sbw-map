/* South Bay Water Quality: map + sidebar logic. No framework; MapLibre GL for rendering. */
(() => {
'use strict';

const STYLES = { dark: 'https://tiles.openfreemap.org/styles/dark', light: 'https://tiles.openfreemap.org/styles/positron' };
const RAMP = [0, '#2dd4bf', 0.25, '#a3e635', 0.5, '#facc15', 0.75, '#fb923c', 1.0, '#d946ef', 2.0, '#7c3aed'];
const KIND_COLOR = { no_report: '#ef4444', not_in_report: '#71717a', nd: '#2dd4bf', unconverted: '#a1a1aa', none: '#000000' };
const PRIMARY_GROUPS = new Set(['inorganic', 'radiological', 'disinfection', 'lead_copper', 'microbial', 'organic', 'pfas']);
const DEFAULT_ENABLED = ['arsenic', 'nitrate_n', 'chromium_6', 'tthm', 'haa5', 'lead', 'copper', 'uranium', 'gross_alpha', 'pfoa', 'pfos'];
const KEEP_UPPER = new Set(['CWSC', 'MWC', 'INC', 'LLC', 'RV', 'USD', 'JUSD', 'SD', 'UCSC', 'NASA', 'CSP', 'SCC', 'HKN', 'WC', 'CO', 'EVG', 'EDV', 'COY', 'NSJ', 'CAL', 'AM']);

const GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf';
const offlineStyle = (theme) => ({ version: 8, name: 'offline', glyphs: GLYPHS, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': theme === 'dark' ? '#0b1020' : '#eef2f7' } }] });
const SVG_NS = 'http://www.w3.org/2000/svg';
function webglAvailable() {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')); } catch (_) { return false; }
}
function notice(msg, sticky) {
  const n = $('#notice'); if (!n) return;
  n.innerHTML = `<span>${msg}</span><button class="link" id="notice-close">dismiss</button>`;
  n.classList.remove('hidden');
  $('#notice-close').addEventListener('click', () => n.classList.add('hidden'));
  if (!sticky) setTimeout(() => n.classList.add('hidden'), 12000);
}
function fatal(msg) {
  const l = $('#loading');
  if (l && !l.classList.contains('hidden')) l.innerHTML = `<div class="err"><b>Something broke while starting the map.</b><br>${esc(msg)}<br><br>Reload the page; if it persists, send this message along.</div>`;
  else notice(`Error: ${esc(msg)}`, true);
}

const S = {
  site: null, geo: null, enabled: new Set(), solo: null, source: 'ccr', showNonCommunity: true, showLabels: true,
  search: '', theme: 'dark', hoverId: null, selected: null, map: null, ready: false, byFid: new Map(),
};
window.__sbw = S; // test hook (S.api is attached in boot)

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const DISPLAY_NAMES = {
  CA4310011: 'San Jose Water Company', CA4310018: 'Cupertino (operated by San Jose Water)', CA4310020: 'San Jose Municipal Water: Evergreen / Edenvale / Coyote',
  CA4310019: 'San Jose Municipal Water: North San Jose / Alviso', CA4310001: 'Cal Water: Los Altos District', CA4310022: 'Great Oaks Water Company',
  CA4300543: 'Cal Am: West San Martin', CA4300909: 'Lick Observatory (UC Santa Cruz)', CA4310021: 'Purissima Hills Water District', CA4300997: 'NASA Ames Research Center',
};
function displayName(sys) { return DISPLAY_NAMES[sys.pwsid] || titleCase(sys.name); }
function titleCase(name) {
  if (!name) return '';
  if (name.includes('/')) return name.split('/').map((part) => titleCase(part.trim())).join(' / ');
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
  const items = ids.map((id) => lab[id] && { id, ...lab[id], value: lab[id].basis_value ?? lab[id].max ?? lab[id].latest }).filter(Boolean);
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
  const counts = { value: 0, gray: 0, red: 0, notRequired: 0 };
  for (const f of S.geo.features) {
    const sys = S.site.systems[f.properties.pwsid];
    const sc = scoreSystem(sys);
    f.properties.kind = sc.kind;
    f.properties.ratio = sc.ratio == null ? -1 : sc.ratio;
    f.properties.visible = visible(sys);
    if (f.properties.visible) {
      if (sc.kind === 'value' || sc.kind === 'nd' || sc.kind === 'unconverted') counts.value++;
      else if (sc.kind === 'not_in_report') counts.gray++;
      else if (sc.kind === 'no_report') counts.red++;
      else if (sc.kind === 'not_required') counts.notRequired++;
    }
  }
  if (S.svg) svgRecolor(); else S.map.getSource('systems').setData(S.geo);
  syncLabels();
  $('#stat-value').textContent = counts.value;
  $('#stat-gray').textContent = counts.gray;
  $('#stat-red').textContent = counts.red;
  $('#stat-nr').textContent = counts.notRequired ? `+${counts.notRequired} not required` : '';
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
  if (!map.getSource('labels')) map.addSource('labels', { type: 'geojson', data: S.labels, promoteId: 'fid' });
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
  if (S.offline) return; // no glyph server reachable: skip the label layers
  const labelPaint = { 'text-color': S.theme === 'dark' ? '#f1f5f9' : '#0f172a', 'text-halo-color': S.theme === 'dark' ? '#0b1020' : '#ffffff', 'text-halo-width': 1.4 };
  map.addLayer({ id: 'sys-label-big', type: 'symbol', source: 'labels', minzoom: 9, filter: ['all', vis, ['>=', ['get', 'population'], 5000]],
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': ['interpolate', ['linear'], ['zoom'], 9, 10, 12, 13], 'text-max-width': 9, 'text-allow-overlap': false, 'text-variable-anchor': ['center', 'top', 'bottom'], 'text-radial-offset': 0.3, visibility: S.showLabels ? 'visible' : 'none' }, paint: labelPaint });
  map.addLayer({ id: 'sys-label-small', type: 'symbol', source: 'labels', minzoom: 12.2, filter: ['all', vis, ['<', ['get', 'population'], 5000]],
    layout: { 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Regular'], 'text-size': 11, 'text-max-width': 8, visibility: S.showLabels ? 'visible' : 'none' }, paint: { ...labelPaint, 'text-color': S.theme === 'dark' ? '#cbd5e1' : '#334155' } });
}
function ringArea(ring) { let a = 0; for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]; return Math.abs(a / 2); }
function geomArea(g) {
  if (g.type === 'Polygon') return ringArea(g.coordinates[0]);
  if (g.type === 'MultiPolygon') return g.coordinates.reduce((s, p) => s + ringArea(p[0]), 0);
  return 0;
}
function ringCentroid(ring) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const f = ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    a += f; cx += (ring[i][0] + ring[i + 1][0]) * f; cy += (ring[i][1] + ring[i + 1][1]) * f;
  }
  if (!a) return ring[0];
  return [cx / (3 * a), cy / (3 * a)];
}
function labelPoint(g) {
  const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
  let best = polys[0], bestA = -1;
  for (const p of polys) { const ar = ringArea(p[0]); if (ar > bestA) { bestA = ar; best = p; } }
  return ringCentroid(best[0]);
}
function syncLabels() {
  if (!S.labels || !S.map) return;
  for (const f of S.labels.features) { const src = S.byFid.get(f.properties.fid); f.properties.visible = src.properties.visible; }
  S.map.getSource('labels')?.setData(S.labels);
}
function prepareGeo() {
  S.labels = { type: 'FeatureCollection', features: [] };
  for (const f of S.geo.features) {
    const sys = S.site.systems[f.properties.pwsid];
    f.properties.area = geomArea(f.geometry);
    f.properties.sortkey = -f.properties.area; // small systems draw on top
    f.properties.population = sys.population || 0;
    f.properties.label = displayName(sys);
    S.byFid.set(f.properties.fid, f);
    S.labels.features.push({ type: 'Feature', id: f.properties.fid, properties: { fid: f.properties.fid, label: f.properties.label, population: f.properties.population, visible: true }, geometry: { type: 'Point', coordinates: labelPoint(f.geometry) } });
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
  if (S.svg) { S.svg.paths.get(S.hoverId)?.classList.remove('hover'); S.hoverId = fid; S.svg.paths.get(fid)?.classList.add('hover'); return; }
  if (S.hoverId != null) S.map.setFeatureState({ source: 'systems', id: S.hoverId }, { hover: false });
  S.hoverId = fid;
  if (fid != null) S.map.setFeatureState({ source: 'systems', id: fid }, { hover: true });
}
function setSelected(fid) {
  if (S.svg) { S.svg.paths.get(S.selected)?.classList.remove('selected'); S.selected = fid; S.svg.paths.get(fid)?.classList.add('selected'); return; }
  if (S.selected != null) S.map.setFeatureState({ source: 'systems', id: S.selected }, { selected: false });
  S.selected = fid;
  if (fid != null) S.map.setFeatureState({ source: 'systems', id: fid }, { selected: true });
}
async function styleFor(theme) {
  try {
    const r = await fetch(STYLES[theme], { signal: AbortSignal.timeout(7000) });
    if (!r.ok) throw new Error(`style ${r.status}`);
    return STYLES[theme];
  } catch (err) {
    notice('Basemap tiles are unreachable from this browser; showing system boundaries on a plain background.', true);
    S.offline = true;
    return offlineStyle(theme);
  }
}
async function initMap() {
  const style = await styleFor(S.theme);
  const map = new maplibregl.Map({ container: 'map', style, center: [-121.86, 37.26], zoom: 9.6, minZoom: 7, maxZoom: 16, attributionControl: { compact: true }, hash: false });
  S.map = map;
  setTimeout(() => { if (!S.ready) { notice('The basemap did not finish loading; switching to a plain background.', true); S.offline = true; map.setStyle(offlineStyle(S.theme), { diff: false }); } }, 12000);
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
  if (S.svg) { svgRecolor(); return; }
  if (S.map) { S.ready = false; styleFor(theme).then((st) => S.map.setStyle(st, { diff: false })); }
}

/* ---------- SVG fallback renderer (no WebGL) ---------- */
function mercator(lng, lat) {
  const s = Math.sin((lat * Math.PI) / 180);
  return [(lng + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}
function initSvgMap() {
  const box = $('#map'); box.classList.add('svg-mode'); box.innerHTML = '';
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('width', '100%'); svg.setAttribute('height', '100%');
  svg.innerHTML = '<defs><pattern id="hatch" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)"><rect width="8" height="8" fill="rgba(239,68,68,0.18)"/><line x1="0" y1="0" x2="0" y2="8" stroke="#ef4444" stroke-width="3"/></pattern></defs>';
  const root = document.createElementNS(SVG_NS, 'g'); root.id = 'svg-root';
  const gPaths = document.createElementNS(SVG_NS, 'g'); const gLabels = document.createElementNS(SVG_NS, 'g');
  root.appendChild(gPaths); root.appendChild(gLabels); svg.appendChild(root); box.appendChild(svg);
  let minx = 1, miny = 1, maxx = 0, maxy = 0;
  const rings = (g) => (g.type === 'Polygon' ? g.coordinates : g.coordinates.flat(1));
  for (const f of S.geo.features) for (const ring of rings(f.geometry)) for (const [lng, lat] of ring) { const [x, y] = mercator(lng, lat); minx = Math.min(minx, x); miny = Math.min(miny, y); maxx = Math.max(maxx, x); maxy = Math.max(maxy, y); }
  const W = box.clientWidth || 1000, H = box.clientHeight || 800, pad = 40;
  const k0 = Math.min((W - 2 * pad) / (maxx - minx), (H - 2 * pad) / (maxy - miny));
  const ox = (W - (maxx - minx) * k0) / 2, oy = (H - (maxy - miny) * k0) / 2;
  const px = (lng, lat) => { const [x, y] = mercator(lng, lat); return [(x - minx) * k0 + ox, (y - miny) * k0 + oy]; };
  const st = { svg, root, gLabels, k: 1, tx: 0, ty: 0, paths: new Map(), labels: new Map(), W, H };
  S.svg = st;
  const feats = [...S.geo.features].sort((a, b) => (b.properties.area || 0) - (a.properties.area || 0)); // big first, small on top
  for (const f of feats) {
    const d = rings(f.geometry).map((ring) => ring.map(([lng, lat], i) => { const [x, y] = px(lng, lat); return `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`; }).join('') + 'Z').join('');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d); path.classList.add('sys'); path.dataset.fid = f.properties.fid;
    path.addEventListener('mousemove', (e) => { setHover(f.properties.fid); renderTooltip(S.site.systems[f.properties.pwsid]); const tip = $('#tooltip'); tip.classList.remove('hidden'); tip.style.left = Math.min(e.clientX + 16, window.innerWidth - tip.offsetWidth - 12) + 'px'; tip.style.top = Math.min(e.clientY + 16, window.innerHeight - tip.offsetHeight - 12) + 'px'; });
    path.addEventListener('mouseleave', () => { setHover(null); $('#tooltip').classList.add('hidden'); });
    path.addEventListener('click', (e) => { e.stopPropagation(); openDetail(f.properties.pwsid, f.properties.fid); });
    gPaths.appendChild(path); st.paths.set(f.properties.fid, path);
    const ring = rings(f.geometry).sort((a, b) => b.length - a.length)[0];
    let cx = 0, cy = 0; for (const [lng, lat] of ring) { const [x, y] = px(lng, lat); cx += x; cy += y; }
    const text = document.createElementNS(SVG_NS, 'text');
    text.setAttribute('x', (cx / ring.length).toFixed(1)); text.setAttribute('y', (cy / ring.length).toFixed(1));
    text.classList.add('lbl'); text.dataset.pop = f.properties.population; text.textContent = f.properties.label;
    gLabels.appendChild(text); st.labels.set(f.properties.fid, text);
  }
  const apply = () => {
    root.setAttribute('transform', `translate(${st.tx} ${st.ty}) scale(${st.k})`);
    for (const [fid, text] of st.labels) {
      const f = S.byFid.get(fid);
      const show = S.showLabels && f.properties.visible !== false && (st.k >= 4 || f.properties.population >= 5000);
      text.style.display = show ? '' : 'none';
      text.setAttribute('font-size', (12 / st.k).toFixed(2));
      text.setAttribute('stroke-width', (3 / st.k).toFixed(2));
    }
  };
  st.apply = apply;
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect(); const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const k = Math.min(60, Math.max(0.6, st.k * Math.exp(-e.deltaY * 0.0016)));
    st.tx = mx - (mx - st.tx) * (k / st.k); st.ty = my - (my - st.ty) * (k / st.k); st.k = k; apply();
  }, { passive: false });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, tx: st.tx, ty: st.ty, moved: false }; svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', (e) => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; st.tx = drag.tx + dx; st.ty = drag.ty + dy; apply(); });
  svg.addEventListener('pointerup', () => { drag = null; });
  svg.addEventListener('click', (e) => { if (e.target === svg || e.target.tagName === 'text') closeDetail(); });
  apply();
  S.ready = true;
  recolor();
  $('#loading').classList.add('hidden');
  notice('This browser has no WebGL, so the street basemap is off; system boundaries, colors, hover and detail all work. Scroll to zoom, drag to pan.', true);
}
function svgRecolor() {
  const st = S.svg; if (!st) return;
  for (const f of S.geo.features) {
    const p = st.paths.get(f.properties.fid); if (!p) continue;
    const kind = f.properties.kind, ratio = f.properties.ratio;
    p.style.display = f.properties.visible ? '' : 'none';
    if (kind === 'not_required') { p.setAttribute('fill', 'url(#hatch)'); p.setAttribute('fill-opacity', '0.9'); }
    else if (kind === 'none') { p.setAttribute('fill', '#000'); p.setAttribute('fill-opacity', '0'); }
    else { p.setAttribute('fill', kind === 'value' ? rampColor(Math.max(0, ratio)) : KIND_COLOR[kind]); p.setAttribute('fill-opacity', kind === 'nd' ? '0.45' : kind === 'not_in_report' ? '0.42' : kind === 'no_report' ? '0.5' : '0.62'); }
  }
  st.apply();
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
  let html = `<h4>${esc(displayName(sys))}</h4><div class="sub">${esc(sys.pwsid)} · ${esc((sys.classification || '').toLowerCase())} · pop ${sys.population?.toLocaleString() ?? '—'}${yr}</div>`;
  html += statusText(sys, sc);
  for (const it of sc.items.slice(0, 7)) {
    const a = analyte(it.id);
    const detected = S.source === 'ccr' ? it.status !== 'nd' : !!it.n_detected;
    const val = detected ? `${fmt(it.value)} ${a.unit}` : 'ND';
    const col = detected ? rampColor(it.ratio) : KIND_COLOR.nd;
    const hint = S.source === 'ccr' && detected && it.basis_tier === 2 ? ' (worst single result)' : '';
    html += `<div class="tt-row"><span>${esc(a.name)}<span class="sub" style="display:inline;margin:0">${hint}</span></span><span class="num" style="color:${col}">${val}${detected && it.ratio != null && a.threshold_type !== 'NONE' ? ` · ${pct(it.ratio)}` : ''}</span></div>`;
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
  let html = `<h2>${esc(displayName(sys))}</h2><p class="sub">${esc(sys.pwsid)} · ${esc(titleCase(sys.classification || ''))} · ${esc(titleCase(sys.city || ''))} · pop ${sys.population?.toLocaleString() ?? '—'} · ${sys.connections?.toLocaleString() ?? '—'} connections</p>${pill}`;
  html += '<div class="card"><h3>Consumer Confidence Report</h3>';
  if (r.status === 'found') {
    html += `<div class="report-actions">${r.file ? `<a class="btn-primary" href="/${esc(r.file)}" target="_blank" rel="noopener">Open the ${esc(r.data_year ?? '')} report ${r.format === 'html' ? '' : '(PDF)'}</a>` : ''}${r.url ? `<a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">Original source</a>` : ''}</div>`;
    html += `<div class="kv"><span>Title</span><span>${esc(r.title || '—')}</span><span>Data year</span><span>${esc(r.data_year ?? '—')}</span><span>Document</span><span>${r.file ? `<a href="/${esc(r.file)}" target="_blank" rel="noopener">open saved copy</a>` : '—'}${r.url ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">source</a>` : ''}</span><span>Retrieved</span><span>${esc(r.retrieved || '—')}</span></div>`;
    if (r.sources_described) html += `<p class="clamp" data-clamp><b>Water sources:</b> ${esc(r.sources_described)}</p>`;
    if (r.violations?.length) html += '<p><b>Violations / exceedances disclosed:</b></p>' + r.violations.map((v) => `<div class="violation">${esc(v)}</div>`).join('');
    if (r.extraction_notes) html += `<details class="notes"><summary>Extraction notes</summary><p class="mono">${esc(r.extraction_notes)}</p></details>`;
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
  b.querySelectorAll('[data-clamp]').forEach((p) => p.addEventListener('click', () => p.classList.toggle('clamp')));
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
      const lc = a.group === 'lead_copper' && v.p90 != null;
      html += `<tr><td><b>${esc(a.name)}</b><div class="sub" style="margin:0">${esc(stdLabel(a))}${lc ? ' · colored by 90th percentile of tap samples' : ''}</div></td><td class="num">${det ? `${lc ? `${fmt(v.p90)} ${a.unit} <span class="sub">p90</span><div class="sub" style="margin:0">max ${fmt(v.max)}</div>` : `${fmt(v.max)} ${a.unit}`}` : 'ND'}${bar}</td><td class="num">${v.latest != null ? `${fmt(v.latest)} ${a.unit}` : 'ND'}<div class="sub" style="margin:0">${esc(v.latest_date || '')}</div></td><td class="num">${v.n_detected ?? 0}/${v.n ?? 0}${v.sampling_points ? `<div class="sub" style="margin:0">${v.sampling_points} pts</div>` : ''}</td></tr>`;
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
  chip.innerHTML = n ? `${esc(src)} · <b>composite of ${n} analyte${n === 1 ? '' : 's'}</b> · worst % of standard` : `${esc(src)} · <b>no analyte selected</b>`;
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
  $('#stats .stat:nth-child(2) .lbl').textContent = src === 'ccr' ? 'not in report' : 'not sampled';
  $('#stats .stat:nth-child(3) .lbl').textContent = src === 'ccr' ? 'no report' : 'no lab data';
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
  S.api = { openDetail, closeDetail, scoreSystem, recolor, setSource, renderSidebar, renderReports };
  setSource(S.source);
  S.renderer = webglAvailable() && !location.hash.includes('svg=1') ? 'maplibre' : 'svg';
  try { if (S.renderer === 'svg') initSvgMap(); else await initMap(); } catch (err) { console.error(err); if (S.renderer !== 'svg') { S.renderer = 'svg'; try { initSvgMap(); } catch (e2) { fatal(e2.message); } } else fatal(err.message); }
  // controls
  document.querySelectorAll('.seg-btn').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) setSource(b.dataset.source); }));
  $('#search').addEventListener('input', (e) => { S.search = e.target.value; renderSidebar(); });
  $('#btn-primary').addEventListener('click', () => { S.enabled = new Set(S.site.analytes.filter((a) => PRIMARY_GROUPS.has(a.group) && countFor(a) > 0).map((a) => a.id)); S.solo = null; renderSidebar(); recolor(); });
  $('#btn-all').addEventListener('click', () => { S.enabled = new Set(S.site.analytes.filter((a) => countFor(a) > 0).map((a) => a.id)); S.solo = null; renderSidebar(); recolor(); });
  $('#btn-none').addEventListener('click', () => { S.enabled = new Set(); S.solo = null; renderSidebar(); recolor(); });
  $('#solo-exit').addEventListener('click', () => { S.solo = null; renderSidebar(); recolor(); });
  $('#toggle-noncommunity').checked = S.showNonCommunity;
  $('#toggle-noncommunity').addEventListener('change', (e) => { S.showNonCommunity = e.target.checked; recolor(); });
  $('#toggle-labels').addEventListener('change', (e) => { S.showLabels = e.target.checked; if (S.svg) { S.svg.apply(); return; } for (const id of ['sys-label-big', 'sys-label-small']) if (S.map.getLayer(id)) S.map.setLayoutProperty(id, 'visibility', S.showLabels ? 'visible' : 'none'); });
  $('#btn-reports').addEventListener('click', () => { renderReports(); $('#reports').classList.remove('hidden'); });
  $('#reports .modal-close').addEventListener('click', () => $('#reports').classList.add('hidden'));
  $('#reports').addEventListener('click', (e) => { if (e.target.id === 'reports') $('#reports').classList.add('hidden'); });
  $('#btn-theme').addEventListener('click', () => setTheme(S.theme === 'dark' ? 'light' : 'dark'));
  $('#detail-close').addEventListener('click', closeDetail);
  $('#about-link').addEventListener('click', (e) => { e.preventDefault(); $('#about').classList.remove('hidden'); });
  $('#about .modal-close').addEventListener('click', () => $('#about').classList.add('hidden'));
  $('#about').addEventListener('click', (e) => { if (e.target.id === 'about') $('#about').classList.add('hidden'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeDetail(); $('#about').classList.add('hidden'); $('#reports').classList.add('hidden'); } });
}
function renderReports() {
  const body = $('#reports-body');
  const systems = Object.values(S.site.systems).sort((a, b) => (b.population || 0) - (a.population || 0));
  const found = systems.filter((s) => s.report?.status === 'found');
  const missing = systems.filter((s) => s.report?.status === 'not_found');
  let html = `<p class="sub">${found.length} reports found · ${missing.length} community systems without a locatable report · ${systems.length - found.length - missing.length} non-community systems (no report required)</p>`;
  html += '<table class="vals"><thead><tr><th>Water system</th><th>Population</th><th>Report</th><th></th></tr></thead><tbody>';
  for (const s of found) {
    const r = s.report;
    html += `<tr><td><b>${esc(displayName(s))}</b><div class="sub" style="margin:0">${esc(s.pwsid)}</div></td><td class="num">${s.population?.toLocaleString() ?? '—'}</td><td>${esc(r.data_year ?? '')} · ${esc((r.format || 'pdf').toUpperCase())}${r.pages ? ` · ${r.pages} pp` : ''}</td><td class="num">${r.file ? `<a class="btn-primary small-btn" href="/${esc(r.file)}" target="_blank" rel="noopener">Open</a>` : ''} ${r.url ? `<a class="link" href="${esc(r.url)}" target="_blank" rel="noopener">source</a>` : ''} <button class="link" data-detail="${esc(s.pwsid)}">map</button></td></tr>`;
  }
  html += '</tbody></table>';
  if (missing.length) html += `<details style="margin-top:12px"><summary class="sub">Community systems with no locatable report (${missing.length})</summary><ul class="search-log">${missing.map((s) => `<li>${esc(displayName(s))} (${esc(s.pwsid)}, pop ${s.population?.toLocaleString() ?? '—'})</li>`).join('')}</ul></details>`;
  body.innerHTML = html;
  body.querySelectorAll('[data-detail]').forEach((b) => b.addEventListener('click', () => { $('#reports').classList.add('hidden'); const s = S.site.systems[b.dataset.detail]; openDetail(s.pwsid, s.fid); }));
}
window.addEventListener('error', (e) => fatal(e.message || String(e.error)));
window.addEventListener('unhandledrejection', (e) => fatal(e.reason?.message || String(e.reason)));
boot();
})();
