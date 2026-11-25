/* bladeRF Workbench Mobile -- the Map tab.
 *
 * A survey's points drawn where they were taken, over (a) an image of the
 * area the user uploaded and positioned, (b) OpenStreetMap tiles when online,
 * or (c) a plain metric grid. Coordinates are Web Mercator metres throughout;
 * an uploaded image is tied to them by 2 points (similarity transform) or
 * 3+ points (least-squares affine).
 *
 * Shares globals with app.js: S, t, h, api, getJSON, toast, PANELS,
 * openPanel, drawSpectrumOn, fmtShort, trimHz, fmtDbm, ...
 */
'use strict';

const RM = 6378137;
const SIM_HOME = [32.0853, 34.7818];
const mercX = (lon) => RM * lon * Math.PI / 180;
const mercY = (lat) => RM * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
const lonOf = (x) => x / RM * 180 / Math.PI;
const latOf = (y) => (2 * Math.atan(Math.exp(y / RM)) - Math.PI / 2) * 180 / Math.PI;

const MV = {
  on: false, sid: '', sv: null, list: [],
  v: {x: mercX(SIM_HOME[1]), y: mercY(SIM_HOME[0]), res: 2},   // res = metres per CSS px
  layer: 'auto', opacity: 0.85, img: null, imgKey: '', A: null, Ai: null,
  sel: null, placing: false, calib: null, follow: true, fitted: false,
  loc: null, tiles: new Map(), dirty: true, range: null,
};
const mapCv = $('map'), miniCv = $('mini');

// ------------------------------------------------------------------ math
function affineFrom(ctrl) {
  // image px (x right, y down) -> mercator metres
  const P = ctrl.map(c => ({px: c.px, py: c.py, X: mercX(c.lon), Y: mercY(c.lat)}));
  if (P.length === 2) {
    const [a, b] = P;
    const zx = b.px - a.px, zy = -(b.py - a.py), wx = b.X - a.X, wy = b.Y - a.Y;
    const den = zx * zx + zy * zy; if (den < 1e-9) return null;
    const ar = (wx * zx + wy * zy) / den, ai = (wy * zx - wx * zy) / den;
    // w = (ar + i ai) * (px - i py) + B
    const A = [ar, ai, 0, ai, -ar, 0];
    A[2] = a.X - (A[0] * a.px + A[1] * a.py); A[5] = a.Y - (A[3] * a.px + A[4] * a.py);
    return A;
  }
  if (P.length >= 3) {
    const solve = (key) => {
      let m = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], r = [0, 0, 0];
      for (const q of P) { const v = [q.px, q.py, 1]; for (let i = 0; i < 3; i++) { r[i] += v[i] * q[key]; for (let j = 0; j < 3; j++) m[i][j] += v[i] * v[j]; } }
      return solve3(m, r);
    };
    const X = solve('X'), Y = solve('Y'); if (!X || !Y) return null;
    return [X[0], X[1], X[2], Y[0], Y[1], Y[2]];
  }
  return null;
}
function solve3(m, r) {
  const d = (a) => a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]);
  const D = d(m); if (Math.abs(D) < 1e-12) return null;
  const out = [];
  for (let k = 0; k < 3; k++) { const mk = m.map((row, i) => row.map((v, j) => j === k ? r[i] : v)); out.push(d(mk) / D); }
  return out;
}
function invAffine(A) {
  const [a, b, c, d, e, f] = A, det = a * e - b * d; if (Math.abs(det) < 1e-18) return null;
  return [e / det, -b / det, (b * f - c * e) / det, -d / det, a / det, (c * d - a * f) / det];
}
const applyA = (A, x, y) => [A[0] * x + A[1] * y + A[2], A[3] * x + A[4] * y + A[5]];

function mapSize() { return {W: mapCv.width / dpr, H: mapCv.height / dpr}; }
function toScr(X, Y) { const {W, H} = mapSize(); return [W / 2 + (X - MV.v.x) / MV.v.res, H / 2 - (Y - MV.v.y) / MV.v.res]; }
function fromScr(sx, sy) { const {W, H} = mapSize(); return [MV.v.x + (sx - W / 2) * MV.v.res, MV.v.y - (sy - H / 2) * MV.v.res]; }

// image-only mode: the base image is shown in its own pixels (calibration,
// or an image nobody positioned yet). World = (px, -py), res = image px per css px.
function imageMode() { return !!(MV.img && (!MV.A || MV.calib)); }

// ------------------------------------------------------------------- data
async function loadSurveys() {
  const r = await getJSON('/api/surveys');
  MV.list = r.list;
  let sid = (S.st && S.st.survey) || r.current || (r.list[0] && r.list[0].id) || '';
  if (!sid) { const s = await api('survey', {op: 'create', name: ''}); sid = s.id; MV.list = (await getJSON('/api/surveys')).list; }
  await loadSurvey(sid);
}
async function loadSurvey(sid) {
  MV.sid = sid;
  MV.sv = await getJSON('/api/survey?id=' + encodeURIComponent(sid));
  $('m-survey-name').textContent = MV.sv.name + `  ·  ${MV.sv.points.length}`;
  const b = MV.sv.base;
  const key = b ? sid + '/' + b.file : '';
  if (key !== MV.imgKey) {
    MV.imgKey = key; MV.img = null;
    if (b) {
      // waited for, so whoever called loadSurvey() (calibration) has the image
      await new Promise((res) => {
        const im = new Image();
        im.onload = () => { MV.img = im; res(); };
        im.onerror = () => res();
        im.src = `/api/file?sid=${encodeURIComponent(sid)}&name=${encodeURIComponent(b.file)}`;
      });
    }
  }
  computeA();
  if (!MV.fitted) fitView();
  MV.dirty = true; updateLegend(); updateTrackBtn();
}
function computeA() {
  const b = MV.sv && MV.sv.base;
  MV.A = b && b.ctrl && b.ctrl.length >= 2 ? affineFrom(b.ctrl) : null;
  MV.Ai = MV.A ? invAffine(MV.A) : null;
}
function geoPoints() { return (MV.sv ? MV.sv.points : []).filter(p => p.lat != null && p.lon != null); }

