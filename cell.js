/* NETLINE Spectrum Analyzer -- Cellular survey screen.
 *
 * For someone who does not know RF: type the site name once, walk to a
 * spot, press the big button, stand still ~half a minute. The app visits
 * every cellular downlink band of the chosen country in 100 MHz views,
 * finds the carriers, and saves "Point N" with a report, the data and a
 * picture of each view that had signal. The map then colours the points by
 * the strength of any band (or best / 5G only). The logic of what counts
 * and what is kept is in cellscan.py.
 */
'use strict';

const CELL = {plan: null, sites: [], run: null, last: null, open: null};
const LEVEL_COL = {strong: '#35e07a', good: '#b6e35a', weak: '#ffb340', very_weak: '#ff7a45', none: '#6f8396'};
const pad2 = (n) => String(n).padStart(2, '0');
const mhz = (hz) => (hz / 1e6).toFixed(hz % 1e6 ? 1 : 0);

async function cellLoad() {
  try { CELL.plan = await getJSON('/api/cell_plan'); } catch (e) { /* */ }
  try { CELL.sites = await getJSON('/api/cell_sites'); } catch (e) { CELL.sites = []; }
}
function cellSite() { return (CELL.plan && CELL.plan.cfg.site) || ''; }
function sitePoints() { const s = CELL.sites.find(x => x.site === cellSite()); return s ? s.points : []; }
function countryName(k) { const c = CELL.plan.countries[k]; return c ? (S.ui.lang === 'he' ? c.name_he : c.name) : k; }
function levelWord(l) { return t('lv_' + (l || 'none')); }
function cellEta() { const w = CELL.plan ? CELL.plan.windows.length : 11; return Math.round(w * (1.2 + 0.6 * CELL.plan.cfg.sweeps)); }

// ---------------------------------------------------------------- screen
async function renderCell() {
  const body = $('cell-body'); if (!body) return;
  if (!CELL.plan) await cellLoad();
  if (!CELL.plan) return;
  body.textContent = '';
  if (CELL.run) { renderCellRun(body); return; }
  const P = CELL.plan, src = S.st.source, L = locState();
  body.append(h('div', {cls: 'mhead'}, h('h3', {cls: 'cell-h'}, '📶 ' + t('cell_title')),
    btn('⚙︎ ' + t('cell_plan'), () => openPanel('cellset'), 'sm')));
  // what the user must check before pressing
  body.append(h('div', {cls: 'cell-checks'},
    h('div', {cls: 'cchk ' + (src.radio && !src.lost ? 'ok' : 'bad')}, h('span', {cls: 'dot'}),
      src.radio && !src.lost ? `bladeRF ${t('connected')}` : t('cell_no_radio'),
      !src.radio ? btn(t('cell_connect'), connectRadio, 'sm prim') : null),
    h('div', {cls: 'cchk ' + (L.cls === 'ok' ? 'ok' : L.cls === 'none' ? 'bad' : 'warn'), onclick: () => $('b-gps').click()},
      h('span', {cls: 'dot'}), L.text)));
  // site
  const site = h('input', {type: 'text', value: cellSite(), placeholder: t('cell_site_ph'), style: 'width:100%'});
  site.addEventListener('change', async () => { CELL.plan = await api('cell_cfg', {site: site.value.trim()}); renderCell(); });
  const others = CELL.sites.filter(s => s.site !== cellSite()).slice(0, 8);
  body.append(box(t('cell_site'), site,
    others.length ? h('div', {cls: 'seg', style: 'margin-top:6px'}, ...others.map(s =>
      btn(s.site, async () => { CELL.plan = await api('cell_cfg', {site: s.site}); CELL.last = null; renderCell(); }, 'sm'))) : null,
    h('div', {cls: 'note'}, `${countryName(P.cfg.country)} · ${P.bands.length} ${t('cell_bands')} · ${P.windows.length} ${t('cell_views')}`)));
  // the button
  const n = (sitePoints().reduce((a, p) => Math.max(a, p.n || 0), 0)) + 1;
  const go = h('button', {cls: 'cell-go', disabled: cellSite() ? null : true, onclick: () => cellRun()},
    h('i', null, '📍'), h('b', null, `${t('cell_sample')} ${n}`), h('small', null, `~${cellEta()} ${t('ms_sec')} · ${t('cell_still')}`));
  body.append(h('div', {cls: 'cell-go-wrap'}, go, !cellSite() ? h('div', {cls: 'note warn'}, t('cell_need_site')) : null,
    src.sim ? h('div', {cls: 'note warn'}, '⚠ ' + t('al_sim')) : null));
  if (CELL.last) body.append(cellResultCard(CELL.last, true));
  // points of this site
  const pts = sitePoints();
  if (pts.length) {
    body.append(box(`${t('cell_points')} — ${cellSite()} (${pts.length})`,
      h('div', {cls: 'list'}, ...pts.slice().reverse().map(p => {
        const act = p.bands.filter(b => b.active).length, nr = p.bands.some(b => b.active && b.best_tech === '5G NR');
        return h('div', {cls: 'li', onclick: () => cellPointModal(cellSite(), p)},
          h('div', {cls: 't'}, h('b', null, p.label),
            h('small', null, `${p.time ? new Date(p.time * 1000).toLocaleTimeString() : ''} · ${act}/${p.bands.length} ${t('cell_active')}` +
              (nr ? ' · 5G ✓' : '') + (p.location ? '' : ' · ⚠ ' + t('gps_none')))),
          h('span', {cls: 'cdots'}, ...p.bands.map(b => h('i', {style: `background:${LEVEL_COL[b.active ? b.level : 'none']}`, title: b.name}))));
      })),
      h('div', {cls: 'seg', style: 'margin-top:8px'},
        btn('🗺 ' + t('cell_on_map'), () => cellShowMap(), 'prim'),
        btn('📦 ' + t('cell_export'), () => cellExport(cellSite())))));
  }
  body.append(h('div', {cls: 'note', style: 'margin:10px 2px'}, t('cell_logic_note')));
}

