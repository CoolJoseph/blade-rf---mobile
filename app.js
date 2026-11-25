/* bladeRF Workbench Mobile -- front end.
 *
 * Talks to the Python back end on 127.0.0.1 (the desktop program's own
 * sweep engine). Everything here is presentation and touch handling; the
 * numbers come from the engine. Markers are read off the same trace data
 * the engine delivers, never off the drawn picture (desktop bug 35).
 */
'use strict';

const AND = window.Android || null;            // Kotlin bridge, absent in a browser
const N_DIV = 10;
const N_MK = 6;
const TRACE_COLORS = ['#ffd24a', '#4ac6ff', '#ff8a3d', '#35e07a'];
const LUT_STOPS = [[0.00, [0, 0, 12]], [0.18, [20, 16, 90]], [0.38, [75, 20, 130]],
  [0.56, [150, 40, 110]], [0.72, [215, 75, 60]], [0.86, [245, 150, 30]], [1.00, [255, 245, 200]]];
const RBW_LIST = [1e3, 3e3, 10e3, 30e3, 100e3, 300e3, 1e6, 3e6];
const FS_LIST = [0, 5e6, 10e6, 20e6, 30.72e6, 40e6, 56e6];
const PANEL_ACC = {freq: '--tune', presets: '--tune', bw: '--view', ampl: '--view', traces: '--view', display: '--view',
  markers: '--meas', signals: '--meas', radio: '--radio', capture: '--file', settings: '--file',
  point: '--meas', surveys: '--file', mapset: '--file'};

// ------------------------------------------------------------------ state
const S = {
  st: null, set: null, presets: [], bands: [],
  seq: -1, fr: null, newRow: false,
  view: {c: 2.44e9, s: 100e6},            // what is drawn (local while touching)
  touching: false, lastSpan: null,
  ui: {lang: 'he', orient: 'auto', ref: -20, div: 10, wf: true, wfRows: 160, bands: true, peakLabel: true,
       mkTrace: 0, keepOn: true, sigAuto: true},
  mk: Array.from({length: N_MK}, () => ({on: false, hz: 0, delta: false})),
  mAct: 0, track: false,
  ident: null, sigs: null, panel: null, dirty: true,
};

// ------------------------------------------------------------------ helpers
const $ = (id) => document.getElementById(id);
function t(k) { const d = I18N[S.ui.lang] || I18N.en; return d[k] ?? I18N.en[k] ?? k; }
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'cls') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function fmtHz(hz, digits = 6) {
  const a = Math.abs(hz);
  if (a >= 1e9) return (hz / 1e9).toFixed(digits) + ' GHz';
  if (a >= 1e6) return (hz / 1e6).toFixed(digits) + ' MHz';
  if (a >= 1e3) return (hz / 1e3).toFixed(Math.max(digits - 3, 0)) + ' kHz';
  return hz.toFixed(0) + ' Hz';
}
function trimHz(hz) {   // 2.440000 GHz -> 2.44 GHz
  return fmtHz(hz, 6).replace(/(\.\d*?)0+ /, '$1 ').replace(/\. /, ' ');
}
function fmtShort(hz) {
  const a = Math.abs(hz), p = (v) => +v.toPrecision(4);
  if (a >= 1e9) return p(hz / 1e9) + ' GHz';
  if (a >= 1e6) return p(hz / 1e6) + ' MHz';
  if (a >= 1e3) return p(hz / 1e3) + ' kHz';
  return p(hz) + ' Hz';
}
function parseHz(text) {        // the desktop's parse_hz, same rules
  if (!text) return null;
  let s = String(text).trim().toLowerCase().replace(/hz/g, '').replace(/,/g, '').trim();
  if (!s) return null;
  let mult = null;
  for (const [sfx, m] of [['g', 1e9], ['m', 1e6], ['k', 1e3]]) {
    if (s.endsWith(sfx)) { mult = m; s = s.slice(0, -1).trim(); break; }
  }
  const v = Number(s);
  if (!isFinite(v)) return null;
  if (mult != null) return v * mult;
  return Math.abs(v) >= 1e5 ? v : v * 1e6;
}
const fmtDb = (v) => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1);
const fmtDbm = (v) => (v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + ' dBm';

async function api(op, body) {
  const r = await fetch('/api/' + op, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})});
  return r.json();
}
async function getJSON(path) { const r = await fetch(path); return r.json(); }

let toastTimer = 0;
function toast(msg, ms = 2500) {
  const e = $('st-msg'); e.textContent = msg; e.dataset.toast = '1';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { e.dataset.toast = ''; }, ms);
}

// ------------------------------------------------------------- settings io
let setTimer = 0, pendingSet = {};
function pushSet(ch, now = false) {
  Object.assign(pendingSet, ch);
  Object.assign(S.set, ch);
  clearTimeout(setTimer);
  const go = () => { setTimer = 0; const c = pendingSet; pendingSet = {}; api('set', c).catch(() => {}); };
  if (now) go(); else setTimer = setTimeout(go, 90);
}
let uiTimer = 0;
function saveUi() {
  clearTimeout(uiTimer);
  uiTimer = setTimeout(() => api('set', {ui: S.ui}).catch(() => {}), 400);
}
function tune(c, s, now = false) {
  const st = S.st ? S.st.source : {freq_min: 70e6, freq_max: 6e9};
  s = Math.min(Math.max(s, 1e3), st.freq_max - st.freq_min);
  c = Math.min(Math.max(c, st.freq_min + s / 2), st.freq_max - s / 2);
  S.view = {c, s};
  pushSet({center: c, span: s}, now);
  markPreset(); updateReadouts(); S.dirty = true;
}

// ================================================================ drawing
const spec = $('spec'), wfc = $('wf');
let dpr = window.devicePixelRatio || 1;
const M = {l: 46, r: 6, t: 4, b: 20};   // plot margins in CSS px
let bandH = 0;

function resizeCanvases() {
  dpr = window.devicePixelRatio || 1;
  for (const c of [spec, wfc]) {
    const r = c.getBoundingClientRect();
    const w = Math.max(10, Math.round(r.width * dpr)), hh = Math.max(10, Math.round(r.height * dpr));
    if (c.width !== w || c.height !== hh) { c.width = w; c.height = hh; }
  }
  S.dirty = true;
}
// A "plot description" P says WHAT to draw (view, scale, data); rectOf()
// turns it into pixels for one canvas. The live spectrum, the map's mini
// strip, saved points and JPEG exports all go through drawSpectrumOn().
function liveP() {
  return {c: S.view.c, s: S.view.s, ref: S.ui.ref, div: S.ui.div, bands: S.ui.bands};
}
function rectOf(cv, P, scale) {
  const sc = scale || dpr, W = cv.width / sc, H = cv.height / sc;
  const bh = P.bands ? 13 : 0, ml = P.compact ? 36 : M.l, mb = P.compact ? 15 : M.b;
  return {x: ml, y: M.t + bh, w: W - ml - M.r, h: H - M.t - mb - bh, W, H,
    c: P.c, s: P.s, ref: P.ref, div: P.div, bandH: bh};
}
function plotRect() { const R = rectOf(spec, liveP()); bandH = R.bandH; return R; }
function fx(f, R) { return R.x + (f - (R.c - R.s / 2)) / R.s * R.w; }
function xf(x, R) { return R.c - R.s / 2 + (x - R.x) / R.w * R.s; }
function yOf(db, R) { return R.y + (R.ref - db) / (N_DIV * R.div) * R.h; }

function niceTicks(lo, hi, n) {
  const raw = (hi - lo) / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * p).find(s => s >= raw) || 10 * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return {out, step};
}
function tickLabel(v, step) {
  const a = Math.max(Math.abs(v), 1);
  const [unit, div] = a >= 1e9 ? ['G', 1e9] : a >= 1e6 ? ['M', 1e6] : a >= 1e3 ? ['k', 1e3] : ['', 1];
  const dec = Math.min(6, Math.max(0, Math.ceil(-Math.log10(step / div))));
  return (v / div).toFixed(dec) + unit;
}

function traceForMarkers() {
  const fr = S.fr; if (!fr) return null;
  const i = S.ui.mkTrace;
  if (fr.tr[i]) return fr.tr[i];
  return fr.tr.find(x => x) || null;
}
function idxOf(freqs, hz) {    // nearest index, freqs ascending
  let lo = 0, hi = freqs.length - 1;
  if (hi < 0) return -1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (freqs[m] < hz) lo = m; else hi = m; }
  return Math.abs(freqs[lo] - hz) <= Math.abs(freqs[hi] - hz) ? lo : hi;
}
function mkValue(m) {
  const d = traceForMarkers(); if (!d || !S.fr) return NaN;
  const f = S.fr.freqs, n = f.length;
  // A marker anchors on FREQUENCY (desktop bug 38). Off the trace it has no
  // level -- reading the nearest edge point would print a fake number.
  if (!n || m.hz < f[0] - (f[1] - f[0]) || m.hz > f[n - 1] + (f[1] - f[0])) return NaN;
  const i = idxOf(f, m.hz); return i < 0 ? NaN : d[i];
}