function fitView() {
  const {W, H} = mapSize(); if (W < 20) return;
  let xs = [], ys = [];
  if (imageMode()) {
    MV.v = {x: MV.img.width / 2, y: -MV.img.height / 2, res: Math.max(MV.img.width / W, MV.img.height / H) * 1.05};
    MV.fitted = true; MV.follow = false; return;
  }
  if (MV.img && MV.A) for (const [px, py] of [[0, 0], [MV.img.width, 0], [0, MV.img.height], [MV.img.width, MV.img.height]]) { const [X, Y] = applyA(MV.A, px, py); xs.push(X); ys.push(Y); }
  for (const p of geoPoints()) { xs.push(mercX(p.lon)); ys.push(mercY(p.lat)); }
  if (!xs.length && MV.loc) { xs.push(mercX(MV.loc.lon)); ys.push(mercY(MV.loc.lat)); }
  if (!xs.length) return;
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  MV.v = {x: (x0 + x1) / 2, y: (y0 + y1) / 2, res: Math.max(0.3, Math.max((x1 - x0) / W, (y1 - y0) / H) * 1.25)};
  MV.fitted = true; MV.dirty = true;
}

// --------------------------------------------------------------- colours
function ptValue(p) {
  const kind = (MV.sv && MV.sv.metric && MV.sv.metric.kind) || 'band';
  const v = kind === 'peak' ? p.peak_dbm : p.band_dbm;
  return v == null ? NaN : v;
}
function valueRange() {
  if (MV.range) return MV.range;
  const vs = geoPoints().map(ptValue).filter(isFinite);
  if (!vs.length) return [-90, -30];
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (hi - lo < 6) { const m = (hi + lo) / 2; lo = m - 3; hi = m + 3; }
  return [Math.floor(lo), Math.ceil(hi)];
}
// weak = blue, strong = red: the convention of every coverage map
const CSTOPS = [[0, [40, 60, 220]], [0.25, [0, 170, 255]], [0.5, [60, 220, 90]], [0.75, [255, 210, 40]], [1, [255, 50, 40]]];
function colorOf(v, rng) {
  if (!isFinite(v)) return '#888';
  const x = Math.min(1, Math.max(0, (v - rng[0]) / (rng[1] - rng[0] || 1)));
  let k = 0; while (k < CSTOPS.length - 2 && x > CSTOPS[k + 1][0]) k++;
  const [p0, c0] = CSTOPS[k], [p1, c1] = CSTOPS[k + 1], u = (x - p0) / (p1 - p0);
  const c = c0.map((a, i) => Math.round(a + (c1[i] - a) * u));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function updateLegend() {
  const el = $('m-legend'); if (!MV.sv) return;
  const rng = valueRange(), kind = (MV.sv.metric && MV.sv.metric.kind) || 'band';
  const grad = CSTOPS.map(([p]) => `${colorOf(rng[0] + p * (rng[1] - rng[0]), rng)} ${p * 100}%`).join(',');
  const m = MV.sv.metric || {};
  const band = kind === 'band' && m.lo && m.hi ? ` <span class="ltr">${fmtShort(m.lo)}–${fmtShort(m.hi)}</span>` : '';
  el.innerHTML = `${kind === 'peak' ? t('peak') : t('band_power')}${band}<div class="bar" style="background:linear-gradient(90deg,${grad})"></div>` +
    `<span class="ltr">${rng[0]} … ${rng[1]} dBm</span>`;
}

// ------------------------------------------------------------------ draw
function drawMap() {
  const ctx = mapCv.getContext('2d'), {W, H} = mapSize();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.direction = 'ltr';
  ctx.fillStyle = '#0b1015'; ctx.fillRect(0, 0, W, H);
  const layer = MV.layer === 'auto' ? (MV.img ? 'image' : 'grid') : MV.layer;

  if (imageMode()) {
    // the image in its own pixels: calibration, or an unpositioned image
    const [sx, sy] = toScr(0, 0);
    ctx.save(); ctx.setTransform(dpr / MV.v.res, 0, 0, dpr / MV.v.res, sx * dpr, sy * dpr);
    ctx.drawImage(MV.img, 0, 0); ctx.restore();
    if (MV.calib) for (const [i, c] of MV.calib.ctrl.entries()) {
      const [x, y] = toScr(c.px, -c.py); crosshair(ctx, x, y, '#ffd24a', String(i + 1));
    }
    if (MV.calib && MV.calib.pending) { const [x, y] = toScr(MV.calib.pending.px, -MV.calib.pending.py); crosshair(ctx, x, y, '#ff453a', '?'); }
    return;
  }

  if (layer === 'osm') drawTiles(ctx, W, H);
  if (layer === 'grid' || layer === 'osm' && !navigator.onLine) drawGrid(ctx, W, H);
  if (MV.img && MV.A && (layer === 'image' || layer === 'osm')) {
    const A = MV.A, r = MV.v.res;
    ctx.save(); ctx.globalAlpha = layer === 'osm' ? MV.opacity : 1;
    ctx.setTransform(dpr * A[0] / r, -dpr * A[3] / r, dpr * A[1] / r, -dpr * A[4] / r,
      dpr * ((A[2] - MV.v.x) / r + W / 2), dpr * (-(A[5] - MV.v.y) / r + H / 2));
    ctx.drawImage(MV.img, 0, 0); ctx.restore();
  }
  if (layer === 'image' && !MV.img) drawGrid(ctx, W, H);
  if (layer !== 'grid') drawScale(ctx, W, H);

  // walks, then points
  const rng = valueRange(), pts = geoPoints();
  ctx.lineWidth = 2; ctx.strokeStyle = 'rgba(200,210,220,0.45)';
  for (const tr of (MV.sv ? MV.sv.tracks : [])) {
    const q = pts.filter(p => p.track === tr.id); if (q.length < 2) continue;
    ctx.beginPath(); q.forEach((p, i) => { const [x, y] = toScr(mercX(p.lon), mercY(p.lat)); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
  }
  for (const p of pts) {
    const [x, y] = toScr(mercX(p.lon), mercY(p.lat)); if (x < -20 || y < -20 || x > W + 20 || y > H + 20) continue;
    const snap = p.kind === 'snap', r = snap ? 8 : 5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fillStyle = colorOf(ptValue(p), rng); ctx.fill();
    ctx.lineWidth = snap ? 2 : 1; ctx.strokeStyle = p.sim ? '#ffb340' : (snap ? '#fff' : 'rgba(0,0,0,0.6)');
    if (p.sim) ctx.setLineDash([3, 2]); ctx.stroke(); ctx.setLineDash([]);
    if (MV.sel && MV.sel.id === p.id) { ctx.beginPath(); ctx.arc(x, y, r + 5, 0, 2 * Math.PI); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke(); }
    if (snap && p.name) { ctx.font = 'bold 11px system-ui'; ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.strokeStyle = '#000'; ctx.lineWidth = 3; ctx.strokeText(p.name, x + 11, y); ctx.fillText(p.name, x + 11, y); }
  }
  // me
  if (MV.loc) {
    const [x, y] = toScr(mercX(MV.loc.lon), mercY(MV.loc.lat));
    if (MV.loc.acc) { ctx.beginPath(); ctx.arc(x, y, Math.max(6, MV.loc.acc / Math.cos(MV.loc.lat * Math.PI / 180) / MV.v.res), 0, 2 * Math.PI);
      ctx.fillStyle = 'rgba(74,198,255,0.12)'; ctx.fill(); ctx.strokeStyle = 'rgba(74,198,255,0.5)'; ctx.lineWidth = 1; ctx.stroke(); }
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI); ctx.fillStyle = MV.loc.src === 'sim' ? '#ffb340' : '#4ac6ff'; ctx.fill();
    ctx.lineWidth = 2.5; ctx.strokeStyle = '#fff'; ctx.stroke();
  }
  if (MV.img && !MV.A) banner(t('need_calib'));
}
function crosshair(ctx, x, y, col, lab) {
  ctx.strokeStyle = col; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x - 14, y); ctx.lineTo(x + 14, y); ctx.moveTo(x, y - 14); ctx.lineTo(x, y + 14); ctx.stroke();
  ctx.beginPath(); ctx.arc(x, y, 9, 0, 2 * Math.PI); ctx.stroke();
  ctx.fillStyle = col; ctx.font = 'bold 13px system-ui'; ctx.fillText(lab, x + 12, y - 12);
}
function niceMeters(m) { const p = Math.pow(10, Math.floor(Math.log10(m))); return [1, 2, 5, 10].map(k => k * p).find(v => v >= m) || 10 * p; }
function groundRes() { const lat = latOf(MV.v.y); return MV.v.res * Math.cos(lat * Math.PI / 180); }   // real metres per css px
function drawGrid(ctx, W, H) {
  const gr = groundRes(), step = niceMeters(gr * 80), stepM = step / Math.cos(latOf(MV.v.y) * Math.PI / 180);
  ctx.strokeStyle = 'rgba(143,166,187,0.16)'; ctx.lineWidth = 1; ctx.beginPath();
  const [X0, Y1] = fromScr(0, 0), [X1, Y0] = fromScr(W, H);
  for (let X = Math.ceil(X0 / stepM) * stepM; X < X1; X += stepM) { const [x] = toScr(X, 0); ctx.moveTo(x, 0); ctx.lineTo(x, H); }
  for (let Y = Math.ceil(Y0 / stepM) * stepM; Y < Y1; Y += stepM) { const [, y] = toScr(0, Y); ctx.moveTo(0, y); ctx.lineTo(W, y); }
  ctx.stroke(); drawScale(ctx, W, H);
}
function drawScale(ctx, W, H) {
  const gr = groundRes(), m = niceMeters(gr * 90), px = m / gr;
  const x = 12, y = H - 60;
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, y - 5); ctx.lineTo(x, y); ctx.lineTo(x + px, y); ctx.lineTo(x + px, y - 5); ctx.stroke();
  ctx.fillStyle = '#fff'; ctx.font = '11px system-ui'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.fillText(m >= 1000 ? (m / 1000) + ' km' : m + ' m', x + 3, y - 3);
  ctx.textAlign = 'center'; ctx.fillText('N ↑', W - 22, 62);
}
function drawTiles(ctx, W, H) {
  const z = Math.max(0, Math.min(19, Math.round(Math.log2(156543.03392 / MV.v.res))));
  const tileM = 2 * Math.PI * RM / Math.pow(2, z), n = Math.pow(2, z);
  const [X0, Y1] = fromScr(0, 0), [X1, Y0] = fromScr(W, H), half = Math.PI * RM;
  const tx0 = Math.floor((X0 + half) / tileM), tx1 = Math.floor((X1 + half) / tileM);
  const ty0 = Math.floor((half - Y1) / tileM), ty1 = Math.floor((half - Y0) / tileM);
  for (let ty = Math.max(0, ty0); ty <= Math.min(n - 1, ty1); ty++) for (let tx = tx0; tx <= tx1; tx++) {
    const wx = ((tx % n) + n) % n, key = `${z}/${wx}/${ty}`;
    let im = MV.tiles.get(key);
    if (!im) {
      im = new Image(); im.crossOrigin = 'anonymous'; im.onload = () => { MV.dirty = true; };
      im.src = `https://tile.openstreetmap.org/${key}.png`; MV.tiles.set(key, im);
      if (MV.tiles.size > 400) MV.tiles.delete(MV.tiles.keys().next().value);
    }
    if (im.complete && im.naturalWidth) {
      const [x, y] = toScr(tx * tileM - half, half - ty * tileM), s = tileM / MV.v.res;
      ctx.drawImage(im, x, y, s + 0.5, s + 0.5);
    }
  }
  ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(W - 170, H - 14, 170, 14);
  ctx.fillStyle = '#fff'; ctx.font = '10px system-ui'; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
  ctx.fillText('© OpenStreetMap contributors', W - 4, H - 2);
}
let bannerTimer = 0;
function banner(txt, ms) {
  const e = $('m-banner'); e.textContent = txt; e.classList.remove('hidden');
  clearTimeout(bannerTimer); if (ms) bannerTimer = setTimeout(() => e.classList.add('hidden'), ms);
}
function hideBanner() { $('m-banner').classList.add('hidden'); }