function cellResultCard(r, fresh) {
  const act = r.bands.filter(b => b.active), nr = act.filter(b => b.carriers.some(c => c.tech === '5G NR'));
  const rows = r.bands.map(b => h('div', {cls: 'crow'},
    h('i', {cls: 'cdot', style: `background:${LEVEL_COL[b.active ? b.level : 'none']}`}),
    h('span', {cls: 'cn'}, b.name),
    h('span', {cls: 'cs'}, b.active ? `${levelWord(b.level)}` : t('cell_none')),
    h('span', {cls: 'cv', dir: 'ltr'}, b.active && b.best_rsrp != null ? `RSRP≈${Math.round(b.best_rsrp)} dBm` : ''),
    h('span', {cls: 'ct'}, b.active ? (b.carriers.some(c => c.tech === '5G NR') ? '5G' : (b.best_tech || '')) : '')));
  return box(`${fresh ? '✓ ' : ''}${r.label}${r.aborted ? ' — ' + t('ms_stopped') : ''}`,
    h('div', {cls: 'note'}, `${act.length}/${r.bands.length} ${t('cell_active')} · 5G: ${nr.length ? '✓ ' + nr.map(b => b.name).join(', ') : '✕'}` +
      (r.location ? '' : ' · ⚠ ' + t('gps_none')) + ` · ${r.images.length} ${t('cell_pics')}`),
    h('div', {cls: 'cres'}, ...rows),
    h('div', {cls: 'note'}, t('cell_rsrp_note')));
}

function cellPointModal(site, p) {
  modal((b, close) => {
    b.append(h('h3', null, `${site} — ${p.label}`),
      h('div', {cls: 'note'}, p.time ? new Date(p.time * 1000).toLocaleString() : '', ' · ',
        h('bdi', {dir: 'ltr'}, p.location ? `${p.location.lat.toFixed(6)}, ${p.location.lon.toFixed(6)}` : t('gps_none'))),
      h('div', {cls: 'cres'}, ...p.bands.map(bd => h('div', {cls: 'crow'},
        h('i', {cls: 'cdot', style: `background:${LEVEL_COL[bd.active ? bd.level : 'none']}`}),
        h('span', {cls: 'cn'}, bd.name), h('span', {cls: 'cs'}, bd.active ? levelWord(bd.level) : t('cell_none')),
        h('span', {cls: 'cv', dir: 'ltr'}, bd.active && bd.best_rsrp != null ? `≈${Math.round(bd.best_rsrp)} dBm` : ''),
        h('span', {cls: 'ct'}, bd.active ? bd.best_tech || '' : '')))),
      h('div', {cls: 'cimgs'}, ...p.images.map(n => h('img', {src: `/api/cell_file?site=${encodeURIComponent(site)}&point=${encodeURIComponent(p.label)}&name=${encodeURIComponent(n)}`,
        title: n, onclick: (e) => window.open(e.target.src, '_blank')}))),
      h('div', {cls: 'modal-actions'},
        btn(t('del'), async () => { if (await confirmBox(t('cell_del_q'))) { await api('cell_delete', {site, point: p.label}); close(); await cellLoad(); renderCell(); } }, 'danger'),
        btn('OK', () => close(), 'prim')));
  });
}