function liveMarkers() {
  const ref = S.mk[0], out = [];
  S.mk.forEach((m, i) => {
    if (!m.on) return;
    const v = mkValue(m); if (!isFinite(v)) return;
    out.push({hz: m.hz, v, act: i === S.mAct, label: (m.delta && i > 0 && ref.on ? 'D' : 'M') + (i + 1)});
  });
  return out;
}
function drawSpectrum() {
  const fr = S.fr, P = liveP();
  if (fr) {
    P.freqs = fr.freqs; P.tr = fr.tr;
    const mkNear = S.mk.some(m => m.on && Math.abs(m.hz - (fr.h.peak_hz || 0)) < S.view.s * 0.03);
    if (S.ui.peakLabel && !mkNear && fr.h.peak_hz != null && fr.tr[0]) P.peak = {hz: fr.h.peak_hz, dbm: fr.h.peak_dbm};
    P.markers = liveMarkers();
  }
  P.shade = [];
  if (S.ident && S.ident.carrier) P.shade.push([S.ident.lo, S.ident.hi, 'rgba(124,255,107,0.14)']);
  if (S.panel === 'signals' && S.sigs) for (const it of S.sigs.items) if (it.width > 0) P.shade.push([it.lo, it.hi, 'rgba(124,255,107,0.07)']);
  P.watermark = isSim() ? t('src_sim').toUpperCase() : '';
  const R = drawSpectrumOn(spec, P);
  bandH = R.bandH;
}
function isSim() { return !!(S.st && S.st.source && S.st.source.sim); }

function drawSpectrumOn(cv, P, scale) {
  const sc = scale || dpr, ctx = cv.getContext('2d');
  ctx.setTransform(sc, 0, 0, sc, 0, 0);
  ctx.direction = 'ltr';    // numbers and units, also inside an RTL panel
  const R = rectOf(cv, P, sc), fs = P.font || 10;
  ctx.fillStyle = '#07090b'; ctx.fillRect(0, 0, R.W, R.H);
  // graticule: 10 x 10 divisions, like a bench analyzer
  ctx.strokeStyle = 'rgba(180,192,204,0.28)'; ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i <= N_DIV; i++) {
    const y = Math.round(R.y + i * R.h / N_DIV) + 0.5; ctx.moveTo(R.x, y); ctx.lineTo(R.x + R.w, y);
    const x = Math.round(R.x + i * R.w / N_DIV) + 0.5; ctx.moveTo(x, R.y); ctx.lineTo(x, R.y + R.h);
  }
  ctx.stroke();
  ctx.strokeStyle = '#b4c0cc'; ctx.strokeRect(R.x + 0.5, R.y + 0.5, R.w, R.h);
  if (P.watermark) {
    ctx.save(); ctx.fillStyle = 'rgba(255,179,64,0.10)'; ctx.font = `bold ${Math.max(16, Math.min(R.w / 9, 54))}px system-ui`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(P.watermark, R.x + R.w / 2, R.y + R.h / 2); ctx.restore();
  }
  ctx.fillStyle = '#c3d1de'; ctx.font = `${fs}px system-ui,sans-serif`; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  const every = P.compact ? 2 : 1;
  for (let i = 0; i <= N_DIV; i += every) ctx.fillText(String(+(R.ref - i * R.div).toFixed(1)), R.x - 4, R.y + i * R.h / N_DIV);
  if (!P.compact) {
    ctx.save(); ctx.translate(9, R.y + R.h / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
    ctx.fillText('dBm', 0, 0); ctx.restore();
  }
  const tk = niceTicks(R.c - R.s / 2, R.c + R.s / 2, Math.max(2, Math.floor(R.w / (fs * 8.5))));
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (const v of tk.out) { const x = fx(v, R); if (x > R.x + 12 && x < R.x + R.w - 12) ctx.fillText(tickLabel(v, tk.step), x, R.y + R.h + 3); }
  if (R.bandH) drawBands(ctx, R);

  ctx.save(); ctx.beginPath(); ctx.rect(R.x, R.y, R.w, R.h); ctx.clip();
  for (const [lo, hi, col] of (P.shade || [])) { const a = fx(lo, R), b = fx(hi, R); ctx.fillStyle = col; ctx.fillRect(a, R.y, Math.max(1, b - a), R.h); }
  if (P.freqs && P.tr) {
    // back to front, so trace 1 (Live) stays on top
    for (let i = P.tr.length - 1; i >= 0; i--) { const d = P.tr[i]; if (d) drawTrace(ctx, R, P.freqs, d, (P.colors || TRACE_COLORS)[i] || '#888'); }
  }
  if (P.peak) {
    const x = fx(P.peak.hz, R), y = yOf(P.peak.dbm, R);
    if (x > R.x && x < R.x + R.w) {
      ctx.fillStyle = 'rgba(255,255,255,0.75)'; ctx.font = `${fs}px system-ui`; ctx.textAlign = x > R.x + R.w - 90 ? 'right' : 'left';
      ctx.textBaseline = 'bottom'; ctx.fillText('▾ ' + fmtShort(P.peak.hz), x - 4, Math.max(R.y + 12, y - 2));
    }
  }
  if (P.markers) drawMarkers(ctx, R, P.markers, fs);
  ctx.restore();
  return R;
}

function drawTrace(ctx, R, f, d, col) {
  const n = f.length; if (!n) return;
  ctx.strokeStyle = col; ctx.lineWidth = 1.3; ctx.beginPath();
  const pxPer = R.w / Math.max(1, (f[n - 1] - f[0]) / R.s * n);
  if (pxPer < 0.5) {
    // more points than pixels: min/max per column so narrow peaks survive
    let col0 = -1, mn = 0, mx = 0, started = false;
    const flush = () => {
      if (col0 < 0) return;
      const y1 = yOf(mx, R), y2 = yOf(mn, R);
      if (!started) { ctx.moveTo(col0, y1); started = true; } else ctx.lineTo(col0, y1);
      ctx.lineTo(col0, y2);
    };
    for (let i = 0; i < n; i++) {
      const v = d[i]; if (!isFinite(v)) continue;
      const c = Math.round(fx(f[i], R));
      if (c !== col0) { flush(); col0 = c; mn = mx = v; } else { if (v < mn) mn = v; if (v > mx) mx = v; }
    }
    flush();
  } else {
    let started = false;
    for (let i = 0; i < n; i++) {
      const v = d[i]; if (!isFinite(v)) { started = false; continue; }
      const x = fx(f[i], R), y = yOf(v, R);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
  }
  ctx.stroke();
}

function drawBands(ctx, R) {
  const f0 = R.c - R.s / 2, f1 = R.c + R.s / 2;
  const y = M.t, hh = R.bandH - 2;
  ctx.font = '9px system-ui'; ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
  let k = 0;
  for (const b of S.bands) {
    if (b.hi < f0 || b.lo > f1) continue;
    const a = Math.max(R.x, fx(b.lo, R)), e = Math.min(R.x + R.w, fx(b.hi, R));
    if (e - a < 2) continue;
    const col = /UL/.test(b.name) ? '74,198,255' : /DL|TDD|NR|SDL/.test(b.name) ? '183,156,255' : '124,255,107';
    ctx.fillStyle = `rgba(${col},0.22)`; ctx.fillRect(a, y + (k % 2), e - a, hh);
    ctx.fillStyle = `rgba(${col},0.95)`;
    if (e - a > 40) { ctx.save(); ctx.beginPath(); ctx.rect(a, y, e - a, hh + 2); ctx.clip(); ctx.fillText(b.name, (a + e) / 2, y + hh / 2 + 1); ctx.restore(); }
    k++;
  }
}

function drawMarkers(ctx, R, list, fs) {
  ctx.font = `bold ${(fs || 10) + 1}px system-ui`; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
  for (const m of list) {
    const x = fx(m.hz, R), y = yOf(m.v, R);
    ctx.fillStyle = m.act ? '#ff3b30' : '#ff9f0a'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 6, y); ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.fillText(m.label, x, Math.max(R.y + 12, y - 9));
  }
}