// mini live spectrum under the map
function drawMini() {
  const fr = S.fr; if (!fr) return;
  const P = {...liveP(), bands: false, compact: true, freqs: fr.freqs, tr: fr.tr, markers: liveMarkers(), font: 9};
  P.watermark = isSim() ? t('src_sim').toUpperCase() : '';
  const m = MV.sv && MV.sv.metric;
  if (m && m.lo && m.hi) P.shade = [[m.lo, m.hi, 'rgba(124,255,107,0.10)']];
  drawSpectrumOn(miniCv, P);
  const h0 = fr.h;
  $('mini-ro').innerHTML = (h0.band_dbm != null ? `${t('band_power')} <b><bdi dir="ltr">${fmtDbm(h0.band_dbm)}</bdi></b><br>` : '') +
    (h0.peak_dbm != null ? `${t('peak')} <bdi dir="ltr">${fmtDbm(h0.peak_dbm)} @ ${fmtShort(h0.peak_hz)}</bdi>` : '');
}

function mapLoop() {
  if (MV.on) {
    if (MV.dirty) { MV.dirty = false; drawMap(); }
    if (S.newMini) { S.newMini = false; drawMini(); }
  }
  requestAnimationFrame(mapLoop);
}

// ------------------------------------------------------------- location
async function pollLocation() {
  if (!MV.on) return;
  try {
    const loc = await getJSON('/api/location');
    MV.loc = loc;
    const g = $('m-gps');
    g.className = 'mchip gps ' + (!loc ? 'none' : loc.src === 'sim' ? 'sim' : loc.age > 15 ? 'none' : (loc.acc || 99) > 25 ? 'weak' : 'ok');
    g.textContent = !loc ? t('gps_none') : loc.src === 'sim' ? t('gps_sim') :
      (loc.age > 15 ? t('gps_wait') : `GPS ±${Math.round(loc.acc || 0)} m`);
    if (loc && MV.follow && !imageMode()) { MV.v.x = mercX(loc.lon); MV.v.y = mercY(loc.lat); if (!MV.fitted) { MV.v.res = 1.5; MV.fitted = true; } }
    MV.dirty = true;
    const tr = S.st && S.st.track;
    if (tr && MV.sv && tr.n !== MV._lastN) { MV._lastN = tr.n; loadSurvey(MV.sid); }
  } catch (e) { /* ignore */ }
}
let geoWatch = null;
function startLocation() {
  if (AND && AND.startLocation) { AND.startLocation(); return; }
  if (geoWatch == null && navigator.geolocation) {
    geoWatch = navigator.geolocation.watchPosition(
      (p) => api('location', {lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy, src: 'browser'}),
      () => {}, {enableHighAccuracy: true, maximumAge: 2000});
  }
}

