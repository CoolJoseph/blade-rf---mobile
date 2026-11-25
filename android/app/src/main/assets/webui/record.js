/* YT Spectrum Analyzer -- "Save spectrum recording".
 *
 * One button, a choice of what to keep: a picture (JPEG), a few seconds of
 * video, the dry data on screen (CSV), a full log (JSON with everything the
 * engine knows) -- or all of them. Every file carries the location, and
 * "put it on the map" also stores the recording as a map point, so a survey
 * can be walked back by coordinates later. Missions call saveRecording()
 * with the same options, without the dialog.
 */
'use strict';

const REC_DEF = {image: true, video: false, videoS: 5, data: true, log: false, map: true};
function recPrefs() { return Object.assign({}, REC_DEF, S.ui.rec || {}); }

function stampNow() {
  const d = new Date(), z = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}`;
}
function locText(loc) {
  return loc ? `${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}` + (loc.acc ? `  ±${Math.round(loc.acc)} m` : '') + `  (${loc.src})` : '⚠ ' + t('gps_none');
}
function livePlot() {
  const fr = S.fr; if (!fr) return null;
  return {...liveP(), bands: true, freqs: fr.freqs, tr: fr.tr, markers: liveMarkers(),
    peak: fr.h.peak_hz != null ? {hz: fr.h.peak_hz, dbm: fr.h.peak_dbm} : null,
    watermark: isSim() ? t('src_sim').toUpperCase() : ''};
}
function recMeta(r, name) {
  if (r.point) return metaFor(r.point, name);
  const src = S.st.source;
  return {
    title: name || r.base, sim: isSim(),
    line1: new Date().toLocaleString() + '   ·   ' + (src.info || ''),
    line2: locText(r.location),
    foot: [`Center ${trimHz(S.view.c)}   Span ${fmtShort(S.view.s)}   RBW ${S.fr ? fmtShort(S.fr.h.rbw) : '—'}   Gain ${Math.round(S.set.gain)} dB`,
      (r.band_dbm != null ? `Band power ${fmtDbm(r.band_dbm)}   ` : '') + (r.peak_dbm != null ? `Peak ${fmtDbm(r.peak_dbm)} @ ${trimHz(r.peak_hz)}` : '')],
  };
}
async function postBlob(name, blob) {
  const r = await fetch('/api/blob?name=' + encodeURIComponent(name), {method: 'POST', body: blob});
  return r.json();
}

// ------------------------------------------------------------------ video
function videoMime() {
  if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) return null;
  for (const m of ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4']) {
    try { if (MediaRecorder.isTypeSupported(m)) return m; } catch (e) { /* */ }
  }
  return null;
}
// Drawn on its own canvas (not a copy of the screen), so it works on any
// screen -- a mission can record while the Map is shown.
async function recordVideo(seconds, meta, onTick) {
  const mime = videoMime(); if (!mime) return null;
  const W = 1280, H = 720, head = 64;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const sp = document.createElement('canvas'); sp.width = W; sp.height = Math.round((H - head) * 0.62);
  const x = c.getContext('2d');
  const draw = () => {
    x.fillStyle = '#12161a'; x.fillRect(0, 0, W, H); x.direction = 'ltr';
    x.fillStyle = '#ffd24a'; x.font = 'bold 24px system-ui'; x.textBaseline = 'top'; x.textAlign = 'left';
    x.fillText(meta.title || 'YT Spectrum Analyzer', 16, 8);
    x.fillStyle = '#d7e2ec'; x.font = '17px system-ui';
    x.fillText(`${new Date().toLocaleString()}   ·   ${meta.line2 || ''}`, 16, 38);
    if (meta.sim) { x.fillStyle = '#ffb340'; x.textAlign = 'right'; x.font = 'bold 20px system-ui'; x.fillText(t('simulated_data'), W - 16, 10); x.textAlign = 'left'; }
    const P = livePlot();
    if (P) {
      drawSpectrumOn(sp, {...P, font: 12}, 1);
      x.drawImage(sp, 0, head);
      const R = rectOf(sp, P, 1), y0 = head + sp.height, hh = H - y0;
      x.fillStyle = '#07090b'; x.fillRect(0, y0, W, hh);
      if (WF.n) {
        x.imageSmoothingEnabled = false;
        const a = fx(WF.f0, R), b = fx(WF.f1, R);
        x.save(); x.beginPath(); x.rect(R.x, y0, R.w, hh); x.clip();
        x.drawImage(WF.buf, 0, 0, WF.n, WF.rows, a, y0, b - a, hh); x.restore();
      }
      x.strokeStyle = '#b4c0cc'; x.strokeRect(R.x + 0.5, y0 + 0.5, R.w, hh - 1);
    }
    x.fillStyle = '#6f8396'; x.font = '14px system-ui'; x.textAlign = 'right'; x.textBaseline = 'bottom';
    x.fillText(`YT Spectrum Analyzer v${S.st.version}`, W - 12, H - 6);
  };
  draw();
  const stream = c.captureStream(15);
  const rec = new MediaRecorder(stream, {mimeType: mime, videoBitsPerSecond: 1500000});
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  const done = new Promise((res) => { rec.onstop = res; });
  rec.start(500);
  const t0 = performance.now();
  const iv = setInterval(() => { draw(); if (onTick) onTick((performance.now() - t0) / 1000); }, 66);
  await sleep(seconds * 1000);
  clearInterval(iv);
  rec.stop(); await done;
  stream.getTracks().forEach(tr => tr.stop());
  return {blob: new Blob(chunks, {type: mime.split(';')[0]}), ext: mime.includes('mp4') ? 'mp4' : 'webm'};
}

// ------------------------------------------------------------------- save
// opts: {name, image, video, videoS, data, log, map, sid, mission, quiet}
async function saveRecording(opts) {
  if (!S.fr) { notify(t('rec_nothing'), 'orange'); return {ok: false, n: 0}; }
  const name = (opts.name || '').trim();
  const base = safeName((name ? name + ' ' : 'REC ') + stampNow());
  const markers = liveMarkers().map(m => ({label: m.label, hz: m.hz, dbm: m.v}));
  const r = await api('record', {base, name, data: !!opts.data, log: !!opts.log, map: !!opts.map,
    sid: opts.sid || '', mission: opts.mission || '', markers,
    display: {ref: S.ui.ref, div: S.ui.div, center: S.view.c, span: S.view.s}});
  if (!r.ok) { notify(r.error || 'error', 'red', 5000); return {ok: false, n: 0}; }
  let n = r.files.length;
  const meta = recMeta(r, name || base);
  if (opts.image) {
    const P = livePlot();
    const blob = await liveJpeg({...meta, P});
    const j = await postBlob(base + '.jpg', blob); if (j.ok) n++;
    if (r.point) { try { await uploadPointJpeg(r.sid, r.point, blob); } catch (e) { /* */ } }
  }
  if (opts.video) {
    if (!opts.quiet) notify(`${t('rec_recording')} ${opts.videoS || 5} ${t('rec_video_s')}`, 'blue', (opts.videoS || 5) * 1000);
    const v = await recordVideo(opts.videoS || 5, meta);
    if (v && v.blob.size) { const j = await postBlob(base + '.' + v.ext, v.blob); if (j.ok) n++; }
    else notify(t('rec_no_video'), 'orange', 5000);
  }
  if (!r.location) notify(t('rec_no_loc'), 'orange', 5000);
  if (!opts.quiet) notify(`✓ ${t('rec_saved')}: ${n} ${t('rec_files')} — ${base}`, 'blue', 5000);
  if (r.point && typeof MV !== 'undefined' && (MV.sid === r.sid || !MV.sid)) { try { await loadSurvey(r.sid); } catch (e) { /* */ } }
  if (S.panel === 'capture') renderPanel();
  return {ok: true, n, base, sid: r.sid};
}

// ----------------------------------------------------------------- dialog
async function recordDialog(over) {
  const pr = Object.assign(recPrefs(), over || {});
  let surveys = [];
  try { surveys = (await getJSON('/api/surveys')).list; } catch (e) { /* */ }
  const loc = MV.loc && MV.loc.age < 30 ? MV.loc : null;
  modal((b, close) => {
    const nm = h('input', {type: 'text', placeholder: t('rec_name'), style: 'width:100%'});
    const cb = (k, label) => {
      const c = h('input', {type: 'checkbox'}); c.checked = !!pr[k];
      c.addEventListener('change', () => { pr[k] = c.checked; });
      return [c, h('label', null, c, label)];
    };
    const [cImg, lImg] = cb('image', t('rec_image'));
    const [cVid, lVid] = cb('video', t('rec_video'));
    const [cDat, lDat] = cb('data', t('rec_data'));
    const [cLog, lLog] = cb('log', t('rec_log'));
    const secs = sel([3, 5, 10, 15, 30].map(v => [v, `${v} ${t('rec_video_s')}`]), pr.videoS, (v) => { pr.videoS = +v; });
    lVid.append(secs);
    if (!videoMime()) { cVid.checked = false; cVid.disabled = true; pr.video = false; lVid.style.opacity = '.5'; }
    const all = btn(t('rec_all'), () => {
      for (const [c, k] of [[cImg, 'image'], [cVid, 'video'], [cDat, 'data'], [cLog, 'log']]) if (!c.disabled) { c.checked = true; pr[k] = true; }
    }, 'sm');
    const [cMap, lMap] = cb('map', t('rec_map'));
    const cur = (S.st && S.st.survey) || (surveys[0] && surveys[0].id) || '';
    const dest = sel(surveys.map(s => [s.id, s.name]).concat([['__new', '＋ ' + t('new_survey')]]), cur, () => {});
    b.append(h('h3', null, '● ' + t('rec_title')), nm,
      h('div', {cls: 'row'}, h('b', {style: 'flex:1'}, t('rec_what')), all),
      h('div', {cls: 'recopts'}, lImg, lVid, lDat, lLog),
      h('div', {style: 'height:8px'}),
      lMap, row(t('rec_dest'), dest),
      h('div', {cls: loc ? 'note' : 'note warn'}, loc ? [`${t('loc_line')}: `, h('bdi', {dir: 'ltr'}, locText(loc))] : t('rec_no_loc')),
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close(false)), btn('● ' + t('save'), async () => {
        if (!pr.image && !pr.video && !pr.data && !pr.log && !pr.map) { notify(t('rec_nothing'), 'orange'); return; }
        S.ui.rec = {image: pr.image, video: pr.video, videoS: pr.videoS, data: pr.data, log: pr.log, map: pr.map}; saveUi();
        let sid = dest.value;
        if (pr.map && sid === '__new') { const s = await api('survey', {op: 'create', name: nm.value.trim()}); sid = s.id; }
        close(true);
        notify(t('rec_saving'), 'blue', 2000);
        await saveRecording({...pr, name: nm.value.trim(), sid: pr.map ? sid : ''});
      }, 'prim')));
    setTimeout(() => nm.focus(), 50);
  });
}

// "Save to..." for any saved file (Recordings list)
function mimeOf(name) {
  const n = name.toLowerCase();
  return n.endsWith('.jpg') ? 'image/jpeg' : n.endsWith('.png') ? 'image/png' : n.endsWith('.csv') ? 'text/csv'
    : n.endsWith('.json') ? 'application/json' : n.endsWith('.webm') ? 'video/webm' : n.endsWith('.mp4') ? 'video/mp4'
    : n.endsWith('.xlsx') ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'application/octet-stream';
}