// -------------------------------------------------------------- waterfall
const LUT = (() => {
  const out = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let k = 0; while (k < LUT_STOPS.length - 2 && x > LUT_STOPS[k + 1][0]) k++;
    const [p0, c0] = LUT_STOPS[k], [p1, c1] = LUT_STOPS[k + 1];
    const u = Math.min(1, Math.max(0, (x - p0) / (p1 - p0)));
    for (let c = 0; c < 3; c++) out[i * 3 + c] = Math.round(c0[c] + (c1[c] - c0[c]) * u);
  }
  return out;
})();
const WF = {buf: document.createElement('canvas'), key: '', n: 0, rows: 0};
function wfPush(fr) {
  if (!fr.wf) return;
  const n = fr.wf.length, rows = S.ui.wfRows;
  const key = `${fr.freqs[0]}|${fr.freqs[n - 1]}|${n}|${rows}`;
  const b = WF.buf, c = b.getContext('2d');
  if (key !== WF.key) {
    WF.key = key; WF.n = n; WF.rows = rows; WF.f0 = fr.freqs[0]; WF.f1 = fr.freqs[n - 1];
    b.width = n; b.height = rows; c.fillStyle = '#00000c'; c.fillRect(0, 0, n, rows);
  }
  c.drawImage(b, 0, 0, n, rows - 1, 0, 1, n, rows - 1);    // scroll down one row
  const img = c.createImageData(n, 1), px = img.data;
  const hi = S.ui.ref, lo = S.ui.ref - N_DIV * S.ui.div, k = 255 / (hi - lo);
  for (let i = 0; i < n; i++) {
    let v = Math.round((fr.wf[i] - lo) * k); v = v < 0 ? 0 : v > 255 ? 255 : (v | 0);
    px[i * 4] = LUT[v * 3]; px[i * 4 + 1] = LUT[v * 3 + 1]; px[i * 4 + 2] = LUT[v * 3 + 2]; px[i * 4 + 3] = 255;
  }
  c.putImageData(img, 0, 0);
}
function drawWaterfall() {
  if (!S.ui.wf) return;
  const ctx = wfc.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = wfc.width / dpr, H = wfc.height / dpr, R = {...plotRect(), y: 0, h: H};
  ctx.fillStyle = '#07090b'; ctx.fillRect(0, 0, W, H);
  if (WF.n) {
    ctx.imageSmoothingEnabled = false;
    const a = fx(WF.f0, R), b = fx(WF.f1, R);
    ctx.drawImage(WF.buf, 0, 0, WF.n, WF.rows, a, 0, b - a, H);
  }
  ctx.fillStyle = '#07090b'; ctx.fillRect(0, 0, R.x, H);
  ctx.strokeStyle = '#b4c0cc'; ctx.strokeRect(R.x + 0.5, 0.5, R.w, H - 1);
  ctx.fillStyle = '#8fa6bb'; ctx.font = '10px system-ui'; ctx.textAlign = 'right'; ctx.textBaseline = 'top';
  ctx.fillText('now', R.x - 4, 2);
  ctx.textBaseline = 'bottom';
  const secs = S.fr && S.fr.h.rate > 0 ? WF.rows / Math.min(S.fr.h.rate, 15) : 0;
  if (secs) ctx.fillText('−' + (secs >= 60 ? (secs / 60).toFixed(1) + 'm' : secs.toFixed(0) + 's'), R.x - 4, H - 2);
  // marker lines through the waterfall
  S.mk.forEach((m, i) => {
    if (!m.on) return; const x = fx(m.hz, R);
    ctx.strokeStyle = i === S.mAct ? 'rgba(255,59,48,0.8)' : 'rgba(255,159,10,0.6)'; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); ctx.setLineDash([]);
  });
}

// ------------------------------------------------------------ marker text
function updateMarkerReadout() {
  const box = $('mk-readout'); box.textContent = '';
  box.style.top = (M.t + bandH + 3) + 'px';
  const ref = S.mk[0], rv = mkValue(ref);
  S.mk.forEach((m, i) => {
    if (!m.on) return;
    const v = mkValue(m);
    let txt;
    if (m.delta && i > 0 && ref.on) {
      const df = m.hz - ref.hz;
      txt = `D${i + 1}  Δ ${df >= 0 ? '+' : '−'}${fmtShort(Math.abs(df))}  ${isFinite(v - rv) ? fmtDb(v - rv) + ' dB' : '—'}`;
    } else {
      txt = `M${i + 1}  ${trimHz(m.hz)}  ${isFinite(v) ? fmtDbm(v) : '—'}`;
    }
    const row = h('div', {cls: 'm', style: i === S.mAct ? 'color:#ffd0cc' : ''}, txt);
    box.append(row);
    if (i === S.mAct) {
      const bands = S.bands.filter(b => m.hz >= b.lo && m.hz <= b.hi).map(b => b.name);
      if (bands.length) box.append(h('div', {cls: 'b'}, bands.join(' / ')));
    }
  });
}

// =============================================================== render
function frameLoopDraw() {
  if (S.dirty) {
    S.dirty = false;
    if (S.newRow && S.fr) { wfPush(S.fr); S.newRow = false; }
    drawSpectrum(); drawWaterfall(); updateMarkerReadout();
  }
  requestAnimationFrame(frameLoopDraw);
}

async function pump() {
  for (;;) {
    try {
      const r = await fetch('/api/frame?since=' + S.seq);
      if (r.status === 200) {
        decodeFrame(await r.arrayBuffer());
      } else { await sleep(35); }
    } catch (e) { await sleep(600); }
  }
}
function decodeFrame(buf) {
  const dv = new DataView(buf);
  const hl = dv.getUint32(0, true);
  const h = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  let off = 4 + hl;
  const n = h.n;
  const freqs = new Float64Array(buf.slice(off, off + n * 8)); off += n * 8;
  const tr = [];
  for (const has of h.has) {
    if (has) { tr.push(new Float32Array(buf.slice(off, off + n * 4))); off += n * 4; } else tr.push(null);
  }
  const wf = h.wf ? new Float32Array(buf.slice(off, off + n * 4)) : null;
  S.fr = {h, freqs, tr, wf}; S.seq = h.seq; S.newRow = true; S.dirty = true; S.newMini = true;
  if (!S.touching && !setTimer && (h.center !== S.view.c || h.span !== S.view.s)) {
    S.view = {c: h.center, s: h.span};
  }
  if (S.track) { peakSearch(S.mAct, false); }
  updateStatus();
}

// ================================================================= touch
const ptrs = new Map();
let g = null;     // current gesture
spec.addEventListener('pointerdown', (e) => {
  spec.setPointerCapture(e.pointerId);
  ptrs.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
  const R = plotRect();
  if (ptrs.size === 1) {
    g = {kind: 'tap', x0: e.offsetX, y0: e.offsetY, t0: performance.now(), c0: S.view.c, s0: S.view.s,
      ref0: S.ui.ref, onAxis: e.offsetX < R.x};
    clearTimeout(g.lp);
    g.lp = setTimeout(() => { if (g && g.kind === 'tap') { g.kind = 'done'; identifyAt(xf(g.x0, plotRect())); } }, 550);
  } else if (ptrs.size === 2) {
    clearTimeout(g && g.lp);
    const [a, b] = [...ptrs.values()];
    g = {kind: 'pinch', d0: Math.max(20, Math.abs(a.x - b.x)), m0: (a.x + b.x) / 2, c0: S.view.c, s0: S.view.s};
    g.fAnchor = xf(g.m0, R);
    S.touching = true;
  }
});
spec.addEventListener('pointermove', (e) => {
  if (!ptrs.has(e.pointerId) || !g) return;
  ptrs.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
  const R = plotRect();
  if (g.kind === 'tap' || g.kind === 'pan' || g.kind === 'ref') {
    const dx = e.offsetX - g.x0, dy = e.offsetY - g.y0;
    if (g.kind === 'tap' && Math.hypot(dx, dy) > 8) {
      clearTimeout(g.lp);
      g.kind = g.onAxis ? 'ref' : (S.ui.dragMarker ? 'mdrag' : 'pan');
      S.touching = true;
    }
    if (g.kind === 'pan') {
      const c = g.c0 - dx / R.w * g.s0;
      tune(c, g.s0);
    } else if (g.kind === 'ref') {
      const per = (N_DIV * S.ui.div) / R.h;
      S.ui.ref = Math.round((g.ref0 + dy * per) * 2) / 2; WF.key = ''; S.dirty = true; saveUi();
    }
  }
  if (g.kind === 'mdrag') { placeMarker(xf(e.offsetX, R)); }
  if (g.kind === 'pinch' && ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    const d = Math.max(20, Math.abs(a.x - b.x)), m = (a.x + b.x) / 2;
    const s = g.s0 * g.d0 / d;
    const frac = (m - R.x) / R.w;
    const c = g.fAnchor - (frac - 0.5) * s;
    tune(c, s);
  }
});
function endPtr(e) {
  ptrs.delete(e.pointerId);
  if (!g) return;
  clearTimeout(g.lp);
  if (g.kind === 'tap' && ptrs.size === 0) {
    const now = performance.now();
    if (now - (S.lastTap || 0) < 300 && Math.abs(e.offsetX - (S.lastTapX || 0)) < 30) {
      peakSearch(S.mAct, true); S.lastTap = 0;
    } else {
      S.lastTap = now; S.lastTapX = e.offsetX;
      if (e.offsetX >= plotRect().x) placeMarker(xf(e.offsetX, plotRect()));
    }
  }
  if (ptrs.size === 0) {
    if (g.kind === 'pan' || g.kind === 'pinch') { pushSet({center: S.view.c, span: S.view.s}, true); }
    S.touching = false; g = null;
  }
}
spec.addEventListener('pointerup', endPtr);
spec.addEventListener('pointercancel', endPtr);
spec.addEventListener('wheel', (e) => {     // desktop browser testing
  e.preventDefault();
  const R = plotRect(), f = xf(e.offsetX, R), s = S.view.s * Math.pow(0.8, -Math.sign(e.deltaY));
  const frac = (e.offsetX - R.x) / R.w; tune(f - (frac - 0.5) * s, s);
}, {passive: false});