// --------------------------------------------------------------- gestures
const mp = new Map(); let mg = null;
mapCv.addEventListener('pointerdown', (e) => {
  mapCv.setPointerCapture(e.pointerId); mp.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
  if (mp.size === 1) mg = {kind: 'tap', x0: e.offsetX, y0: e.offsetY, vx: MV.v.x, vy: MV.v.y};
  else if (mp.size === 2) {
    const [a, b] = [...mp.values()];
    mg = {kind: 'pinch', d0: Math.max(10, Math.hypot(a.x - b.x, a.y - b.y)), res0: MV.v.res, anchor: fromScr((a.x + b.x) / 2, (a.y + b.y) / 2)};
  }
});
mapCv.addEventListener('pointermove', (e) => {
  if (!mp.has(e.pointerId) || !mg) return;
  mp.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
  if (mg.kind === 'tap' && Math.hypot(e.offsetX - mg.x0, e.offsetY - mg.y0) > 8) { mg.kind = 'pan'; MV.follow = false; }
  if (mg.kind === 'pan') { MV.v.x = mg.vx - (e.offsetX - mg.x0) * MV.v.res; MV.v.y = mg.vy + (e.offsetY - mg.y0) * MV.v.res; MV.dirty = true; }
  if (mg.kind === 'pinch' && mp.size === 2) {
    const [a, b] = [...mp.values()], d = Math.max(10, Math.hypot(a.x - b.x, a.y - b.y));
    MV.v.res = Math.min(5000, Math.max(0.02, mg.res0 * mg.d0 / d));
    const [ax, ay] = fromScr((a.x + b.x) / 2, (a.y + b.y) / 2);
    MV.v.x += mg.anchor[0] - ax; MV.v.y += mg.anchor[1] - ay; MV.follow = false; MV.dirty = true;
  }
});
function mapUp(e) {
  mp.delete(e.pointerId);
  if (mg && mg.kind === 'tap' && mp.size === 0) mapTap(e.offsetX, e.offsetY);
  if (mp.size === 0) mg = null;
}
mapCv.addEventListener('pointerup', mapUp);
mapCv.addEventListener('pointercancel', mapUp);
mapCv.addEventListener('wheel', (e) => {
  e.preventDefault(); const k = Math.pow(1.25, Math.sign(e.deltaY)), [ax, ay] = fromScr(e.offsetX, e.offsetY);
  MV.v.res = Math.min(5000, Math.max(0.02, MV.v.res * k));
  const [bx, by] = fromScr(e.offsetX, e.offsetY); MV.v.x += ax - bx; MV.v.y += ay - by; MV.follow = false; MV.dirty = true;
}, {passive: false});

function mapTap(x, y) {
  const [X, Y] = fromScr(x, y);
  if (MV.calib) { MV.calib.pending = {px: X, py: -Y}; MV.dirty = true; askCalibCoords(); return; }
  if (imageMode()) return;
  if (MV.placing) { MV.placing = false; $('m-place').classList.remove('on'); hideBanner(); saveDialog({lat: latOf(Y), lon: lonOf(X)}); return; }
  if (MV.moving) { const p = MV.moving; MV.moving = null; hideBanner();
    api('point_op', {sid: MV.sid, id: p.id, what: 'move', lat: latOf(Y), lon: lonOf(X)}).then(() => loadSurvey(MV.sid)); return; }
  let best = null, bd = 16;
  for (const p of geoPoints()) {
    const [px, py] = toScr(mercX(p.lon), mercY(p.lat)), d = Math.hypot(px - x, py - y);
    if (d < bd) { bd = d; best = p; }
  }
  if (best) { MV.sel = best; MV.dirty = true; if (S.panel === 'point') renderPanel(); else openPanel('point'); }
}

// ------------------------------------------------------------ calibration
function startCalib() {
  if (!MV.img) { toast(t('upload_image')); return; }
  closePanel();
  MV.calib = {ctrl: [], pending: null}; MV.fitted = false; fitView(); MV.dirty = true;
  banner(t('calib_tap').replace('{n}', '1'));
}
function parseLatLon(s) {
  const m = String(s).match(/(-?\d+(?:\.\d+)?)[,\s;]+(-?\d+(?:\.\d+)?)/);
  if (!m) return null;
  const lat = +m[1], lon = +m[2];
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? {lat, lon} : null;
}
function askCalibCoords() {
  const c = MV.calib, n = c.ctrl.length + 1;
  modal((b, close) => {
    const inp = h('input', {type: 'text', cls: 'ltr', placeholder: '32.0853, 34.7818', style: 'width:100%'});
    const err = h('div', {cls: 'err'});
    const add = (ll) => {
      c.ctrl.push({px: c.pending.px, py: c.pending.py, lat: ll.lat, lon: ll.lon}); c.pending = null; close(true); MV.dirty = true;
      if (c.ctrl.length >= 2) calibNext(); else banner(t('calib_tap').replace('{n}', String(c.ctrl.length + 1)));
    };
    b.append(h('h3', null, `${t('calibrate')} ${n}`), h('div', {cls: 'note'}, t('calib_coords')), inp,
      h('div', {cls: 'note'}, t('coords_hint')), err,
      h('div', {cls: 'modal-actions'},
        btn(t('cancel'), () => { c.pending = null; MV.dirty = true; close(false); }),
        MV.loc ? btn(t('use_my_loc'), () => add({lat: MV.loc.lat, lon: MV.loc.lon})) : null,
        btn(t('ok'), () => { const ll = parseLatLon(inp.value); if (!ll) { err.textContent = t('coords_hint'); return; } add(ll); }, 'prim')));
    setTimeout(() => inp.focus(), 50);
  });
}
function calibNext() {
  banner(t('calib_more'));
  modal((b, close) => {
    b.append(h('h3', null, t('calibrate')), h('div', {cls: 'note'}, t('calib_more')),
      h('div', {cls: 'modal-actions'},
        btn(t('add_point'), () => { close(); banner(t('calib_tap').replace('{n}', String(MV.calib.ctrl.length + 1))); }),
        btn(t('finish'), async () => { close(); await finishCalib(); }, 'prim')));
  });
}
async function finishCalib() {
  const ctrl = MV.calib.ctrl; MV.calib = null; hideBanner();
  await api('survey', {op: 'calib', id: MV.sid, ctrl});
  MV.fitted = false; MV.layer = 'auto'; await loadSurvey(MV.sid); fitView(); toast(t('calib_done'));
}

