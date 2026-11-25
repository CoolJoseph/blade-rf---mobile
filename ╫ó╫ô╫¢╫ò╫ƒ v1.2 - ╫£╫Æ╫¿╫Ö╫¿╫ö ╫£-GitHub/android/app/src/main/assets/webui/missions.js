/* YT Spectrum Analyzer -- Missions.
 *
 * A mission is a saved recipe for hands-free work: which bands to visit (by
 * preset), how long to measure each, what to save there (picture, video,
 * data, full log, a geo-tagged map point), and how often to repeat -- once,
 * N times, or continuously until Stop. Name it after the place ("Azrieli
 * roof") and it becomes that site's survey procedure.
 *
 * The runner lives here, in the page, because the pictures and the video
 * are drawn here. While it runs the screen is kept on and a red bar on every
 * screen shows progress and a Stop button.
 */
'use strict';

const MS = {list: [], run: null, edit: null};

async function loadMissions() {
  try { MS.list = await getJSON('/api/missions'); } catch (e) { MS.list = []; }
}
function presetById(id) { return S.presets.find(p => p.id === id); }
function bandNames(m) {
  const ps = m.presets.map(presetById).filter(Boolean);
  return ps.length ? ps.map(presetName) : [t('ms_current')];
}
function missionEstimate(m) {
  const n = Math.max(1, m.presets.filter(presetById).length), o = m.outputs;
  const perBand = (o.image ? 0.35 : 0) + (o.video ? o.video_s * 0.19 : 0) + (o.data ? 0.08 : 0) + (o.log ? 0.25 : 0) + (m.map ? 0.05 : 0);
  const roundS = n * (m.dwell + (o.video ? o.video_s : 0) + 2) + (m.repeat === 'once' ? 0 : m.interval);
  const perRound = perBand * n;
  return {roundS, perRound, perHour: perRound * 3600 / Math.max(1, roundS)};
}
function estText(m) {
  const e = missionEstimate(m);
  const mb = (v) => v >= 1000 ? (v / 1024).toFixed(1) + ' GB' : v.toFixed(v < 10 ? 1 : 0) + ' MB';
  const dur = e.roundS >= 90 ? (e.roundS / 60).toFixed(1) + ' min' : Math.round(e.roundS) + ' s';
  return `${t('ms_est')}: ~${mb(e.perRound)} ${t('ms_per_round')} · ${t('ms_round_t')} ${dur}` +
    (m.repeat === 'once' ? '' : ` · ~${mb(e.perHour)} ${t('ms_per_hour')}`);
}
function newMission() {
  return {id: '', name: '', presets: [], dwell: 5, gain: null,
    outputs: {image: true, video: false, video_s: 5, data: true, log: false},
    map: true, track: false, repeat: 'once', count: 3, interval: 0};
}