let hintTimer = 0;
function hint(html, ms = 6000) {
  const e = $('hint'); e.innerHTML = html; e.classList.remove('hidden');
  clearTimeout(hintTimer); hintTimer = setTimeout(() => e.classList.add('hidden'), ms);
}
async function identifyAt(hz) {
  try {
    const r = await getJSON('/api/identify?hz=' + hz);
    S.ident = r; S.dirty = true;
    let html = `<b class="ltr">${trimHz(hz)}</b>`;
    if (r.carrier) {
      html += `<br><bdi>${r.tech || t('unknown')}</bdi> · <bdi dir="ltr">${fmtShort(r.occupied)}</bdi> ${t('occupied')} · <bdi dir="ltr">${fmtDbm(r.level)}</bdi>`;
      if (r.band) html += `<br>${t('id_band')}: <bdi dir="ltr">${r.band}</bdi>`;
    } else {
      html += `<br>${t('id_none')}`;
      if (r.bands && r.bands.length) html += `<br>${t('id_band')}: <span class="ltr">${r.bands.join(' / ')}</span>`;
    }
    hint(html);
    setTimeout(() => { S.ident = null; S.dirty = true; }, 6000);
  } catch (e) { /* ignore */ }
}

// =============================================================== markers
function placeMarker(hz) {
  const m = S.mk[S.mAct]; m.on = true; m.hz = hz;
  if (S.ui.snap !== false) snapLocal(m);
  S.dirty = true; if (S.panel === 'markers') renderPanel();
}
function snapLocal(m) {     // nearest local maximum within ~1.5% of the view
  const d = traceForMarkers(); if (!d) return;
  const f = S.fr.freqs, i0 = idxOf(f, m.hz), w = Math.max(2, Math.round(d.length * 0.015));
  let bi = i0;
  for (let i = Math.max(0, i0 - w); i <= Math.min(d.length - 1, i0 + w); i++) if (d[i] > d[bi]) bi = i;
  m.hz = f[bi];
}
function inView(f) { return f >= S.view.c - S.view.s / 2 && f <= S.view.c + S.view.s / 2; }
function peakSearch(k, redraw = true) {
  const d = traceForMarkers(); if (!d) return;
  const f = S.fr.freqs; let bi = -1;
  for (let i = 0; i < d.length; i++) if (isFinite(d[i]) && inView(f[i]) && (bi < 0 || d[i] > d[bi])) bi = i;
  if (bi < 0) return;
  const m = S.mk[k]; m.on = true; m.hz = f[bi];
  if (redraw) { S.dirty = true; if (S.panel === 'markers') renderPanel(); }
}
function localPeaks(d, f) {
  const out = [], w = Math.max(2, Math.round(d.length / 300));
  for (let i = w; i < d.length - w; i++) {
    if (!isFinite(d[i]) || !inView(f[i])) continue;
    let ok = true;
    for (let j = i - w; j <= i + w; j++) if (d[j] > d[i] || (d[j] === d[i] && j < i)) { ok = false; break; }
    if (ok) out.push(i);
  }
  return out;
}
function nextPeak(dir) {   // dir: 0 = next lower, -1 left, +1 right
  const d = traceForMarkers(); if (!d) return;
  const f = S.fr.freqs, m = S.mk[S.mAct];
  if (!m.on) { peakSearch(S.mAct); return; }
  const v0 = mkValue(m), pk = localPeaks(d, f);
  let best = -1;
  for (const i of pk) {
    if (dir === 0) { if (d[i] < v0 - 0.05 && (best < 0 || d[i] > d[best])) best = i; }
    else if (dir < 0) { if (f[i] < m.hz - 1 && (best < 0 || f[i] > f[best])) best = i; }
    else { if (f[i] > m.hz + 1 && (best < 0 || f[i] < f[best])) best = i; }
  }
  if (best >= 0) { m.hz = f[best]; S.dirty = true; if (S.panel === 'markers') renderPanel(); }
}

// ============================================================= readouts
function updateReadouts() {
  $('ro-center').textContent = trimHz(S.view.c);
  $('ro-span').textContent = fmtShort(S.view.s);
  const rbw = S.fr ? S.fr.h.rbw : (S.st ? S.st.rbw_now : 0);
  $('ro-rbw').textContent = rbw ? fmtShort(rbw) + (S.set && !S.set.rbw_auto ? '' : ' A') : '—';
}
function updateStatus() {
  updateReadouts();
  const fr = S.fr, fl = $('st-flags'); fl.textContent = '';
  const src = S.st ? S.st.source : null;
  if (src && src.lost) fl.append(h('span', {cls: 'w'}, t('src_lost')));
  if (fr && fr.h.overload) fl.append(h('span', {cls: 'w'}, t('st_overload')));
  if (fr && src && src.radio && fr.h.peak_dbm != null && fr.h.peak_dbm > -10) fl.append(h('span', {cls: 'w'}, t('st_damage')));
  if (fr && fr.h.fs_low) fl.append(h('span', {cls: 'a'}, t('st_fslow')));
  if (fr && fr.h.cal_extrap && src && src.radio) fl.append(h('span', {cls: 'a'}, t('st_calx')));
  if (S.set && !S.set.running) fl.append(h('span', {cls: 'a'}, t('st_held')));
  if (fr) $('st-rate').textContent = `${fr.h.rate.toFixed(fr.h.rate < 10 ? 1 : 0)} ${t('sweeps')} · ${fr.h.segments} ${t('seg')}`;
  const msg = $('st-msg');
  if (!msg.dataset.toast) msg.textContent = (S.st && S.st.message) || (src && src.stalled) || (src ? src.info : '');
  const ban = $('banner');
  if (src && src.lost) { ban.textContent = t('st_lost'); ban.classList.remove('hidden'); } else ban.classList.add('hidden');
}
function updateSourceChip() {
  const src = S.st.source, chip = $('b-source');
  chip.classList.toggle('radio', !!src.radio && !src.lost);
  chip.classList.toggle('sim', !!src.sim);
  chip.classList.toggle('lost', !!src.lost);
  $('src-label').textContent = src.lost ? t('src_lost') : src.radio ? `${t('src_radio')} ${t('connected')} · RX${(S.set.port || 0) + 1}` : t('src_sim');
  const run = $('b-run');
  run.textContent = S.set.running ? '❚❚' : '▶'; run.classList.toggle('held', !S.set.running);
  $('k-single').classList.toggle('on', !S.set.running);
}

// ================================================================ presets
function presetName(p) { return (S.ui.lang === 'he' && p.name_he) ? p.name_he : p.name; }
function renderPresetBar() {
  const bar = $('presetbar'); bar.textContent = '';
  for (const p of S.presets) {
    bar.append(h('button', {cls: 'pchip', 'data-id': p.id, onclick: () => applyPreset(p.id)}, presetName(p)));
  }
  bar.append(h('button', {cls: 'pchip add', onclick: () => openPanel('presets')}, '＋'));
  markPreset();
}
function markPreset() {
  const a = S.view.c - S.view.s / 2, b = S.view.c + S.view.s / 2;
  for (const el of document.querySelectorAll('.pchip[data-id]')) {
    const p = S.presets.find(q => q.id === el.dataset.id);
    el.classList.toggle('on', !!p && Math.abs(p.start - a) < 1e3 + 1e-4 * S.view.s && Math.abs(p.stop - b) < 1e3 + 1e-4 * S.view.s);
  }
}
async function applyPreset(id) {
  const p = S.presets.find(q => q.id === id); if (!p) return;
  S.lastSpan = {c: S.view.c, s: S.view.s};
  await api('preset', {op: 'apply', id});
  S.view = {c: (p.start + p.stop) / 2, s: p.stop - p.start};
  if (p.ref != null) { S.ui.ref = p.ref; saveUi(); }
  S.dirty = true; markPreset(); updateReadouts();
  const st = await getJSON('/api/state'); S.st = st; S.set = st.settings;
  toast(presetName(p));
}