async function cellExport(site) {
  const r = await api('cell_export', {site});
  if (!r.ok) { notify(r.error || 'error', 'red'); return; }
  notify(`✓ ${r.path.split(/[\\/]/).pop()}`, 'blue', 5000);
  if (AND && AND.saveAs) AND.saveAs(r.path, r.path.split(/[\\/]/).pop(), 'application/zip');
}

async function cellShowMap(sid) {
  if (!sid) { const l = (await getJSON('/api/surveys')).list; const s = l.find(x => x.name === cellSite()); sid = s && s.id; }
  if (sid) { await api('survey', {op: 'select', id: sid}); MV.sid = sid; MV.sv = null; }
  MV.cellBand = MV.cellBand || '*best';
  setScreen('map');
}

// ---------------------------------------------------------------- runner
function renderCellRun(body) {
  const R = CELL.run;
  const done = R.views.filter(v => v.state !== 'wait').length;
  body.append(h('div', {cls: 'cell-run'},
    h('h3', null, `📍 ${t('cell_sampling')} ${R.label || ''}`),
    h('div', {cls: 'note'}, t('cell_still_long')),
    h('div', {cls: 'pbar'}, h('i', {style: `width:${Math.round(100 * done / R.views.length)}%`})),
    h('canvas', {id: 'cell-live'}),
    h('div', {cls: 'cviews'}, ...R.views.map((v, i) => h('div', {cls: 'cv ' + v.state},
      h('span', {cls: 'ic'}, {wait: '⏳', now: '🔎', act: '✓', empty: '·', skip: '✕'}[v.state]),
      h('span', {dir: 'ltr'}, `${mhz(v.lo)}–${mhz(v.hi)} MHz`),
      h('small', null, v.state === 'act' ? `${v.n} ${t('cell_carriers')}` + (v.pic ? ' 🖼' : '') : v.state === 'now' ? (v.note || '') : '')))),
    btn('■ ' + t('ms_stop'), () => { R.stop = true; notify(t('ms_stopped'), 'orange'); }, 'danger')));
}
let cellLiveT = 0;
function cellLiveDraw() {
  const cv = $('cell-live'); if (!cv || !S.fr) return;
  const r = cv.getBoundingClientRect(); if (r.width < 10) return;
  if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr); }
  drawSpectrumOn(cv, {...cellPlotP(), compact: true});
}
function cellPlotP(carriers) {
  const fr = S.fr, tr = fr ? fr.tr : [];
  let mx = -200, mn = 50;
  for (const d of tr) if (d) for (const v of d) if (isFinite(v)) { if (v > mx) mx = v; if (v < mn) mn = v; }
  const ref = Math.max(-60, Math.ceil((mx + 8) / 10) * 10);
  return {c: S.view.c, s: S.view.s, ref, div: 10, bands: true, freqs: fr ? fr.freqs : null,
    tr: [null, tr[1] || null, tr[2] || null, null],
    shade: (carriers || []).map(c => [c.lo, c.hi, 'rgba(53,224,122,0.16)']),
    watermark: isSim() ? t('src_sim').toUpperCase() : ''};
}
async function cellJpeg(R, w, v, idx) {
  const P = cellPlotP(v.carriers);
  const loc = MV.loc && MV.loc.age < 30 ? MV.loc : null;
  const foot = v.carriers.length ? v.carriers.map(c => `${mhz(c.center)} MHz · ${mhz(c.bw)} MHz ${c.tech} · ch ${c.ch_dbm.toFixed(1)} dBm · RSRP≈${Math.round(c.rsrp)} dBm (${levelWord(c.level)})`).slice(0, 3)
    : [t('cell_none')];
  const blob = await liveJpeg({P: {...P, font: 11}, sim: isSim(),
    title: `${cellSite()} — ${R.label} — ${mhz(w.lo)}–${mhz(w.hi)} MHz`,
    line1: new Date().toLocaleString() + '   ·   ' + (w.bands || []).map(k => (CELL.plan.bands.find(b => b.key === k) || {}).name).join(' · '),
    line2: locText(loc), foot});
  const name = `${pad2(idx + 1)} ${mhz(w.lo)}-${mhz(w.hi)} MHz.jpg`;
  await fetch(`/api/cell_img?run=${encodeURIComponent(R.run)}&name=${encodeURIComponent(name)}`, {method: 'POST', body: blob});
}