// ------------------------------------------------------------------- list
async function renderMissions() {
  const body = $('mission-body'); if (!body) return;
  if (MS.edit) { renderEditor(body); return; }
  await loadMissions();
  body.textContent = '';
  body.append(h('div', {cls: 'mhead'}, h('h3', null, '🗂 ' + t('ms_title')),
    btn('＋ ' + t('ms_new'), () => { MS.edit = newMission(); renderMissions(); }, 'prim')));
  if (MS.run) body.append(h('div', {cls: 'warnbox', style: 'margin-bottom:10px'}, runText()));
  if (!MS.list.length) body.append(h('div', {cls: 'note', style: 'font-size:13px'}, t('ms_none')));
  const last = S.ui.msLast || {};
  for (const m of MS.list) {
    const o = m.outputs, tags = [];
    tags.push(`${bandNames(m).length} × ${t('ms_band')}`, `${m.dwell} ${t('ms_sec')}`);
    if (o.image) tags.push('🖼 JPEG'); if (o.video) tags.push(`🎬 ${o.video_s}${t('ms_sec')}`);
    if (o.data) tags.push('📄 CSV'); if (o.log) tags.push('🧾 LOG'); if (m.map) tags.push('📍'); if (m.track) tags.push('👣');
    if (m.gain != null) tags.push(`Gain ${m.gain} dB`);
    const rep = m.repeat === 'continuous' ? h('span', {cls: 'tag cont'}, '∞ ' + t('ms_cont')) :
      h('span', {cls: 'tag'}, m.repeat === 'count' ? `${m.count} ${t('ms_times')}` : t('ms_once'));
    body.append(h('div', {cls: 'mcard'},
      h('h4', null, m.name),
      h('div', {cls: 'meta'}, bandNames(m).join(' · ')),
      h('div', {cls: 'tags'}, ...tags.map(x => h('span', {cls: 'tag'}, x)), rep),
      last[m.id] ? h('div', {cls: 'note'}, `${t('ms_last')}: ${new Date(last[m.id]).toLocaleString()}`) : null,
      h('div', {cls: 'seg'},
        btn(t('ms_run'), () => runMission(m), 'prim'),
        btn(t('ms_edit'), () => { MS.edit = JSON.parse(JSON.stringify(m)); renderMissions(); }, 'sm'),
        btn(t('ms_dup'), async () => { const c = JSON.parse(JSON.stringify(m)); c.id = ''; c.name += ' 2'; await saveMission(c); renderMissions(); }, 'sm'),
        btn('🗑', async () => { if (await confirmBox(t('ms_del_q'))) { await api('mission', {op: 'delete', id: m.id}); renderMissions(); } }, 'sm danger'))));
  }
}
async function saveMission(m) {
  if (!m.id) m.id = 'm' + Date.now();
  const r = await api('mission', {op: 'save', mission: m});
  if (r.missions) MS.list = r.missions;
  return MS.list.find(x => x.id === m.id) || m;
}

