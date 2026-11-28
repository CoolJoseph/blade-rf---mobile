/* NETLINE Spectrum Analyzer -- screens: Home, Spectrum, Missions, Map.
 *
 * Also owns the location status: the GPS chip in the top bar, the red bar
 * that cannot be missed when location is refused or switched off, and the
 * "Location" box in Settings. Loaded last, after app.js / map.js /
 * record.js / missions.js.
 */
'use strict';

const SCR = {cur: 'home'};
const SCREENS = {home: 'homeview', spec: null, cell: 'cellview', missions: 'missionview', map: 'mapview'};

function setScreen(name) {
  if (name === 'recordings') { setScreen('spec'); openPanel('capture'); return; }
  if (name === 'radio' || name === 'settings') { setScreen('spec'); openPanel(name); return; }
  if (!(name in SCREENS)) name = 'spec';
  SCR.cur = name;
  for (const n of ['home', 'spec', 'cell', 'missions', 'map']) document.body.classList.toggle('scr-' + n, n === name);
  $('homeview').classList.toggle('hidden', name !== 'home');
  $('main').classList.toggle('hidden', name !== 'spec');
  $('presetbar').classList.toggle('hidden', name !== 'spec');
  $('missionview').classList.toggle('hidden', name !== 'missions');
  $('cellview').classList.toggle('hidden', name !== 'cell');
  $('mapview').classList.toggle('hidden', name !== 'map');
  for (const [id, n] of [['tab-cell', 'cell'], ['tab-spec', 'spec'], ['tab-missions', 'missions'], ['tab-map', 'map']]) $(id).classList.toggle('on', n === name);
  closePanel();
  mapActivate(name === 'map');
  if (name === 'spec') requestAnimationFrame(() => { applyLayout(); resizeCanvases(); });
  if (name === 'missions') renderMissions();
  if (name === 'cell') renderCell();
  if (name === 'home') renderHome();
}

// ------------------------------------------------------------------ home
function renderHome() {
  const st = S.st; if (!st) return;
  const src = st.source, box = $('home-status'); box.textContent = '';
  const pill = (cls, text) => h('div', {cls: 'hs ' + cls}, h('span', {cls: 'dot'}), h('span', null, text));
  box.append(src.radio && !src.lost ? pill('ok', `bladeRF ${t('connected')}`) :
    src.lost ? pill('bad', t('src_lost')) : pill('warn', t('src_sim')));
  const L = locState();
  box.append(pill(L.cls === 'ok' ? 'ok' : L.cls === 'none' ? 'bad' : 'warn', L.text));
  if (MS.run) box.append(pill('bad', `● ${t('ms_running')}: ${MS.run.m.name}`));
  const red = [...AL.active.values()].filter(a => a.level === 'red').length;
  if (red) box.append(h('button', {cls: 'hs bad', onclick: () => { setScreen('spec'); openPanel('alerts'); }}, h('span', {cls: 'dot'}), `${red} ⛔`));
  applyMode();
  $('home-foot').textContent = `${st.app_name || 'NETLINE Spectrum Analyzer'} v${st.version} · engine ${String(st.core_version || '').split(' ').slice(0, 3).join(' ')}`;
  $('h-lang').textContent = S.ui.lang === 'he' ? 'EN' : 'עב';
}
for (const b of document.querySelectorAll('.tile[data-go]')) b.addEventListener('click', () => setScreen(b.dataset.go));
$('h-lang').onclick = () => { setLang(S.ui.lang === 'he' ? 'en' : 'he'); renderHome(); };
$('b-home').onclick = () => setScreen('home');
$('tab-spec').onclick = () => setScreen('spec');
$('tab-cell').onclick = () => setScreen('cell');
$('tab-missions').onclick = () => setScreen('missions');
$('tab-map').onclick = () => setScreen('map');

// ------------------------------------------------------------ user mode
// Field = someone who only walks a site and samples: Cellular survey, Map,
// Recordings. Expert = everything. Only the menus change.
function applyMode() {
  const field = S.ui.mode === 'field';
  document.body.classList.toggle('field', field);
  $('mode-field').classList.toggle('on', field); $('mode-expert').classList.toggle('on', !field);
}
$('mode-field').onclick = () => { S.ui.mode = 'field'; saveUi(); applyMode(); };
$('mode-expert').onclick = () => { S.ui.mode = 'expert'; saveUi(); applyMode(); };