// -------------------------------------------------------------- saving
async function liveJpeg(meta) {
  // spectrum as on screen + a header saying where/when/what
  const W = 1600, Hs = 820, head = 150, foot = 110, sc = 2;
  const c = document.createElement('canvas'); c.width = W; c.height = head + Hs + foot;
  const sp = document.createElement('canvas'); sp.width = W; sp.height = Hs;
  const P = meta.P;
  drawSpectrumOn(sp, {...P, font: 11}, sc);
  composeJpeg(c, sp, head, meta);
  return new Promise(r => c.toBlob(r, 'image/jpeg', 0.9));
}
function composeJpeg(c, sp, head, meta) {
  const x = c.getContext('2d'), W = c.width;
  x.direction = 'ltr';
  x.fillStyle = '#12161a'; x.fillRect(0, 0, W, c.height);
  x.fillStyle = '#ffd24a'; x.font = 'bold 40px system-ui'; x.textBaseline = 'top'; x.textAlign = 'left';
  x.fillText(meta.title || 'bladeRF', 24, 18);
  x.fillStyle = '#d7e2ec'; x.font = '26px system-ui';
  x.fillText(meta.line1 || '', 24, 72); x.fillText(meta.line2 || '', 24, 108);
  if (meta.sim) { x.fillStyle = '#ffb340'; x.font = 'bold 30px system-ui'; x.textAlign = 'right'; x.fillText(t('simulated_data'), W - 24, 22); x.textAlign = 'left'; }
  x.drawImage(sp, 0, head);
  x.fillStyle = '#c3d1de'; x.font = '26px system-ui';
  const y0 = head + sp.height + 14;
  (meta.foot || []).forEach((l, i) => x.fillText(l, 24, y0 + i * 34));
  x.fillStyle = '#6f8396'; x.font = '20px system-ui'; x.textAlign = 'right';
  x.fillText('bladeRF Workbench Mobile', W - 24, c.height - 30);
}
function metaFor(p, name) {
  const when = new Date((p.t || Date.now() / 1000) * 1000);
  const loc = p.lat != null ? `${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}` + (p.acc ? `  ±${Math.round(p.acc)} m` : '') + `  (${p.src})` : t('gps_none');
  const foot = [
    `Center ${trimHz(p.center)}   Span ${fmtShort(p.span)}   RBW ${fmtShort(p.rbw)}   Gain ${Math.round(p.gain || 0)} dB`,
    (p.band_dbm != null ? `Band power ${fmtDbm(p.band_dbm)} (${fmtShort(p.band_lo)} – ${fmtShort(p.band_hi)})   ` : '') +
      (p.peak_dbm != null ? `Peak ${fmtDbm(p.peak_dbm)} @ ${trimHz(p.peak_hz)}` : ''),
  ];
  return {title: name || p.name || when.toLocaleString(), line1: when.toLocaleString() + '   ·   ' + (p.source || ''), line2: loc, foot, sim: p.sim};
}
async function uploadPointJpeg(sid, p, blob) {
  const r = await fetch(`/api/point_jpg?sid=${encodeURIComponent(sid)}&id=${encodeURIComponent(p.id)}`, {method: 'POST', body: blob});
  return (await r.json()).path;
}
function surveyPath(sid, file) { return `${S.st.files_dir}/Surveys/${sid}/${file}`; }
function saveAsFile(path, name, mime, sid, file) {
  if (AND && AND.saveAs) { AND.saveAs(path || surveyPath(sid, file), name, mime); return; }
  const a = h('a', {href: `/api/file?sid=${encodeURIComponent(sid)}&name=${encodeURIComponent(file)}`, download: name});
  document.body.append(a); a.click(); a.remove();
}
function safeName(s) { return (s || 'point').replace(/[\\/:*?"<>|]+/g, '_').trim(); }

async function savePoint(opts) {
  // opts: {sid, name, lat?, lon?, saveAs}
  const fr = S.fr; if (!fr) { toast('…'); return; }
  const P = {...liveP(), bands: true, freqs: fr.freqs, tr: fr.tr, markers: liveMarkers(),
    peak: fr.h.peak_hz != null ? {hz: fr.h.peak_hz, dbm: fr.h.peak_dbm} : null,
    watermark: isSim() ? t('src_sim').toUpperCase() : ''};
  const r = await api('point', {sid: opts.sid || '', name: opts.name || '', lat: opts.lat, lon: opts.lon});
  if (!r.ok) { toast(r.error || 'error'); return; }
  const p = r.point, sid = r.sid;
  const blob = await liveJpeg({...metaFor(p, opts.name), P});
  const path = await uploadPointJpeg(sid, p, blob);
  toast(`${t('saved_point')}${p.lat == null ? ' — ' + t('gps_none') : ''}`);
  if (opts.saveAs) saveAsFile(path, safeName(opts.name || p.name || ('point ' + new Date().toISOString().slice(0, 19))) + '.jpg', 'image/jpeg', sid, p.id + '.jpg');
  if (MV.sid === sid || !MV.sid) await loadSurvey(sid);
}
async function saveDialog(pos) {
  if (!MV.list.length) { try { MV.list = (await getJSON('/api/surveys')).list; } catch (e) { /* */ } }
  const loc = pos || (MV.loc && MV.loc.age < 30 ? MV.loc : null);
  modal((b, close) => {
    const nm = h('input', {type: 'text', placeholder: t('name_opt'), style: 'width:100%'});
    const cur = MV.sid || (S.st && S.st.survey) || '';
    const opts = MV.list.map(s => [s.id, s.name]).concat([['__new', '＋ ' + t('new_survey')]]);
    const dest = sel(opts, cur, () => {});
    const go = async (saveAs) => {
      let sid = dest.value;
      if (sid === '__new') { const s = await api('survey', {op: 'create', name: ''}); sid = s.id; MV.list = (await getJSON('/api/surveys')).list; }
      close(true);
      await savePoint({sid, name: nm.value.trim(), lat: pos ? pos.lat : undefined, lon: pos ? pos.lon : undefined, saveAs});
    };
    b.append(h('h3', null, t('save_title')), row(t('name_opt'), nm), row(t('destination'), dest),
      h('div', {cls: 'note'}, loc ? `${t('loc_line')}: ` : '', loc ? h('bdi', {dir: 'ltr'}, `${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}` +
        (loc.acc ? ` ±${Math.round(loc.acc)} m` : '') + (loc.src ? ` (${loc.src})` : '')) : t('no_loc_warn')),
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close(false)), btn(t('save_as'), () => go(true)), btn(t('save'), () => go(false), 'prim')));
    setTimeout(() => nm.focus(), 50);
  });
}

// -------------------------------------------------------------- panels
PANELS.point = async (b) => {
  const p = MV.sel; if (!p) return;
  const sid = MV.sid, idx = MV.sv.points.findIndex(q => q.id === p.id);
  $('sheet-title').textContent = p.name || (p.kind === 'track' ? t('pt_track') : t('pt_snap'));
  const nav = h('div', {cls: 'seg'},
    btn('◀', () => { const q = MV.sv.points[idx - 1]; if (q) { MV.sel = q; renderPanel(); MV.dirty = true; } }, 'sm'),
    btn('▶', () => { const q = MV.sv.points[idx + 1]; if (q) { MV.sel = q; renderPanel(); MV.dirty = true; } }, 'sm'),
    h('span', {cls: 'note'}, ` ${idx + 1} / ${MV.sv.points.length}`));
  const pic = h('div');
  b.append(nav, pic);
  const when = new Date(p.t * 1000).toLocaleString();
  const kv = h('div', {cls: 'kv'},
    h('b', null, t('time')), h('span', null, when),
    h('b', null, t('loc_line')), h('bdi', {dir: 'ltr'}, p.lat != null ? `${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}` + (p.acc ? ` ±${Math.round(p.acc)} m` : '') + ` (${p.src})` : t('gps_none')),
    h('b', null, t('band_power')), h('bdi', {dir: 'ltr'}, p.band_dbm != null ? `${fmtDbm(p.band_dbm)}  (${fmtShort(p.band_lo)} – ${fmtShort(p.band_hi)})` : '—'),
    h('b', null, t('peak')), h('bdi', {dir: 'ltr'}, p.peak_dbm != null ? `${fmtDbm(p.peak_dbm)} @ ${trimHz(p.peak_hz)}` : '—'),
    h('b', null, 'Center / Span / RBW'), h('bdi', {dir: 'ltr'}, `${trimHz(p.center)} / ${fmtShort(p.span)} / ${fmtShort(p.rbw)}`),
    h('b', null, t('src_sel')), h('span', null, (p.sim ? '⚠ ' + t('simulated_data') + ' · ' : '') + (p.source || '')),
    p.sweeps ? h('b', null, t('sweeps_n')) : null, p.sweeps ? h('span', null, String(p.sweeps)) : null);
  b.append(kv);
  const sigBox = h('div');
  b.append(sigBox);
  b.append(box(null, h('div', {cls: 'seg'},
    btn(t('pt_open'), () => { pushSet({center: p.center, span: p.span}, true); S.view = {c: p.center, s: p.span}; setTab('spec'); closePanel(); }, 'prim'),
    btn(t('pt_saveas'), async () => {
      let path;
      if (!p.jpg) { const blob = await pointJpeg(sid, p); path = await uploadPointJpeg(sid, p, blob); p.jpg = p.id + '.jpg'; }
      else path = null;
      saveAsFile(path || '', safeName(p.name || 'point ' + when) + '.jpg', 'image/jpeg', sid, p.jpg);
    }),
    AND && AND.share ? btn(t('share'), async () => {
      if (!p.jpg) { const blob = await pointJpeg(sid, p); await uploadPointJpeg(sid, p, blob); p.jpg = p.id + '.jpg'; }
      AND.share(surveyPath(sid, p.jpg));
    }) : null,
    btn(t('rename'), () => modal((bb, close) => {
      const i = h('input', {type: 'text', value: p.name || '', style: 'width:100%'});
      bb.append(h('h3', null, t('rename')), i, h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close()),
        btn(t('save'), async () => { await api('point_op', {sid, id: p.id, what: 'rename', name: i.value}); close(); await loadSurvey(sid); MV.sel = MV.sv.points.find(q => q.id === p.id); renderPanel(); }, 'prim')));
    })),
    btn(t('pt_place'), () => { MV.moving = p; closePanel(); banner(t('m_place_hint')); }),
    MV.loc ? btn(t('pt_move'), async () => { await api('point_op', {sid, id: p.id, what: 'move', lat: MV.loc.lat, lon: MV.loc.lon}); await loadSurvey(sid); }) : null,
    btn(t('del'), async () => { if (await confirmBox(t('pt_delete_q'))) { await api('point_op', {sid, id: p.id, what: 'delete'}); MV.sel = null; closePanel(); await loadSurvey(sid); } }, 'danger'))));
  // picture: the saved JPEG, else drawn from the stored spectrum
  if (p.jpg) {
    pic.append(h('img', {cls: 'pt-img', src: `/api/file?sid=${encodeURIComponent(sid)}&name=${encodeURIComponent(p.jpg)}&v=${p.t}`}));
  }
  try {
    const d = await loadPointData(sid, p);
    if (!p.jpg) {
      const cv = h('canvas', {style: 'width:100%;height:220px;border-radius:6px'}); pic.append(cv);
      requestAnimationFrame(() => { const r = cv.getBoundingClientRect(); cv.width = r.width * dpr; cv.height = r.height * dpr; drawSpectrumOn(cv, pointP(d)); });
    }
    const sg = (d.h.signals || []);
    sigBox.append(box(t('signals_here'), sg.length ? h('div', {cls: 'list'}, ...sg.slice(0, 8).map(it => h('div', {cls: 'li sig'},
      h('div', {cls: 't'}, h('b', null, h('bdi', {dir: 'ltr'}, trimHz(it.hz))), h('small', null, h('bdi', {dir: 'ltr'}, [it.tech, it.band].filter(Boolean).join(' · ')))),
      h('span', {cls: 'lvl', dir: 'ltr'}, fmtDbm(it.dbm))))) : h('div', {cls: 'note'}, t('no_signals'))));
  } catch (e) { /* data missing */ }
};
async function loadPointData(sid, p) {
  const buf = await (await fetch(`/api/point_data?sid=${encodeURIComponent(sid)}&id=${encodeURIComponent(p.id)}`)).arrayBuffer();
  const dv = new DataView(buf), hl = dv.getUint32(0, true);
  const hd = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  let off = 4 + hl; const n = hd.n;
  const freqs = new Float64Array(buf.slice(off, off + 8 * n)); off += 8 * n;
  const tr = [];
  for (const has of hd.has) { if (has) { tr.push(new Float32Array(buf.slice(off, off + 4 * n))); off += 4 * n; } else tr.push(null); }
  return {h: hd, freqs, tr};
}
function pointP(d) {
  // the 5th array is the mean-power trace: not drawn (it is for numbers)
  const tr = d.tr.slice(0, 4);
  let mx = -Infinity;
  for (const a of tr) if (a) for (const v of a) if (isFinite(v) && v > mx) mx = v;
  const ref = isFinite(mx) ? Math.ceil((mx + 8) / 10) * 10 : S.ui.ref;
  const p = d.h.point || {};
  return {c: d.h.center, s: d.h.span, ref, div: 10, bands: true, freqs: d.freqs, tr,
    peak: p.peak_hz != null ? {hz: p.peak_hz, dbm: p.peak_dbm} : null,
    shade: p.band_lo ? [[p.band_lo, p.band_hi, 'rgba(124,255,107,0.08)']] : [],
    watermark: p.sim ? t('src_sim').toUpperCase() : ''};
}
async function pointJpeg(sid, p) {
  const d = await loadPointData(sid, p);
  const W = 1600, Hs = 820, head = 150, foot = 110;
  const c = document.createElement('canvas'); c.width = W; c.height = head + Hs + foot;
  const sp = document.createElement('canvas'); sp.width = W; sp.height = Hs;
  drawSpectrumOn(sp, {...pointP(d), font: 11}, 2);
  composeJpeg(c, sp, head, metaFor(p));
  return new Promise(r => c.toBlob(r, 'image/jpeg', 0.9));
}