// ================================================================ panels
function openPanel(name) {
  if (S.panel === name) { closePanel(); return; }
  S.panel = name;
  for (const k of document.querySelectorAll('.key[data-panel]')) k.classList.toggle('on', k.dataset.panel === name);
  $('sheet').style.setProperty('--acc', `var(${PANEL_ACC[name] || '--tune'})`);
  $('sheet').classList.remove('hidden');
  renderPanel();
  if (name === 'signals') refreshSignals();
}
function closePanel() {
  S.panel = null; $('sheet').classList.add('hidden');
  for (const k of document.querySelectorAll('.key[data-panel]')) k.classList.remove('on');
  S.dirty = true;
}
$('sheet-close').onclick = closePanel;

function box(title, ...kids) { return h('div', {cls: 'box'}, title ? h('h4', null, title) : null, ...kids); }
function row(label, ...kids) { return h('div', {cls: 'row'}, label != null ? h('label', null, label) : null, ...kids); }
function sel(opts, val, onchange, attrs = {}) {
  const s = h('select', {...attrs, onchange: (e) => onchange(e.target.value)});
  for (const o of opts) {
    const [v, lab] = Array.isArray(o) ? o : [o, o];
    const op = h('option', {value: v}, lab); if (String(v) === String(val)) op.selected = true; s.append(op);
  }
  return s;
}
function hzInput(val, onset) {
  const i = h('input', {type: 'text', inputmode: 'decimal', cls: 'grow ltr', value: trimHz(val)});
  const commit = () => { const v = parseHz(i.value); if (v != null) onset(v); else i.value = trimHz(val); };
  i.addEventListener('change', commit);
  i.addEventListener('keydown', (e) => { if (e.key === 'Enter') { i.blur(); } });
  return i;
}
function numInput(val, onset, attrs = {}) {
  const i = h('input', {type: 'number', value: val, cls: 'ltr', style: 'width:96px', inputmode: 'decimal', ...attrs});
  i.addEventListener('change', () => { const v = parseFloat(i.value); if (isFinite(v)) onset(v); });
  return i;
}
function chk(checked, onset, label) {
  const c = h('input', {type: 'checkbox'}); c.checked = !!checked;
  c.addEventListener('change', () => onset(c.checked));
  return h('label', {style: 'display:flex;align-items:center;gap:8px;color:var(--fg)'}, c, label);
}
function btn(label, fn, cls = '') { return h('button', {cls: 'btn ' + cls, onclick: fn}, label); }

function renderPanel() {
  const b = $('sheet-body'), name = S.panel; if (!name) return;
  const keep = b.scrollTop;
  b.textContent = '';
  $('sheet-title').textContent = t('p_' + name);
  const fn = PANELS[name]; if (fn) fn(b);
  b.scrollTop = keep;
}