// -------------------------------------------------------------- location
// Android tells us the permission and provider state; a browser only tells
// us when it refuses (LOC_ERR, set by map.js's geolocation watcher).
function nativeLocStatus() {
  if (AND && AND.locationStatus) { try { return JSON.parse(AND.locationStatus()); } catch (e) { /* */ } }
  return null;
}
function locState() {
  const loc = MV.loc, ns = nativeLocStatus();
  if (loc && loc.src === 'sim' && loc.age <= 15) return {cls: 'weak', text: t('gps_sim')};
  if (ns && ns.perm !== 'granted') return {cls: 'none', text: t('loc_state_denied'), bar: t('loc_bar_denied'), fixable: true};
  if (ns && !ns.gps && !ns.net) return {cls: 'none', text: t('gps_off'), bar: t('loc_bar_gps'), fixable: true};
  if (!ns && window.LOC_ERR === 1) return {cls: 'none', text: t('loc_state_denied'), bar: t('loc_bar_pc'), fixable: false};
  if (loc && loc.src === 'sim') return {cls: 'weak', text: t('gps_sim')};
  if (loc && loc.age <= 15) return {cls: (loc.acc || 99) > 25 ? 'weak' : 'ok', text: `GPS ±${Math.round(loc.acc || 0)} m`};
  if (ns && !ns.gps) return {cls: 'weak', text: t('gps_off'), bar: t('loc_bar_gps'), fixable: true};
  if (!ns && window.LOC_ERR) return {cls: 'none', text: t('gps_none'), hint: t('loc_pc_none')};
  return {cls: 'weak', text: t('loc_state_wait')};
}
function updateLocUi() {
  const L = locState(), chip = $('b-gps');
  chip.className = 'chip gps ' + L.cls;
  // the top bar is narrow on a phone: the full words live in Home, Settings and the red bar
  $('gps-label').textContent = (document.body.classList.contains('portrait') || innerWidth < 1100) && L.cls !== 'ok' ? (L.cls === 'none' ? 'GPS ✕' : 'GPS …') : L.text;
  chip.title = L.text;
  const bar = $('locbar');
  if (L.bar) {
    bar.textContent = '';
    bar.append(h('span', null, L.bar));
    if (L.fixable) bar.append(h('span', {cls: 'fix'}, t('loc_fix')));
    bar.classList.remove('hidden');
  } else bar.classList.add('hidden');
  if (L.cls === 'none') alertSet('loc', 'red', L.text, L.bar || L.hint || ''); else alertClear('loc');
  if (SCR.cur === 'home') renderHome();
}
function fixLocation() {
  if (AND && AND.fixLocation) { AND.fixLocation(); return; }
  startLocation();
  modal((b, close) => {
    b.append(h('h3', null, t('loc_box')), h('div', {cls: 'note', style: 'white-space:pre-wrap'}, t('loc_pc_none')),
      h('div', {cls: 'modal-actions'}, btn('OK', () => close(), 'prim')));
  });
}
$('locbar').onclick = fixLocation;
$('b-gps').onclick = () => {
  const L = locState();
  if (L.cls === 'none' || L.bar) { fixLocation(); return; }
  const loc = MV.loc;
  if (loc) notify(`${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}` + (loc.acc ? ` ±${Math.round(loc.acc)} m` : '') + ` (${loc.src})`, 'blue', 5000);
  else notify(L.text, 'orange', 3000);
};
// Settings panel box
function locStatusBox() {
  const L = locState(), loc = MV.loc;
  return box(t('loc_box'),
    h('div', {cls: 'row'}, h('span', {cls: 'chip gps ' + L.cls}, h('span', {cls: 'dot'}), h('span', null, L.text))),
    loc ? h('div', {cls: 'note ltr'}, `${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}` + (loc.acc ? ` ±${Math.round(loc.acc)} m` : '') + ` · ${loc.src}`) : null,
    L.bar ? h('div', {cls: 'note warn'}, L.bar) : L.hint ? h('div', {cls: 'note'}, L.hint) : h('div', {cls: 'note'}, t('gps_answer')),
    L.cls !== 'ok' ? btn(t('loc_fix'), fixLocation, 'prim') : null);
}

// ---------------------------------------------------------------- start
(async function startScreens() {
  for (let i = 0; i < 100 && !S.st; i++) await sleep(100);
  startLocation();                       // location from the very start, not only on the map
  setInterval(updateLocUi, 1500);
  updateLocUi();
  setScreen('home');
  setInterval(() => { if (SCR.cur === 'home') renderHome(); }, 2000);
  setInterval(() => { if (SCR.cur === 'cell' && !CELL.run && !$('cell-body').contains(document.activeElement)) renderCell(); }, 3000);
})();
const _applyLang0 = applyLang;
applyLang = function () { _applyLang0(); if (typeof SCR !== 'undefined') { updateLocUi(); if (SCR.cur === 'missions') renderMissions(); if (SCR.cur === 'cell') renderCell(); applyMode(); } };
