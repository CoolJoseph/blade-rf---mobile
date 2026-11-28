// v1.3 cellular survey: field mode, sample a point (simulator), result,
// files, map layer. Engine first (fresh data dir), then: node tests/test_cell.js [outdir]
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const out = process.argv[2] || '.';
  const b = await chromium.launch();
  const errs = [], ok = (c, m) => { if (!c) errs.push(m); };
  const ctx = await b.newContext({viewport: {width: 393, height: 852}, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    geolocation: {latitude: 32.0712, longitude: 34.7905}, permissions: ['geolocation']});
  const p = await ctx.newPage();
  p.on('pageerror', e => errs.push(`${e}`));
  p.on('console', m => { if (m.type() === 'error') errs.push(`console ${m.text()}`); });
  await p.goto('http://127.0.0.1:8765/'); await p.waitForTimeout(2500);
  await p.evaluate(() => setLang('he'));
  await p.click('#mode-field'); await p.waitForTimeout(300);
  ok(!(await p.isVisible('.tile.t-spec')), 'field mode: spectrum tile still shown');
  await p.screenshot({path: `${out}/c1_home_field.png`});
  await p.click('.tile.t-cell'); await p.waitForTimeout(1200);
  await p.fill('#cell-body input[type=text]', 'בית ספר אורט'); await p.press('#cell-body input[type=text]', 'Enter');
  await p.locator('#cell-body h3').click(); await p.waitForTimeout(800);
  await p.screenshot({path: `${out}/c2_cell_ready.png`});
  for (let k = 0; k < 2; k++) {
    await p.click('.cell-go'); await p.waitForTimeout(2500);
    if (k === 0) await p.screenshot({path: `${out}/c3_running.png`});
    for (let i = 0; i < 120 && await p.evaluate(() => !!CELL.run); i++) await p.waitForTimeout(500);
    await p.evaluate(async ({k}) => { await api('location', {lat: 32.0712 + k * 0.0004, lon: 34.7905 + k * 0.0003, acc: 4, src: 'gps'}); }, {k: k + 1});
  }
  await p.waitForTimeout(800);
  await p.screenshot({path: `${out}/c4_result.png`, fullPage: true});
  const last = await p.evaluate(() => CELL.last);
  ok(last && last.ok, 'no result');
  ok(last && last.bands.filter(b => b.active).length >= 5, 'too few active bands: ' + JSON.stringify(last && last.bands.map(b => [b.name, b.active])));
  ok(last && last.images.length >= 5 && last.images.length <= 8, 'pictures: ' + (last && last.images.length));
  ok(last && last.label === 'נקודה 2', 'label ' + (last && last.label));
  await p.locator('#cell-body .list .li').first().click(); await p.waitForTimeout(1200);
  await p.screenshot({path: `${out}/c5_point_modal.png`});
  await p.locator('#modal .modal-actions .btn.prim').click();
  await p.locator('#cell-body .btn', {hasText: /מפה/}).first().click(); await p.waitForTimeout(3000);
  ok(await p.isVisible('#m-cell'), 'map: no cellular layer chip');
  await p.screenshot({path: `${out}/c6_map_best.png`});
  await p.selectOption('#m-cell', '*5g'); await p.waitForTimeout(800);
  await p.screenshot({path: `${out}/c7_map_5g.png`});
  await p.evaluate(() => setLang('en')); await p.evaluate(() => setScreen('cell')); await p.waitForTimeout(500);
  await p.evaluate(() => openPanel('cellset')); await p.waitForTimeout(500);
  await p.screenshot({path: `${out}/c8_plan.png`});
  await b.close();
  console.log(errs.length ? 'FAIL\n' + errs.join('\n') : 'CELL PASS');
  process.exit(errs.length ? 1 : 0);
})();