PANELS.surveys = (b) => {
  const sv = MV.sv;
  b.append(box(null, h('div', {cls: 'note'}, t('survey_note')),
    h('div', {cls: 'seg'}, btn('＋ ' + t('new_survey'), async () => {
      const s = await api('survey', {op: 'create', name: ''}); MV.fitted = false; MV.list = (await getJSON('/api/surveys')).list; await loadSurvey(s.id); renderPanel();
    }, 'prim'))));
  const list = h('div', {cls: 'list'});
  for (const s of MV.list) {
    list.append(h('div', {cls: 'li' + (s.id === MV.sid ? ' on' : '')},
      h('div', {cls: 't', onclick: async () => { await api('survey', {op: 'select', id: s.id}); MV.fitted = false; await loadSurvey(s.id); renderPanel(); }},
        h('b', null, s.name), h('small', null, `${s.n_points} ${t('points')} · ${s.n_tracks} ${t('walks')}` + (s.has_base ? ' · 🖼' : ''))),
      btn('✎', () => modal((bb, close) => {
        const i = h('input', {type: 'text', value: s.name, style: 'width:100%'});
        bb.append(h('h3', null, t('rename')), i, h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close()),
          btn(t('save'), async () => { await api('survey', {op: 'rename', id: s.id, name: i.value}); close(); MV.list = (await getJSON('/api/surveys')).list; if (s.id === MV.sid) await loadSurvey(s.id); renderPanel(); }, 'prim')));
      }), 'sm'),
      btn('🗑', async () => { if (await confirmBox(`${t('del')}: ${s.name}?`)) { await api('survey', {op: 'delete', id: s.id}); MV.list = (await getJSON('/api/surveys')).list; MV.fitted = false; await loadSurveys(); renderPanel(); } }, 'sm danger')));
  }
  b.append(box(t('surveys'), list));
  if (!sv) return;
  b.append(box(sv.name, h('div', {cls: 'seg'},
    btn(t('export'), async () => { const r = await api('survey', {op: 'export', id: MV.sid}); toast(t('saved') + ': ' + r.kml.split('/').pop());
      if (AND && AND.share) AND.share(r.kml); }),
    btn(t('export_map'), exportMapJpeg),
    btn(t('fit'), () => { MV.fitted = false; fitView(); MV.dirty = true; }))));
};