async function cellRun() {
  if (CELL.run || (typeof MS !== 'undefined' && MS.run)) { notify(t('ms_busy'), 'orange'); return; }
  await cellLoad();
  const plan = CELL.plan, cfg = plan.cfg;
  const prev = {center: S.set.center, span: S.set.span, traces: [...S.set.traces], avg_count: S.set.avg_count,
    rbw_auto: S.set.rbw_auto, gain: S.set.gain, running: S.set.running};
  const R = CELL.run = {stop: false, label: '', views: plan.windows.map(w => ({...w, state: 'wait'}))};
  if (AND && AND.keepScreenOn) AND.keepScreenOn(true);
  renderCell();
  cellLiveT = setInterval(cellLiveDraw, 250);
  let result = null;
  try {
    pushSet({traces: ['Live', 'Max Hold', 'Average', 'Off'], avg_count: Math.max(3, cfg.sweeps), rbw_auto: true, running: true}, true);
    S.set.running = true;
    const st = await api('cell_start', {site: cfg.site, label: t('cell_point_word'), lang: S.ui.lang});
    R.run = st.run; R.label = st.label; renderCell();
    for (let i = 0; i < st.windows.length && !R.stop; i++) {
      const w = st.windows[i], V = R.views[i];
      V.state = 'now'; renderCell();
      let v = null;
      for (let attempt = 0; attempt < 3 && !R.stop; attempt++) {
        tune(w.center, 100e6, true);
        await sleep(120);
        await api('restart'); WF.key = '';
        // wait for frames of THIS view, then enough sweeps of it
        const t0 = Date.now(); let seq0 = -1;
        while (!R.stop && Date.now() - t0 < 25000) {
          await sleep(150);
          const fr = S.fr;
          if (!fr || Math.abs(fr.h.center - w.center) > 1 || Math.abs(fr.h.span - 100e6) > 1) continue;
          if (seq0 < 0) seq0 = fr.h.seq;
          if (fr.h.seq - seq0 >= cfg.sweeps + 1) break;
        }
        for (let k = 0; k < 8 && !R.stop; k++) {
          v = await api('cell_view', {run: R.run, idx: i});
          if (v.ok || !v.retry) break;
          await sleep(300);
        }
        // overload: too much signal for this gain -> 10 dB less, measure again
        if (v && v.ok && v.overload && S.set.gain > (S.st.source.gain_min || 0) + 5) {
          V.note = 'Gain −10'; renderCell();
          pushSet({gain: Math.max(S.st.source.gain_min || 0, S.set.gain - 10)}, true);
          await sleep(300);
          continue;
        }
        break;
      }
      if (R.stop) break;
      if (!v || !v.ok) { V.state = 'skip'; renderCell(); continue; }
      V.n = v.carriers.length; V.state = v.active ? 'act' : 'empty';
      if (v.save_img) { await cellJpeg(R, w, v, i); V.pic = true; }
      renderCell();
    }
    if (R.run) result = await api('cell_finish', {run: R.run, aborted: R.stop});
  } catch (e) {
    notify(`${t('ms_stopped')}: ${e.message || e}`, 'red', 8000);
  } finally {
    clearInterval(cellLiveT);
    pushSet({center: prev.center, span: prev.span, traces: prev.traces, avg_count: prev.avg_count,
      rbw_auto: prev.rbw_auto, gain: prev.gain, running: prev.running}, true);
    S.view = {c: prev.center, s: prev.span};
    if (AND && AND.keepScreenOn) AND.keepScreenOn(S.ui.keepOn !== false);
    CELL.run = null;
    if (result && result.ok) {
      CELL.last = result;
      const act = result.bands.filter(b => b.active).length;
      notify(`✓ ${result.label}: ${act}/${result.bands.length} ${t('cell_active')} · ${result.images.length} ${t('cell_pics')}`, 'blue', 8000);
      if (!result.location) notify(t('rec_no_loc'), 'orange', 8000);
    }
    await cellLoad();
    if (SCR.cur === 'cell') renderCell();
  }
}

