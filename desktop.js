/* Desktop edition glue.
 *
 * The same page runs on the PC, opened by the Windows launcher with
 * ?desktop=1. What the Android activity provides on the phone (window.Android)
 * is provided here by the engine's /api/desktop calls: native Save-as and
 * Open dialogs, "show in folder", keep-awake. Loaded BEFORE app.js, which
 * reads window.Android once at start.
 */
'use strict';
const DESKTOP = /[?&]desktop=1/.test(location.search);
if (DESKTOP) {
  const post = async (o) => (await fetch('/api/desktop', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(o)})).json();
  const say = (m) => window.nativeToast && window.nativeToast(m);
  window.Android = {
    setOrientation() {},
    keepScreenOn(on) { post({op: 'keep_awake', on: !!on}); },
    async requestUsb() {
      const r = await (await fetch('/api/connect', {method: 'POST', body: '{}'})).json();
      if (!r.radio) say(r.error ? r.error.split('\n')[0] : 'bladeRF not found');
      if (window.onUsbResult) window.onUsbResult();
    },
    share(path) { post({op: 'reveal', path}); },
    async saveAs(path, name, mime) {
      const r = await post({op: 'saveas', path, name, mime});
      if (r.ok) say('✓ ' + r.path); else if (r.error) say(r.error);
    },
    async pickFpga() {
      const r = await post({op: 'pick_fpga'});
      if (r.ok) { say('FPGA ✓ ' + r.path); this.requestUsb(); } else if (r.error) say(r.error);
    },
    async pickPresets() {
      const r = await post({op: 'pick_presets'});
      if (r.ok && window.onPresetsPicked) window.onPresetsPicked(r.text); else if (r.error) say(r.error);
    },
  };
  // "Share" on a PC means: show the file in Explorer
  I18N.en.share = 'Show in folder';
  I18N.he.share = 'הצג בתיקייה';

  document.addEventListener('DOMContentLoaded', () => {
    document.body.classList.add('desktop');
    document.title = 'YT Spectrum Analyzer';
    const rot = document.getElementById('b-rot'); if (rot) rot.style.display = 'none';
    // right-click = the phone's long-press: identify the signal under the mouse
    const spec = document.getElementById('spec');
    spec.addEventListener('contextmenu', (e) => { e.preventDefault(); identifyAt(xf(e.offsetX, plotRect())); });
  });

  // keyboard, the way a bench analyzer's front panel is driven from a PC
  document.addEventListener('keydown', (e) => {
    const tag = (e.target && e.target.tagName) || '';
    if (/INPUT|SELECT|TEXTAREA/.test(tag)) return;
    if (typeof S === 'undefined' || !S.set) return;
    const k = e.key;
    const done = () => e.preventDefault();
    if (k === 'Escape') { window.onBack && window.onBack(); return done(); }
    if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 's') { recordDialog({}); return done(); }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (k === ' ') { document.getElementById('b-run').click(); return done(); }
    if (k === 'p' || k === 'P') { doAct('peak'); return done(); }
    if (k === 'n' || k === 'N') { doAct('next'); return done(); }
    if (k === 's' || k === 'S') { doAct('single'); return done(); }
    if (k === 'r' || k === 'R') { doAct('restart'); return done(); }
    if (k === 'm' || k === 'M') { openPanel('markers'); return done(); }
    if (k === 'f' || k === 'F') { openPanel('freq'); return done(); }
    if (k === 'ArrowLeft') { tune(S.view.c - S.view.s / 10, S.view.s, true); return done(); }
    if (k === 'ArrowRight') { tune(S.view.c + S.view.s / 10, S.view.s, true); return done(); }
    if (k === '+' || k === '=') { tune(S.view.c, S.view.s / 2, true); return done(); }
    if (k === '-' || k === '_') { tune(S.view.c, S.view.s * 2, true); return done(); }
    if (k === 'PageUp') { setGain(S.set.gain + 1); return done(); }
    if (k === 'PageDown') { setGain(S.set.gain - 1); return done(); }
    if (k === 'h' || k === 'H') { setScreen('home'); return done(); }
    if (k === 'ArrowUp') { S.ui.ref += 5; WF.key = ''; saveUi(); S.dirty = true; return done(); }
    if (k === 'ArrowDown') { S.ui.ref -= 5; WF.key = ''; saveUi(); S.dirty = true; return done(); }
  });
}