// ----------------------------------------------------------------- editor
function renderEditor(body) {
  const m = MS.edit;
  body.textContent = '';
  const est = h('div', {cls: 'est'});
  const upd = () => { est.textContent = estText(m); };
  const nm = h('input', {type: 'text', value: m.name, placeholder: t('ms_name_ph'), style: 'width:100%'});
  nm.addEventListener('input', () => { m.name = nm.value; });
  // bands, in the order of the preset list
  const pick = h('div', {cls: 'bandpick'});
  for (const p of S.presets) {
    const c = h('input', {type: 'checkbox'}); c.checked = m.presets.includes(p.id);
    const lab = h('label', {cls: c.checked ? 'on' : ''}, c, h('span', null, presetName(p)));
    c.addEventListener('change', () => {
      m.presets = S.presets.filter(q => q.id === p.id ? c.checked : m.presets.includes(q.id)).map(q => q.id);
      lab.classList.toggle('on', c.checked); upd();
    });
    pick.append(lab);
  }
  const o = m.outputs;
  const ck = (obj, k, label) => chk(obj[k], (v) => { obj[k] = v; upd(); }, label);
  const repSeg = h('div', {cls: 'seg'});
  const drawRep = () => {
    repSeg.textContent = '';
    for (const [k, lab] of [['once', t('ms_once')], ['count', t('ms_count')], ['continuous', '∞ ' + t('ms_cont')]]) {
      repSeg.append(btn(lab, async () => {
        if (k === 'continuous' && m.repeat !== 'continuous') {
          const prev = m.repeat; m.repeat = 'continuous';
          const ok = await contConfirm(m);
          if (!ok) { m.repeat = prev; }
        } else m.repeat = k;
        drawRep(); upd();
      }, 'sm' + (m.repeat === k ? ' on' : '')));
    }
    cnt.style.display = m.repeat === 'count' ? '' : 'none';
    ivRow.style.display = m.repeat === 'once' ? 'none' : '';
  };
  const cnt = row(t('ms_count'), numInput(m.count, (v) => { m.count = Math.max(1, Math.round(v)); upd(); }, {min: 1, step: 1}), t('ms_times'));
  const ivRow = row(t('ms_interval'), sel([0, 10, 30, 60, 120, 300, 600, 1800].map(v => [v, v < 60 ? `${v} ${t('ms_sec')}` : `${v / 60} min`]), m.interval,
    (v) => { m.interval = +v; upd(); }));
  const src = S.st.source;
  body.append(
    h('div', {cls: 'mhead'}, btn('‹', () => { MS.edit = null; renderMissions(); }, 'sm'), h('h3', null, m.id ? t('ms_edit') : t('ms_new'))),
    box(t('ms_name'), nm),
    box(t('ms_bands'), pick, h('div', {cls: 'note'}, t('ms_bands_note'))),
    box(null,
      row(t('ms_dwell'), sel([2, 3, 5, 10, 20, 30, 60, 120].map(v => [v, `${v} ${t('ms_sec')}`]), m.dwell, (v) => { m.dwell = +v; upd(); })),
      row(t('ms_gain'), sel([['', t('ms_gain_keep')]].concat(
        Array.from({length: Math.floor((src.gain_max - src.gain_min) / 5) + 1}, (_, i) => src.gain_min + i * 5).map(v => [v, v + ' dB'])),
        m.gain == null ? '' : m.gain, (v) => { m.gain = v === '' ? null : +v; }))),
    box(t('ms_outputs'),
      h('div', {cls: 'recopts'}, ck(o, 'image', t('rec_image')),
        h('label', null, ...chk(o.video, (v) => { o.video = v; upd(); }, t('rec_video')).childNodes,
          sel([3, 5, 10, 15, 30].map(v => [v, `${v} ${t('ms_sec')}`]), o.video_s, (v) => { o.video_s = +v; upd(); })),
        ck(o, 'data', t('rec_data')), ck(o, 'log', t('rec_log'))),
      h('div', {style: 'height:6px'}),
      ck(m, 'map', t('ms_map')), ck(m, 'track', t('ms_track'))),
    box(t('ms_repeat'), repSeg, cnt, ivRow, est),
    h('div', {cls: 'modal-actions'},
      btn(t('cancel'), () => { MS.edit = null; renderMissions(); }),
      btn(t('ms_save'), async () => { if (!m.name.trim()) { notify(t('ms_need_name'), 'orange'); nm.focus(); return; }
        await saveMission(m); MS.edit = null; notify('✓ ' + m.name, 'blue'); renderMissions(); }, 'prim'),
      btn(t('ms_save') + ' + ' + t('ms_run'), async () => { if (!m.name.trim()) { notify(t('ms_need_name'), 'orange'); nm.focus(); return; }
        const sm = await saveMission(m); MS.edit = null; renderMissions(); runMission(sm, true); }, 'prim')));
  drawRep(); upd();
}
function contConfirm(m) {
  return modal((b, close) => {
    b.append(h('h3', null, '⚠ ' + t('ms_cont_title')), h('div', {cls: 'warnbox'}, t('ms_cont_text')),
      h('div', {cls: 'est'}, estText(m)),
      h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close(false)), btn(t('ms_cont_ok'), () => close(true), 'prim')));
  });
}

// ----------------------------------------------------------------- runner
function runText() {
  const r = MS.run; if (!r) return '';
  const el = Math.round((Date.now() - r.t0) / 1000), z = (n) => String(n).padStart(2, '0');
  const clock = `${z(Math.floor(el / 3600))}:${z(Math.floor(el / 60) % 60)}:${z(el % 60)}`;
  const rounds = r.m.repeat === 'count' ? `${r.round}/${r.m.count}` : String(r.round);
  const what = r.wait > 0 ? `${t('ms_waiting')} ${r.wait} ${t('ms_sec')}` : `${t('ms_band')} ${r.i}/${r.n} ${r.band}`;
  return `${t('ms_running')}: ${r.m.name} · ${t('ms_round')} ${rounds} · ${what} · ${clock} · ${r.files} ${t('ms_files')}`;
}
function updateRunbar() {
  const bar = $('runbar');
  if (!MS.run) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  $('run-text').textContent = runText();
}
$('run-stop').onclick = () => { if (MS.run) { MS.run.stop = true; notify(t('ms_stopped'), 'orange'); } };
setInterval(updateRunbar, 1000);