PANELS.mapset = (b) => {
  const sv = MV.sv; if (!sv) return;
  const file = h('input', {type: 'file', accept: 'image/*', style: 'display:none'});
  file.addEventListener('change', async () => {
    const f = file.files[0]; if (!f) return;
    const url = URL.createObjectURL(f), im = new Image();
    im.onload = async () => {
      await fetch(`/api/base_image?sid=${encodeURIComponent(MV.sid)}&name=${encodeURIComponent(f.name)}&w=${im.width}&h=${im.height}`, {method: 'POST', body: f});
      URL.revokeObjectURL(url); MV.imgKey = ''; MV.fitted = false; await loadSurvey(MV.sid); startCalib();
    };
    im.src = url;
  });
  b.append(file);
  b.append(box(t('base_image'), h('div', {cls: 'note'}, t('base_note')),
    sv.base ? h('div', {cls: 'note ltr'}, `${sv.base.name}  ${sv.base.w}×${sv.base.h}  · ${sv.base.ctrl.length} ✓`) : null,
    h('div', {cls: 'seg'},
      btn(t('upload_image'), () => file.click(), 'prim'),
      sv.base ? btn(t('calibrate'), startCalib) : null,
      sv.base ? btn(t('remove_image'), async () => { await api('survey', {op: 'remove_base', id: MV.sid}); MV.imgKey = ''; await loadSurvey(MV.sid); renderPanel(); }, 'danger') : null)));
  const lay = h('div', {cls: 'seg'}, ...[['auto', t('auto')], ['image', t('layer_image')], ['osm', t('layer_osm')], ['grid', t('layer_grid')]].map(([k, lab]) =>
    btn(lab, () => { MV.layer = k; MV.dirty = true; S.ui.mapLayer = k; saveUi(); renderPanel(); }, 'sm' + (MV.layer === k ? ' on' : ''))));
  const op = h('input', {type: 'range', min: 0.2, max: 1, step: 0.05, value: MV.opacity});
  op.addEventListener('input', () => { MV.opacity = +op.value; MV.dirty = true; });
  b.append(box(t('layer'), lay, row(t('opacity'), op), h('div', {cls: 'note'}, t('osm_note'))));
  const m = sv.metric || {kind: 'band'};
  const lo = h('input', {type: 'text', cls: 'ltr', style: 'width:110px', value: m.lo ? trimHz(m.lo) : '', placeholder: 'auto'});
  const hi = h('input', {type: 'text', cls: 'ltr', style: 'width:110px', value: m.hi ? trimHz(m.hi) : '', placeholder: 'auto'});
  const setM = async (kind, l, hh) => { await api('survey', {op: 'metric', id: MV.sid, kind, lo: l, hi: hh}); await loadSurvey(MV.sid); renderPanel(); };
  b.append(box(t('metric'),
    h('div', {cls: 'seg'}, btn(t('metric_band'), () => setM('band', m.lo, m.hi), 'sm' + (m.kind !== 'peak' ? ' on' : '')),
      btn(t('metric_peak'), () => setM('peak', m.lo, m.hi), 'sm' + (m.kind === 'peak' ? ' on' : ''))),
    row(t('band_range'), lo, '–', hi, btn(t('apply'), () => setM(m.kind, parseHz(lo.value) || '', parseHz(hi.value) || ''), 'sm')),
    h('div', {cls: 'seg'}, btn(t('use_view'), () => setM(m.kind, S.view.c - S.view.s / 2, S.view.c + S.view.s / 2), 'sm'),
      btn(t('whole_span'), () => setM(m.kind, '', ''), 'sm')),
    h('div', {cls: 'note'}, t('hz_note'))));
  const s = S.set;
  b.append(box(t('track_settings'),
    row(t('interval_s'), numInput(s.track_interval, (v) => pushSet({track_interval: v}, true), {min: 0.5, step: 0.5})),
    row(t('min_dist'), numInput(s.track_dist, (v) => pushSet({track_dist: v}, true), {min: 0, step: 1})),
    h('div', {cls: 'note'}, t('track_note'))));
  b.append(box(null, chk(s.sim_gps, (v) => { pushSet({sim_gps: v}, true); }, t('sim_gps')),
    h('div', {cls: 'note'}, t('sim_gps_note')), h('div', {cls: 'note'}, t('gps_answer'))));
};

