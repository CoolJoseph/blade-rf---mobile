// v1.2 flows: home screen, screens, alerts, gain drag, save recording (all
// kinds incl. video), missions (create, continuous warning, run once),
// location bar. Engine first:
//   python android/app/src/main/python/mobile_app.py --sim --port 8765
// then:  node tests/test_v12.js [outdir]
const { chromium } = require('playwright');
(async () => {
  const out = process.argv[2] || '.';
  const url = 'http://127.0.0.1:8765/';
  const b = await chromium.launch();
  const errs = [];
  const ok = (c, m) => { if (!c) errs.push(m); };
  for (const [name, vp] of [['portrait', {width: 393, height: 852}], ['landscape', {width: 852, height: 393}]]) {
    const ctx = await b.newContext({viewport: vp, deviceScaleFactor: 2, isMobile: true, hasTouch: true});
    const p = await ctx.newPage();
    p.on('pageerror', e => errs.push(`${name}: ${e}`));
    p.on('console', m => { if (m.type() === 'error') errs.push(`${name}: console ${m.text()}`); });
    await p.goto(url); await p.waitForTimeout(2500);
    await p.evaluate(async () => { await api('set', {gain: 30, sim_gps: false}); S.set.gain = 30;
      for (const m of await (await fetch('/api/missions')).json()) await api('mission', {op: 'delete', id: m.id}); });
    for (const lang of ['he', 'en']) {
      await p.evaluate((l) => setLang(l), lang); await p.waitForTimeout(300);
      ok(await p.isVisible('#homeview'), `${name}/${lang}: home not shown at start`);
      await p.screenshot({path: `${out}/v12_${name}_${lang}_home.png`});
      await p.click('.tile[data-go=spec]'); await p.waitForTimeout(800);
      ok(await p.isVisible('#spec'), `${name}: spectrum not shown`);
      await p.screenshot({path: `${out}/v12_${name}_${lang}_spec.png`});
      await p.click('#b-home'); await p.waitForTimeout(300);
    }
    // gain by vertical drag
    await p.click('.tile[data-go=spec]'); await p.waitForTimeout(600);
    const g0 = await p.evaluate(() => S.set.gain);
    const box = await p.locator('#spec').boundingBox();
    await p.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.7); await p.mouse.down();
    for (let i = 1; i <= 10; i++) await p.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.7 - i * 9);
    await p.mouse.up(); await p.waitForTimeout(400);
    const g1 = await p.evaluate(() => S.set.gain);
    ok(g1 > g0, `${name}: drag up did not raise gain (${g0} -> ${g1})`);
    const c0 = await p.evaluate(() => S.view.c);
    await p.mouse.move(box.x + box.width * 0.6, box.y + 100); await p.mouse.down();
    for (let i = 1; i <= 8; i++) await p.mouse.move(box.x + box.width * 0.6 - i * 12, box.y + 100);
    await p.mouse.up(); await p.waitForTimeout(400);
    ok(await p.evaluate((c) => S.view.c !== c, c0), `${name}: sideways drag did not tune`);
    // panels still open
    for (const k of ['freq', 'ampl', 'markers', 'radio', 'capture', 'settings']) {
      await p.click(`.key[data-panel=${k}]`); await p.waitForTimeout(200);
      ok(await p.isVisible('#sheet'), `${name}: panel ${k}`);
    }
    await p.click('#sheet-close');
    // location bar (headless browser refuses geolocation)
    for (let i = 0; i < 20 && !(await p.isVisible('#locbar')); i++) await p.waitForTimeout(1000);   // a simulated fix lasts 15 s
    ok(await p.isVisible('#locbar'), `${name}: location-refused bar not shown`);
    // alerts: simulator alert is orange and opens the list
    ok(await p.evaluate(() => AL.active.has('sim') && AL.active.get('sim').level === 'orange'), `${name}: no orange simulator alert`);
    ok(await p.locator('#alerts .al').count() > 0, `${name}: no alert pill/badge`);
    await p.locator('#alerts .al').first().click(); await p.waitForTimeout(300);
    ok((await p.textContent('#sheet-title')).length > 0 && await p.isVisible('#sheet'), `${name}: alerts panel`);
    await p.screenshot({path: `${out}/v12_${name}_alerts.png`});
    await p.click('#sheet-close');
    if (name === 'portrait') {
      // save recording, everything
      await p.click('[data-act=record]'); await p.waitForTimeout(500);
      await p.screenshot({path: `${out}/v12_${name}_recdialog.png`});
      await p.locator('#modal .row .btn').first().click();     // "Everything"
      await p.locator('#modal .modal-actions .btn.prim').click();
      await p.waitForTimeout(9000);
      const files = await p.evaluate(async () => (await (await fetch('/api/captures')).json()).map(f => f.name));
      for (const ext of ['.jpg', '.csv', '.json']) ok(files.some(f => f.endsWith(ext)), `record: no ${ext} in ${files}`);
      ok(files.some(f => f.endsWith('.webm')), `record: no video in ${files}`);
      // missions: new, continuous warning, save, run once
      await p.click('#tab-missions'); await p.waitForTimeout(500);
      await p.locator('#mission-body .mhead .btn.prim').click(); await p.waitForTimeout(400);
      await p.fill('#mission-body input[type=text]', 'Roof test');
      const picks = p.locator('#mission-body .bandpick input');
      await picks.nth(3).check(); await picks.nth(5).check();
      await p.locator('#mission-body .seg .btn', {hasText: /Continuous|רציף/}).click(); await p.waitForTimeout(400);
      ok(await p.isVisible('#modal .warnbox'), 'continuous: no are-you-sure');
      await p.screenshot({path: `${out}/v12_${name}_contwarn.png`});
      await p.locator('#modal .modal-actions .btn').first().click(); await p.waitForTimeout(300);   // cancel
      ok(await p.evaluate(() => MS.edit.repeat) === 'once', 'continuous cancel did not revert');
      await p.evaluate(() => { MS.edit.dwell = 2; MS.edit.outputs.video = false; });
      await p.screenshot({path: `${out}/v12_${name}_editor.png`, fullPage: true});
      await p.locator('#mission-body .modal-actions .btn.prim').first().click(); await p.waitForTimeout(600);
      await p.screenshot({path: `${out}/v12_${name}_missions.png`});
      const n0 = (await p.evaluate(async () => (await (await fetch('/api/captures')).json()).length));
      await p.locator('#mission-body .mcard .btn.prim').first().click();
      await p.waitForTimeout(3500);
      ok(await p.isVisible('#runbar'), 'mission: run bar not shown');
      await p.screenshot({path: `${out}/v12_${name}_running.png`});
      for (let i = 0; i < 40 && await p.evaluate(() => !!MS.run); i++) await p.waitForTimeout(500);
      ok(await p.evaluate(() => !MS.run), 'mission did not finish');
      const n1 = (await p.evaluate(async () => (await (await fetch('/api/captures')).json()).length));
      ok(n1 - n0 >= 4, `mission saved ${n1 - n0} files, expected >= 4`);
      await p.click('#tab-map'); await p.waitForTimeout(2000);
      await p.screenshot({path: `${out}/v12_${name}_map.png`});
    }
    await ctx.close();
  }
  await b.close();
  console.log(errs.length ? 'FAIL\n' + errs.join('\n') : 'V1.2 PASS');
  process.exit(errs.length ? 1 : 0);
})();