async function runMission(m, confirmed) {
  if (MS.run) { notify(t('ms_busy'), 'orange'); return; }
  if (m.repeat === 'continuous' && !confirmed && !(await contConfirm(m))) return;
  const bands = m.presets.map(presetById).filter(Boolean);
  const view0 = {...S.view};
  const R = MS.run = {m, round: 0, i: 0, n: Math.max(1, bands.length), band: '', files: 0, t0: Date.now(), stop: false, wait: 0};
  updateRunbar();
  if (AND && AND.keepScreenOn) AND.keepScreenOn(true);
  if (!S.set.running) { await api('set', {running: true}); S.set.running = true; }
  let sid = '';
  try {
    if (m.map) {
      const d = new Date(), z = (n) => String(n).padStart(2, '0');
      const s = await api('survey', {op: 'create', name: `${m.name} ${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`});
      sid = s.id;
      if (typeof MV !== 'undefined') MV.sid = sid;
    }
    if (m.track && sid) await api('track', {what: 'start', sid});
    setScreen('spec');
    notify(`▶ ${m.name}`, 'blue');
    outer:
    while (!R.stop) {
      R.round++;
      const list = bands.length ? bands : [null];
      for (let k = 0; k < list.length; k++) {
        if (R.stop) break outer;
        const p = list[k];
        R.i = k + 1; R.band = p ? presetName(p) : t('ms_current'); R.wait = 0; updateRunbar();
        if (p) await applyPreset(p.id); else tune(view0.c, view0.s, true);
        if (m.gain != null) pushSet({gain: m.gain}, true);
        await api('restart'); WF.key = '';
        // dwell: at least the asked time AND two complete sweeps of this band
        const seq0 = S.seq, t0 = Date.now();
        while (!R.stop) {
          await sleep(250);
          const el = Date.now() - t0;
          if ((el >= m.dwell * 1000 && S.seq - seq0 >= 2) || el > m.dwell * 1000 + 60000) break;
        }
        if (R.stop) break outer;
        const res = await saveRecording({name: `${m.name} - ${R.band}`, ...m.outputs, videoS: m.outputs.video_s,
          map: m.map, sid, mission: m.name, quiet: true});
        R.files += res.n || 0; updateRunbar();
      }
      if (m.repeat === 'once' || (m.repeat === 'count' && R.round >= m.count)) break;
      for (let w = Math.round(m.interval); w > 0 && !R.stop; w--) { R.wait = w; updateRunbar(); await sleep(1000); }
      R.wait = 0;
    }
  } catch (e) {
    notify(`${t('ms_stopped')}: ${e.message || e}`, 'red', 8000);
  } finally {
    if (m.track && sid) { try { await api('track', {what: 'stop'}); } catch (e) { /* */ } }
    if (AND && AND.keepScreenOn) AND.keepScreenOn(S.ui.keepOn !== false);
    S.ui.msLast = Object.assign({}, S.ui.msLast || {}, {[m.id]: Date.now()}); saveUi();
    const stopped = R.stop;
    MS.run = null; updateRunbar();
    notify(`${stopped ? t('ms_stopped') : '✓ ' + t('ms_done')}: ${m.name} · ${R.round} ${t('ms_round')} · ${R.files} ${t('ms_files')}`, stopped ? 'orange' : 'blue', 10000);
    if (SCR.cur === 'missions') renderMissions();
  }
}