const PANELS = {
  freq(b) {
    const c = S.view.c, s = S.view.s;
    b.append(box(null,
      row(t('center'), hzInput(c, (v) => { tune(v, S.view.s, true); renderPanel(); })),
      row(t('span'), hzInput(s, (v) => { S.lastSpan = {c: S.view.c, s: S.view.s}; tune(S.view.c, v, true); renderPanel(); })),
      row(t('start'), hzInput(c - s / 2, (v) => { const e = S.view.c + S.view.s / 2; tune((v + e) / 2, Math.max(1e3, e - v), true); renderPanel(); })),
      row(t('stop'), hzInput(c + s / 2, (v) => { const a = S.view.c - S.view.s / 2; tune((a + v) / 2, Math.max(1e3, v - a), true); renderPanel(); })),
      h('div', {cls: 'note'}, t('hz_note'))));
    const src = S.st.source;
    b.append(box(t('span'), h('div', {cls: 'seg'},
      btn(t('full_span'), () => { S.lastSpan = {...S.view}; tune((src.freq_min + src.freq_max) / 2, src.freq_max - src.freq_min, true); renderPanel(); }),
      btn(t('zoom_in') + ' ½', () => { S.lastSpan = {...S.view}; tune(S.view.c, S.view.s / 2, true); renderPanel(); }),
      btn(t('zoom_out') + ' ×2', () => { S.lastSpan = {...S.view}; tune(S.view.c, S.view.s * 2, true); renderPanel(); }),
      btn(t('last_span'), () => { if (S.lastSpan) { const l = S.lastSpan; S.lastSpan = {...S.view}; tune(l.c, l.s, true); renderPanel(); } }),
      ...[1e6, 10e6, 20e6, 50e6, 100e6, 500e6].map(v => btn(fmtShort(v), () => { S.lastSpan = {...S.view}; tune(S.view.c, v, true); renderPanel(); }, 'sm')))));
    const step = S.ui.cfStep || 0;
    const stepV = step > 0 ? step : S.view.s / 10;
    b.append(box(t('cf_step'),
      row(null, btn('◀', () => { tune(S.view.c - stepV, S.view.s, true); renderPanel(); }),
        hzInput(stepV, (v) => { S.ui.cfStep = v; saveUi(); renderPanel(); }),
        btn('▶', () => { tune(S.view.c + stepV, S.view.s, true); renderPanel(); })),
      h('div', {cls: 'note'}, t('gesture_note'))));
  },

  bw(b) {
    const s = S.set;
    b.append(box(null,
      row(null, chk(s.rbw_auto, (v) => { pushSet({rbw_auto: v}, true); renderPanel(); }, t('rbw_auto'))),
      row(t('rbw_manual'), sel(RBW_LIST.map(v => [v, fmtShort(v)]), s.rbw_auto ? '' : s.rbw,
        (v) => { pushSet({rbw: +v, rbw_auto: false}, true); renderPanel(); }, s.rbw_auto ? {disabled: true} : {})),
      row(t('rbw_measured'), h('b', {cls: 'ltr'}, S.fr ? fmtShort(S.fr.h.rbw) : '—'))));
    b.append(box(null,
      row(t('detector'), sel(S.st.detectors, s.detector, (v) => pushSet({detector: v}, true))),
      row(t('window'), sel(S.st.windows, s.window, (v) => pushSet({window: v}, true))),
      row(t('points'), sel([601, 1201, 2001, 3001], s.points, (v) => pushSet({points: +v}, true)))));
    b.append(box(t('max_fs'),
      row(null, sel(FS_LIST.map(v => [v, v ? (v / 1e6) + ' MS/s' : t('auto') + ` (${(S.st.source.max_fs / 1e6).toFixed(0)} MS/s)`]), s.max_fs || 0,
        (v) => pushSet({max_fs: +v}, true))),
      h('div', {cls: 'note'}, t('max_fs_note'))));
  },

  ampl(b) {
    const src = S.st.source, s = S.set;
    const refRow = row(t('ref_level'),
      btn('−5', () => { S.ui.ref -= 5; WF.key = ''; saveUi(); S.dirty = true; renderPanel(); }, 'sm'),
      numInput(S.ui.ref, (v) => { S.ui.ref = v; WF.key = ''; saveUi(); S.dirty = true; }),
      btn('+5', () => { S.ui.ref += 5; WF.key = ''; saveUi(); S.dirty = true; renderPanel(); }, 'sm'), 'dBm');
    const divs = h('div', {cls: 'seg'}, ...[1, 2, 5, 10, 20].map(v =>
      btn(String(v), () => { S.ui.div = v; WF.key = ''; saveUi(); S.dirty = true; renderPanel(); }, 'sm' + (S.ui.div === v ? ' on' : ''))));
    b.append(box(null, refRow, row(t('db_div'), divs),
      row(null, btn(t('auto_scale'), autoScale, 'prim'))));
    const gv = h('b', {cls: 'ltr', style: 'min-width:52px'}, s.gain.toFixed(0) + ' dB');
    const rng = h('input', {type: 'range', min: src.gain_min, max: src.gain_max, step: 1, value: s.gain});
    rng.addEventListener('input', () => { gv.textContent = rng.value + ' dB'; pushSet({gain: +rng.value}); });
    b.append(box(t('gain'), row(null, rng, gv),
      row(t('cal_offset'), numInput(s.cal_offset, (v) => pushSet({cal_offset: v}, true), {step: 0.1}), 'dB'),
      h('div', {cls: 'note'}, t('ampl_note'))));
  },

  traces(b) {
    const s = S.set;
    const bx = box(null);
    s.traces.forEach((m, i) => {
      bx.append(row(h('span', null, h('span', {style: `display:inline-block;width:14px;height:4px;background:${TRACE_COLORS[i]};vertical-align:middle;margin-inline-end:6px`}),
        `${t('trace')} ${i + 1}`),
      sel(S.st.trace_modes, m, (v) => { const tr = [...S.set.traces]; tr[i] = v; pushSet({traces: tr}, true); })));
    });
    b.append(bx);
    b.append(box(null,
      row(t('avg_count'), numInput(s.avg_count, (v) => pushSet({avg_count: Math.round(v)}, true), {min: 1, max: 1000, step: 1})),
      row(null, btn(t('k_restart'), () => doAct('restart'), 'prim')),
      h('div', {cls: 'note'}, t('restart_note'))));
  },

  display(b) {
    b.append(box(null,
      row(null, chk(S.ui.wf, (v) => { S.ui.wf = v; saveUi(); applyLayout(); }, t('waterfall'))),
      row(t('wf_rows'), sel([[80, '80'], [160, '160'], [320, '320'], [640, '640']], S.ui.wfRows, (v) => { S.ui.wfRows = +v; WF.key = ''; saveUi(); })),
      row(null, chk(S.ui.bands, (v) => { S.ui.bands = v; saveUi(); S.dirty = true; }, t('band_overlay'))),
      row(null, chk(S.ui.peakLabel, (v) => { S.ui.peakLabel = v; saveUi(); S.dirty = true; }, t('peak_label')))));
  },

  markers(b) {
    const seg = h('div', {cls: 'seg'});
    S.mk.forEach((m, i) => seg.append(btn((m.on ? '● ' : '○ ') + 'M' + (i + 1), () => { S.mAct = i; renderPanel(); S.dirty = true; }, 'sm' + (i === S.mAct ? ' on' : ''))));
    const m = S.mk[S.mAct];
    b.append(box(t('marker'), seg,
      row(null, chk(m.on, (v) => { m.on = v; if (v && !m.hz) m.hz = S.view.c; S.dirty = true; renderPanel(); }, t('on')),
        S.mAct > 0 ? chk(m.delta, (v) => { m.delta = v; S.dirty = true; }, t('delta')) : null),
      row(t('mk_freq'), hzInput(m.hz || S.view.c, (v) => { m.on = true; m.hz = v; S.dirty = true; renderPanel(); })),
      row(t('mk_trace'), sel(S.set.traces.map((md, i) => [i, `${t('trace')} ${i + 1} (${md})`]), S.ui.mkTrace,
        (v) => { S.ui.mkTrace = +v; saveUi(); S.dirty = true; }))));
    b.append(box(null, h('div', {cls: 'seg'},
      btn(t('mk_peak'), () => peakSearch(S.mAct), 'prim'),
      btn(t('mk_next'), () => nextPeak(0)),
      btn(t('mk_left'), () => nextPeak(-1)),
      btn(t('mk_right'), () => nextPeak(+1)),
      btn(t('mk_center'), () => { if (m.on) tune(m.hz, S.view.s, true); }),
      btn(t('mk_ref'), () => { const v = mkValue(m); if (isFinite(v)) { S.ui.ref = Math.ceil(v); WF.key = ''; saveUi(); S.dirty = true; } }),
      btn(t('mk_track'), () => { S.track = !S.track; renderPanel(); }, S.track ? 'on' : ''),
      btn(t('mk_alloff'), () => { S.mk.forEach(x => { x.on = false; x.delta = false; }); S.track = false; S.dirty = true; renderPanel(); }, 'danger'))));
    const tb = h('table', {cls: 'tbl'}, h('tr', null, h('th', null, ''), h('th', null, t('mk_freq')), h('th', null, 'dBm / Δ'), h('th', null, t('id_band'))));
    let any = false;
    const ref = S.mk[0], rv = mkValue(ref);
    S.mk.forEach((x, i) => {
      if (!x.on) return; any = true; const v = mkValue(x);
      const dl = x.delta && i > 0 && ref.on;
      tb.append(h('tr', {onclick: () => { S.mAct = i; renderPanel(); }},
        h('td', null, (dl ? 'D' : 'M') + (i + 1)),
        h('td', {cls: 'ltr'}, dl ? 'Δ ' + fmtShort(x.hz - ref.hz) : trimHz(x.hz)),
        h('td', {cls: 'ltr'}, isFinite(v) ? (dl ? fmtDb(v - rv) + ' dB' : fmtDbm(v)) : '—'),
        h('td', {cls: 'ltr'}, S.bands.filter(bb => x.hz >= bb.lo && x.hz <= bb.hi).map(bb => bb.name).join(' / '))));
    });
    b.append(box(t('mk_table'), any ? tb : h('div', {cls: 'note'}, t('mk_none'))));
  },

  signals(b) {
    b.append(box(null, h('div', {cls: 'seg'},
      btn(t('sig_refresh'), refreshSignals, 'prim'),
      chk(S.ui.sigAuto, (v) => { S.ui.sigAuto = v; saveUi(); }, t('sig_auto')),
      btn(t('sig_overview'), () => { const src = S.st.source; S.lastSpan = {...S.view};
        const tr = [...S.set.traces]; if (!tr.includes('Max Hold')) { tr[1] = 'Max Hold'; pushSet({traces: tr}, true); }
        tune((src.freq_min + src.freq_max) / 2, src.freq_max - src.freq_min, true); toast(t('sig_overview_note'), 5000); })),
      h('div', {cls: 'note'}, t('sig_tap'))));
    const list = h('div', {cls: 'list', id: 'sig-list'});
    b.append(list);
    fillSignals(list);
  },

  presets(b) {
    b.append(box(null, h('div', {cls: 'seg'},
      btn('＋ ' + t('pr_new'), () => editPreset(null), 'prim'),
      btn(t('pr_save_view'), () => editPreset({name: '', name_he: '', start: S.view.c - S.view.s / 2, stop: S.view.c + S.view.s / 2,
        rbw: S.set.rbw_auto ? null : S.set.rbw}))),
      h('div', {cls: 'note'}, t('pr_note'))));
    const list = h('div', {cls: 'list'});
    S.presets.forEach((p, i) => {
      list.append(h('div', {cls: 'li'},
        h('div', {cls: 't', onclick: () => applyPreset(p.id)},
          h('b', null, presetName(p)),
          h('small', {dir: 'ltr', style: 'unicode-bidi:isolate'}, `${trimHz(p.start)} – ${trimHz(p.stop)}` + (p.rbw ? `  ·  RBW ${fmtShort(p.rbw)}` : ''))),
        btn('▶', () => applyPreset(p.id), 'sm prim'),
        btn('✎', () => editPreset(p), 'sm'),
        btn('↑', () => presetOp('move', {id: p.id, by: -1}), 'sm'),
        btn('↓', () => presetOp('move', {id: p.id, by: 1}), 'sm'),
        btn('🗑', async () => { if (await confirmBox(t('pr_confirm_del'))) presetOp('delete', {id: p.id}); }, 'sm danger')));
    });
    b.append(list);
    b.append(h('div', {style: 'height:8px'}));
    b.append(box(null, h('div', {cls: 'seg'},
      btn(t('pr_export'), async () => { const r = await api('presets_export'); if (r.path) { toast(t('saved') + ': ' + r.path.split('/').pop()); shareFile(r.path); } }),
      btn(t('pr_import'), importPresets),
      btn(t('pr_reset'), async () => { if (await confirmBox(t('pr_confirm_reset'))) presetOp('reset', {}); }, 'danger'))));
  },

  radio(b) {
    const src = S.st.source;
    b.append(box(null,
      h('div', {cls: 'mono', style: 'white-space:pre-wrap'}, src.info || '—'),
      src.usb_speed != null ? row(t('usb_speed'), h('b', null, src.usb_speed >= 2 ? t('usb_ss') : t('usb_hs'))) : null,
      row(t('rx_port'), h('div', {cls: 'seg'}, ...[0, 1].map(p => btn('RX' + (p + 1), async () => { await api('port', {port: p}); S.set.port = p; renderPanel(); updateSourceChip(); }, 'sm' + ((S.set.port || 0) === p ? ' on' : ''))))),
      row(t('src_sel'), h('div', {cls: 'seg'},
        btn('bladeRF', connectRadio, 'sm' + (src.radio ? ' on' : '')),
        btn(t('src_sim'), async () => { await api('simulate'); await pollState(); renderPanel(); }, 'sm' + (src.sim ? ' on' : '')))),
      S.st.force_sim ? h('div', {cls: 'note warn'}, t('force_sim_note')) : null,
      h('div', {cls: 'note'}, t('radio_note')),
      src.error ? h('div', null, h('div', {cls: 'note warn'}, t('radio_err') + ':'), h('div', {cls: 'err ltr'}, src.error)) : null,
      src.native_log ? h('details', null, h('summary', {cls: 'note'}, 'libbladeRF log'), h('div', {cls: 'err ltr mono', style: 'font-size:11px'}, src.native_log)) : null));
    b.append(box(t('fpga'),
      h('div', {cls: 'note'}, src.fpga_file ? '✓ hostedxA4.rbf' : '— hostedxA4.rbf'),
      AND && AND.pickFpga ? btn(t('fpga_load'), () => AND.pickFpga()) : null,
      h('div', {cls: 'note'}, t('fpga_note'))));
    b.append(box(t('about'), h('div', {cls: 'note ltr', style: 'white-space:pre-wrap'},
      `bladeRF Workbench Mobile ${S.st.version}\nengine: ${S.st.core_version}`)));
  },

  capture(b) {
    b.append(box(null, h('div', {cls: 'seg'},
      btn(t('shot'), screenshot, 'prim'),
      btn(t('csv'), async () => { const r = await api('csv'); if (r.ok) { toast(t('saved') + ': ' + r.csv.split('/').pop()); renderPanel(); } else toast(r.error || 'error'); }, 'prim'))));
    const list = h('div', {cls: 'list'}, h('div', {cls: 'note'}, '…'));
    b.append(box(t('captures'), list));
    getJSON('/api/captures').then((files) => {
      list.textContent = '';
      if (!files.length) list.append(h('div', {cls: 'note'}, t('no_files')));
      for (const f of files.slice(0, 60)) {
        list.append(h('div', {cls: 'li'}, h('div', {cls: 't'}, h('b', {cls: 'ltr'}, f.name), h('small', null, (f.size / 1024).toFixed(0) + ' kB')),
          AND && AND.share ? btn(t('share'), () => shareFile(f.path), 'sm') : null));
      }
    });
  },

  settings(b) {
    b.append(box(t('language'), h('div', {cls: 'seg'},
      btn('עברית', () => setLang('he'), 'sm' + (S.ui.lang === 'he' ? ' on' : '')),
      btn('English', () => setLang('en'), 'sm' + (S.ui.lang === 'en' ? ' on' : '')))));
    if (!(typeof DESKTOP !== 'undefined' && DESKTOP)) b.append(box(t('orientation'), h('div', {cls: 'seg'},
      ...['auto', 'portrait', 'landscape'].map(o => btn(t('or_' + o), () => setOrient(o), 'sm' + (S.ui.orient === o ? ' on' : ''))))));
    if (AND && AND.keepScreenOn) b.append(box(null, chk(S.ui.keepOn, (v) => { S.ui.keepOn = v; saveUi(); AND.keepScreenOn(v); }, t('keep_on'))));
    b.append(box(null, h('div', {cls: 'note'}, t('gesture_note'))));
  },
};