// ---------------------------------------------------------- plan panel
PANELS.cellset = (b) => {
  const P = CELL.plan; if (!P) return;
  const cfg = P.cfg;
  const set = async (d) => { CELL.plan = await api('cell_cfg', d); renderPanel(); if (SCR.cur === 'cell') renderCell(); };
  b.append(box(t('cell_country'), h('div', {cls: 'seg'}, ...Object.keys(P.countries).map(k =>
    btn(countryName(k), () => set({country: k, disabled: []}), 'sm' + (cfg.country === k ? ' on' : '')))),
    h('div', {cls: 'note'}, t('cell_country_note'))));
  const list = h('div', {cls: 'list'});
  const inPlan = P.countries[cfg.country].bands;
  for (const k of inPlan) {
    const bd = P.all.find(x => x.key === k); if (!bd) continue;
    const on = !cfg.disabled.includes(k);
    list.append(h('div', {cls: 'li'}, chk(on, (v) => set({disabled: v ? cfg.disabled.filter(x => x !== k) : [...cfg.disabled, k]}), ''),
      h('div', {cls: 't'}, h('b', null, bd.name), h('small', {dir: 'ltr'}, `DL ${mhz(bd.lo)}–${mhz(bd.hi)} MHz · ${bd.tech.toUpperCase()}`))));
  }
  for (const c of cfg.custom) list.append(h('div', {cls: 'li'}, h('div', {cls: 't'}, h('b', null, '★ ' + c.name),
    h('small', {dir: 'ltr'}, `DL ${mhz(c.lo)}–${mhz(c.hi)} MHz`)), btn('🗑', () => set({custom: cfg.custom.filter(x => x !== c)}), 'sm danger')));
  b.append(box(`${t('cell_bands_t')} · ${P.windows.length} ${t('cell_views')}`, list,
    btn('＋ ' + t('cell_add_band'), () => modal((bb, close) => {
      const nm = h('input', {type: 'text', placeholder: 'n1 2100'}), lo = h('input', {type: 'text', cls: 'ltr', placeholder: '2110'}),
        hi = h('input', {type: 'text', cls: 'ltr', placeholder: '2170'});
      bb.append(h('h3', null, t('cell_add_band')), row(t('pr_name'), nm), row('DL ' + t('start'), lo), row('DL ' + t('stop'), hi),
        h('div', {cls: 'note'}, t('hz_note')),
        h('div', {cls: 'modal-actions'}, btn(t('cancel'), () => close()), btn(t('save'), async () => {
          const a = parseHz(lo.value), z = parseHz(hi.value);
          if (!nm.value.trim() || a == null || z == null || z <= a) return;
          close(); await set({custom: [...cfg.custom, {name: nm.value.trim(), lo: a, hi: z, tech: 'lte/nr'}]});
        }, 'prim')));
    }), 'sm')));
  b.append(box(t('cell_detect'),
    row(t('cell_thr'), sel([4, 5, 6, 8, 10, 12].map(v => [v, v + ' dB']), cfg.thr, (v) => set({thr: +v}))),
    row(t('cell_sweeps'), sel([2, 3, 5, 8].map(v => [v, String(v)]), cfg.sweeps, (v) => set({sweeps: +v}))),
    chk(cfg.save_empty, (v) => set({save_empty: v}), t('cell_save_empty')),
    h('div', {cls: 'note'}, t('cell_detect_note'))));
};