async function exportMapJpeg() {
  const c = document.createElement('canvas'); c.width = mapCv.width; c.height = mapCv.height + 90 * dpr;
  const x = c.getContext('2d');
  x.fillStyle = '#12161a'; x.fillRect(0, 0, c.width, c.height);
  try { x.drawImage(mapCv, 0, 90 * dpr); } catch (e) { /* tainted by tiles */ }
  x.fillStyle = '#ffd24a'; x.font = `bold ${22 * dpr}px system-ui`; x.textBaseline = 'top';
  x.fillText(MV.sv.name, 12 * dpr, 10 * dpr);
  const rng = valueRange(), kind = (MV.sv.metric && MV.sv.metric.kind) || 'band';
  x.fillStyle = '#d7e2ec'; x.font = `${14 * dpr}px system-ui`;
  x.fillText(`${new Date().toLocaleString()}  ·  ${geoPoints().length} ${t('points')}  ·  ${kind === 'peak' ? t('peak') : t('band_power')} ${rng[0]} … ${rng[1]} dBm`, 12 * dpr, 44 * dpr);
  for (let i = 0; i < 200; i++) { x.fillStyle = colorOf(rng[0] + i / 199 * (rng[1] - rng[0]), rng); x.fillRect((12 + i) * dpr, 68 * dpr, dpr + 1, 10 * dpr); }
  let blob;
  try { blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.9)); } catch (e) { blob = null; }
  if (!blob) { toast('map export blocked by online tiles — switch background to image/grid'); return; }
  const name = `map ${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.jpg`;
  const r = await (await fetch(`/api/blob?sid=${encodeURIComponent(MV.sid)}&name=${encodeURIComponent(name)}`, {method: 'POST', body: blob})).json();
  toast(t('saved') + ': ' + name);
  saveAsFile(r.path, name, 'image/jpeg', MV.sid, name);
}

// --------------------------------------------------------------- track
function updateTrackBtn() {
  const tr = S.st && S.st.track, b = $('m-track');
  b.classList.toggle('on', !!tr);
  b.innerHTML = tr ? `■ <span>${t('m_track_stop')}</span> · ${tr.n}` : `● <span>${t('m_track')}</span>`;
}
$('m-track').onclick = async () => {
  if (S.st && S.st.track) { const r = await api('track', {what: 'stop'}); toast(`${r.n} ${t('points')}`); }
  else { await api('track', {what: 'start', sid: MV.sid}); MV.follow = true; }
  await pollState(); updateTrackBtn(); await loadSurvey(MV.sid);
};
$('m-save').onclick = () => saveDialog(null);
$('m-place').onclick = () => { MV.placing = !MV.placing; $('m-place').classList.toggle('on', MV.placing); if (MV.placing) banner(t('m_place_hint')); else hideBanner(); };
$('m-me').onclick = () => { MV.follow = true; if (MV.loc) { MV.v.x = mercX(MV.loc.lon); MV.v.y = mercY(MV.loc.lat); MV.v.res = Math.min(MV.v.res, 2); } MV.dirty = true; };
$('m-survey').onclick = async () => { MV.list = (await getJSON('/api/surveys')).list; openPanel('surveys'); };
$('m-layers').onclick = () => openPanel('mapset');

// ---------------------------------------------------------------- tabs
function resizeMap() {
  for (const c of [mapCv, miniCv]) {
    const r = c.getBoundingClientRect();
    const w = Math.max(10, Math.round(r.width * dpr)), hh = Math.max(10, Math.round(r.height * dpr));
    if (c.width !== w || c.height !== hh) { c.width = w; c.height = hh; }
  }
  MV.dirty = true; S.newMini = true;
}
function setTab(which) {
  MV.on = which === 'map';
  $('tab-spec').classList.toggle('on', !MV.on); $('tab-map').classList.toggle('on', MV.on);
  $('main').classList.toggle('hidden', MV.on); $('mapview').classList.toggle('hidden', !MV.on);
  closePanel();
  if (MV.on) {
    startLocation();
    requestAnimationFrame(() => { resizeMap(); if (!MV.sv) loadSurveys(); else loadSurvey(MV.sid); });
  } else requestAnimationFrame(resizeCanvases);
}
$('tab-spec').onclick = () => setTab('spec');
$('tab-map').onclick = () => setTab('map');
new ResizeObserver(() => { if (MV.on) resizeMap(); }).observe($('mapview'));
setInterval(pollLocation, 1000);
setInterval(() => { if (MV.on) updateTrackBtn(); }, 1000);
setTimeout(() => { MV.layer = (S.ui && S.ui.mapLayer) || 'auto'; }, 1500);
requestAnimationFrame(mapLoop);

// the spectrum tab's own "Save point" key
window.mapSaveDialog = () => saveDialog(null);
window.mapLangChanged = () => { if (MV.sv) updateLegend(); updateTrackBtn(); };