// --------------------------------------------------------------- signals
let sigTimer = 0;
async function refreshSignals() {
  clearTimeout(sigTimer);
  try { S.sigs = await getJSON('/api/signals'); } catch (e) { return; }
  S.dirty = true;
  const list = $('sig-list'); if (list) fillSignals(list);
  if (S.panel === 'signals' && S.ui.sigAuto) sigTimer = setTimeout(refreshSignals, 2000);
}
function fillSignals(list) {
  list.textContent = '';
  const s = S.sigs; if (!s) return;
  if (s.floor != null) list.append(h('div', {cls: 'note'}, `${t('sig_floor')} `, h('b', {cls: 'ltr'}, fmtDbm(s.floor))));
  if (!s.items.length) list.append(h('div', {cls: 'note'}, t('sig_none')));
  const rbw = S.fr ? S.fr.h.rbw : 0;
  for (const it of s.items) {
    // each part isolated, so Hebrew words and "333 kHz" do not swap places
    const narrow = it.width <= 0 || (rbw && it.width <= 4 * rbw);
    const parts = [];
    if (narrow) parts.push(h('bdi', null, 'CW'));
    else {
      parts.push(h('bdi', null, it.tech || t('unknown')));
      parts.push(h('span', null, t('sig_width') + ' ', h('bdi', {dir: 'ltr'}, fmtShort(it.width))));
    }
    if (it.band) parts.push(h('bdi', {dir: 'ltr'}, it.band));
    const small = h('small');
    parts.forEach((p, i) => { if (i) small.append(' · '); small.append(p); });
    list.append(h('div', {cls: 'li sig', onclick: () => zoomToSignal(it)},
      h('div', {cls: 't'}, h('b', null, h('bdi', {dir: 'ltr'}, trimHz(it.hz))), small),
      h('span', {cls: 'lvl', dir: 'ltr'}, fmtDbm(it.dbm))));
  }
}
function zoomToSignal(it) {
  S.lastSpan = {...S.view};
  const w = Math.max(it.width * 3, 2e6, S.fr ? S.fr.h.rbw * 60 : 0);
  tune(it.width > 0 ? (it.lo + it.hi) / 2 : it.hz, Math.min(w, S.view.s), true);
  const m = S.mk[S.mAct]; m.on = true; m.hz = it.hz; S.dirty = true;
}

// --------------------------------------------------------------- dialogs
function modal(build) {
  return new Promise((resolve) => {
    const body = $('modal-body'); body.textContent = '';
    const close = (v) => { $('modal').classList.add('hidden'); resolve(v); };
    build(body, close);
    $('modal').classList.remove('hidden');
  });
}
function confirmBox(text) {
  return modal((b, close) => {
    b.append(h('div', {style: 'white-space:pre-wrap'}, text),
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close(false)), btn(t('yes'), () => close(true), 'prim')));
  });
}
async function presetOp(op, d) {
  const r = await api('preset', {op, ...d});
  if (r.presets) { S.presets = r.presets; renderPresetBar(); if (S.panel === 'presets') renderPanel(); }
}
function editPreset(p) {
  const isNew = !p || !p.id;
  const v = p ? {...p} : {name: '', name_he: '', start: S.view.c - S.view.s / 2, stop: S.view.c + S.view.s / 2};
  return modal((b, close) => {
    const nm = h('input', {type: 'text', value: v.name || '', dir: 'ltr'});
    const nh = h('input', {type: 'text', value: v.name_he || '', dir: 'rtl'});
    const st = h('input', {type: 'text', value: trimHz(v.start), cls: 'ltr', inputmode: 'decimal'});
    const sp = h('input', {type: 'text', value: trimHz(v.stop), cls: 'ltr', inputmode: 'decimal'});
    const rb = h('input', {type: 'text', value: v.rbw ? fmtShort(v.rbw) : '', cls: 'ltr', inputmode: 'decimal'});
    const gn = h('input', {type: 'number', value: v.gain ?? '', cls: 'ltr'});
    const err = h('div', {cls: 'err'});
    b.append(h('h3', null, isNew ? t('pr_new') : t('edit')),
      row(t('pr_name'), nm), row(t('pr_name_he'), nh), row(t('start'), st), row(t('stop'), sp),
      row(t('pr_rbw'), rb), row(t('pr_gain'), gn), h('div', {cls: 'note'}, t('hz_note')), err,
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close(false)), btn(t('save'), async () => {
        const a = parseHz(st.value), z = parseHz(sp.value);
        if (a == null || z == null || z <= a) { err.textContent = `${t('start')} / ${t('stop')} ?`; return; }
        const rbw = rb.value.trim() ? parseHz(rb.value.trim().match(/[a-z]/i) ? rb.value : rb.value + 'k') : null;
        const name = nm.value.trim() || nh.value.trim() || `${fmtShort(a)}–${fmtShort(z)}`;
        const d = {name, name_he: nh.value.trim(), start: a, stop: z, rbw: rbw || '', gain: gn.value.trim() === '' ? '' : +gn.value};
        if (isNew) await presetOp('add', d); else await presetOp('update', {id: p.id, ...d});
        close(true);
      }, 'prim')));
  });
}
function importPresets() {
  if (AND && AND.pickPresets) { AND.pickPresets(); return; }
  modal((b, close) => {
    const ta = h('textarea', {rows: 8, style: 'width:100%;background:#1b2129;color:#eaf2f8;border:1px solid #2c3742;border-radius:5px'});
    b.append(h('h3', null, t('pr_import')), h('div', {cls: 'note'}, t('pr_import_paste')), ta,
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close()), btn(t('pr_import'), async () => {
        const r = await api('presets_import', {text: ta.value});
        if (r.ok) { S.presets = r.presets; renderPresetBar(); renderPanel(); toast(`${r.n} ${t('pr_imported')}`); close(); }
        else toast(r.error || 'error');
      }, 'prim')));
  });
}
// Kotlin calls this after the user picked a presets file
window.onPresetsPicked = async (text) => {
  const r = await api('presets_import', {text});
  if (r.ok) { S.presets = r.presets; renderPresetBar(); if (S.panel === 'presets') renderPanel(); toast(`${r.n} ${t('pr_imported')}`); }
  else toast(r.error || 'error');
};

