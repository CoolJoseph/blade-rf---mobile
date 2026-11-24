// UI smoke test in a phone-sized Chromium, portrait and landscape, Hebrew
// and English: opens every panel, uses every gesture, and fails on any
// JavaScript error. Start the engine first:
//   python android/app/src/main/python/mobile_app.py --sim --port 8765
// then:  node tests/test_ui.js [outdir]
const { chromium } = require('playwright');
(async () => {
  const out = process.argv[2] || '.';
  const url = 'http://127.0.0.1:8765/';
  const b = await chromium.launch();
  const errs = [];
  for (const [name, vp] of [['portrait', {width: 393, height: 852}], ['landscape', {width: 852, height: 393}]]) {
    const ctx = await b.newContext({viewport: vp, deviceScaleFactor: 1, isMobile: true, hasTouch: true});
    const p = await ctx.newPage();
    p.on('pageerror', e => errs.push(`${name}: ${e}`));
    p.on('console', m => { if (m.type() === 'error') errs.push(`${name}: ${m.text()}`); });
    await p.goto(url); await p.waitForTimeout(2000);
    for (const lang of ['he', 'en']) {
      await p.evaluate((l) => setLang(l), lang);
      for (const k of ['freq', 'presets', 'bw', 'ampl', 'traces', 'display', 'markers', 'signals', 'radio', 'capture', 'settings']) {
        await p.click(`.key[data-panel=${k}]`); await p.waitForTimeout(250);
        if (!(await p.isVisible('#sheet'))) errs.push(`${name}/${lang}: panel ${k} did not open`);
      }
      await p.click('#sheet-close');
    }
    for (const a of ['peak', 'next', 'single', 'single', 'restart']) { await p.click(`[data-act=${a}]`); await p.waitForTimeout(150); }
    const box = await p.locator('#spec').boundingBox();
    await p.mouse.move(box.x + box.width * 0.6, box.y + 100); await p.mouse.down();
    for (let i = 1; i <= 8; i++) await p.mouse.move(box.x + box.width * 0.6 - i * 12, box.y + 100);
    await p.mouse.up(); await p.waitForTimeout(400);
    await p.mouse.click(box.x + box.width * 0.5, box.y + 120); await p.waitForTimeout(200);
    await p.mouse.move(box.x + box.width * 0.4, box.y + 120); await p.mouse.down(); await p.waitForTimeout(700); await p.mouse.up();
    await p.waitForTimeout(500);
    await p.screenshot({path: `${out}/ui_${name}.png`});
    await ctx.close();
  }
  await b.close();
  console.log(errs.length ? 'FAIL\n' + errs.join('\n') : 'UI PASS (no JS errors, every panel opened)');
  process.exit(errs.length ? 1 : 0);
})();