// ---------------------------------------------------------------- actions
async function doAct(a) {
  if (a === 'savept') { if (window.mapSaveDialog) window.mapSaveDialog(); return; }
  if (a === 'peak') { peakSearch(S.mAct); return; }
  if (a === 'next') { nextPeak(0); return; }
  if (a === 'restart') { await api('restart'); WF.key = ''; toast(t('k_restart')); return; }
  if (a === 'single') {
    if (S.set.running) { await api('single'); S.set.running = false; }
    else { await api('set', {running: true}); S.set.running = true; }
    updateSourceChip(); updateStatus(); return;
  }
}
async function connectRadio() {
  if (AND && AND.requestUsb) { AND.requestUsb(); toast('USB…'); return; }
  const r = await api('connect'); await pollState(); if (S.panel) renderPanel();
  if (!r.radio) toast(t('radio_err'));
}
window.onUsbResult = async () => { await pollState(); if (S.panel === 'radio') renderPanel(); };

function shareFile(path) { if (AND && AND.share) AND.share(path); }

async function screenshot() {
  const W = spec.width, H1 = spec.height, H2 = S.ui.wf ? wfc.height : 0, head = Math.round(26 * dpr);
  const c = document.createElement('canvas'); c.width = W; c.height = head + H1 + H2;
  const x = c.getContext('2d');
  x.fillStyle = '#12161a'; x.fillRect(0, 0, W, c.height);
  x.fillStyle = '#ffd24a'; x.font = `bold ${Math.round(13 * dpr)}px system-ui`; x.textBaseline = 'middle';
  const rbw = S.fr ? fmtShort(S.fr.h.rbw) : '';
  x.fillText(`Center ${trimHz(S.view.c)}   Span ${fmtShort(S.view.s)}   RBW ${rbw}   ${new Date().toLocaleString()}`, 8 * dpr, head / 2);
  x.drawImage(spec, 0, head);
  if (H2) x.drawImage(wfc, 0, head + H1);
  // marker readout text (it is HTML over the canvas, so paint it in)
  x.fillStyle = '#fff'; x.font = `${Math.round(12 * dpr)}px ui-monospace,monospace`; x.textAlign = 'right'; x.textBaseline = 'top';
  [...$('mk-readout').children].forEach((el, i) => x.fillText(el.textContent, W - 8 * dpr, head + (6 + i * 16) * dpr));
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  const d0 = new Date(), z = (n) => String(n).padStart(2, '0');
  const name = `Spectrum ${d0.getFullYear()}${z(d0.getMonth() + 1)}${z(d0.getDate())}_${z(d0.getHours())}${z(d0.getMinutes())}${z(d0.getSeconds())}.png`;
  const r = await fetch('/api/blob?name=' + encodeURIComponent(name), {method: 'POST', body: blob});
  const j = await r.json();
  if (j.ok) { toast(t('saved') + ': ' + name); if (S.panel === 'capture') renderPanel(); }
}

function autoScale() {
  const d = traceForMarkers(); if (!d) return;
  let mx = -Infinity, mn = Infinity;
  for (const v of d) if (isFinite(v)) { if (v > mx) mx = v; if (v < mn) mn = v; }
  if (!isFinite(mx)) return;
  const div = [1, 2, 5, 10, 20].find(v => v * N_DIV >= (mx - mn) + 15) || 20;
  S.ui.div = div; S.ui.ref = Math.ceil((mx + 5) / 5) * 5; WF.key = ''; saveUi(); S.dirty = true;
  if (S.panel === 'ampl') renderPanel();
}

// ------------------------------------------------------- language/layout
function applyLang() {
  const he = S.ui.lang === 'he';
  document.documentElement.lang = he ? 'he' : 'en';
  document.documentElement.dir = he ? 'rtl' : 'ltr';
  for (const e of document.querySelectorAll('[data-i18n]')) e.textContent = t(e.dataset.i18n);
  for (const e of document.querySelectorAll('[data-i18n-title]')) e.title = t(e.dataset.i18nTitle);
  $('b-lang').textContent = he ? 'EN' : 'עב';
  renderPresetBar();
  if (S.panel) renderPanel();
  if (S.st) { updateSourceChip(); updateStatus(); }
  if (window.mapLangChanged) window.mapLangChanged();
}
function setLang(l) { S.ui.lang = l; saveUi(); applyLang(); }
function setOrient(o) {
  S.ui.orient = o; saveUi();
  if (AND && AND.setOrientation) AND.setOrientation(o);
  toast(t('rot_' + o));
  if (S.panel === 'settings') renderPanel();
}
function applyLayout() {
  const land = window.innerWidth > window.innerHeight;
  document.body.classList.toggle('landscape', land);
  document.body.classList.toggle('portrait', !land);
  $('wf-wrap').classList.toggle('off', !S.ui.wf);
  requestAnimationFrame(() => {
    // the open panel never covers the soft keys: portrait sits above them,
    // landscape beside them
    const keys = $('keys'), st = $('status');
    document.body.style.setProperty('--kh', land ? '0px' : (keys.offsetHeight + st.offsetHeight) + 'px');
    document.body.style.setProperty('--kw', land ? keys.offsetWidth + 'px' : '0px');
    resizeCanvases();
  });
}
window.addEventListener('resize', applyLayout);
new ResizeObserver(() => resizeCanvases()).observe($('plots'));

// Android back button: close what is open first, leave the app last.
window.onBack = () => {
  if (!$('modal').classList.contains('hidden')) { $('modal').classList.add('hidden'); return true; }
  if (S.panel) { closePanel(); return true; }
  return false;
};
// Kotlin -> page messages (USB permission, file import results)
window.nativeToast = (m) => toast(m, 4000);

// ---------------------------------------------------------------- wiring
for (const k of document.querySelectorAll('.key[data-panel], .ro[data-panel]')) k.addEventListener('click', () => openPanel(k.dataset.panel));
for (const k of document.querySelectorAll('.key[data-act]')) k.addEventListener('click', () => doAct(k.dataset.act));
$('b-lang').onclick = () => setLang(S.ui.lang === 'he' ? 'en' : 'he');
$('b-rot').onclick = () => setOrient({auto: 'portrait', portrait: 'landscape', landscape: 'auto'}[S.ui.orient] || 'auto');
$('b-run').onclick = async () => { const run = !S.set.running; await api('set', {running: run}); S.set.running = run; updateSourceChip(); updateStatus(); };
$('b-source').onclick = () => openPanel('radio');
$('modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('modal').classList.add('hidden'); });

// long-press on a key shows what it does (no hover on a phone)
let tipT = 0;
document.addEventListener('pointerdown', (e) => {
  const el = e.target.closest('[title]'); if (!el) return;
  tipT = setTimeout(() => {
    const tip = $('tip'); tip.textContent = el.title; tip.classList.remove('hidden');
    const r = el.getBoundingClientRect(); tip.style.left = Math.max(6, Math.min(r.left, innerWidth - 260)) + 'px'; tip.style.top = (r.bottom + 6) + 'px';
    setTimeout(() => tip.classList.add('hidden'), 2500);
  }, 600);
});
document.addEventListener('pointerup', () => clearTimeout(tipT));

async function pollState() {
  try {
    const st = await getJSON('/api/state');
    S.st = st;
    if (!S.set || !Object.keys(pendingSet).length) S.set = st.settings;
    if (JSON.stringify(st.presets) !== JSON.stringify(S.presets)) { S.presets = st.presets; renderPresetBar(); }
    updateSourceChip(); updateStatus();
  } catch (e) { /* server restarting */ }
}

async function boot() {
  let st = null;
  for (let i = 0; i < 100 && !st; i++) { try { st = await getJSON('/api/state'); } catch (e) { await sleep(200); } }
  S.st = st; S.set = st.settings; S.presets = st.presets;
  Object.assign(S.ui, st.settings.ui || {});
  S.view = {c: st.settings.center, s: st.settings.span};
  try { S.bands = await getJSON('/api/bands'); } catch (e) { S.bands = []; }
  if (AND && AND.setOrientation) AND.setOrientation(S.ui.orient);
  if (AND && AND.keepScreenOn) AND.keepScreenOn(S.ui.keepOn !== false);
  applyLang(); applyLayout(); updateSourceChip(); updateStatus();
  requestAnimationFrame(frameLoopDraw);
  pump();
  setInterval(pollState, 1000);
}
boot();
