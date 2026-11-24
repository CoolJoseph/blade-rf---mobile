// Map tab end-to-end: save a named point, record a walk, upload an area
// image, calibrate it with 2 points, open a saved point and a walk point.
// Start the engine with --sim first (see test_ui.js). node tests/test_map.js [outdir]
const { chromium } = require('playwright');
const OUT = process.argv[2] || '.';
(async () => {
  const b = await chromium.launch();
  const errs = [];
  const ctx = await b.newContext({viewport: {width: 393, height: 852}, deviceScaleFactor: 2, isMobile: true, hasTouch: true});
  const p = await ctx.newPage();
  p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type()==='error') errs.push(m.text()); });
  p.on('dialog', d => d.dismiss());
  await p.goto('http://127.0.0.1:8765/'); await p.waitForTimeout(2000);
  await p.evaluate(() => api('set', {sim_gps: true}));
  await p.click('.pchip >> nth=7'); await p.waitForTimeout(1500);   // B3 DL
  await p.click('#tab-map'); await p.waitForTimeout(2500);
  await p.screenshot({path: `${OUT}/m1_map.png`});
  // save a named point
  await p.click('#m-save'); await p.waitForTimeout(400);
  await p.screenshot({path: `${OUT}/m2_savedlg.png`});
  await p.fill('#modal-body input', 'Corner A');
  await p.click('#modal-body .btn.prim'); await p.waitForTimeout(2500);
  // record a walk for ~8 s
  await p.click('#m-track'); await p.waitForTimeout(8000);
  await p.click('#m-track'); await p.waitForTimeout(1500);
  await p.screenshot({path: `${OUT}/m3_walk.png`});
  // upload base image and calibrate with 2 points
  await p.click('#m-layers'); await p.waitForTimeout(400);
  const png = await p.evaluate(() => { const c = document.createElement('canvas'); c.width = 1000; c.height = 700;
    const x = c.getContext('2d'); x.fillStyle = '#344630'; x.fillRect(0, 0, 1000, 700); x.fillStyle = '#999';
    for (let i = 0; i < 8; i++) x.fillRect(10 + i * 120, 40, 80, 110); x.fillStyle = '#5a5a5a'; x.fillRect(0, 330, 1000, 30); x.fillRect(480, 0, 24, 700);
    return c.toDataURL('image/png').split(',')[1]; });
  await p.setInputFiles('#sheet-body input[type=file]', {name: 'area.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64')}); await p.waitForTimeout(2500); await p.screenshot({path: `${OUT}/m35_afterupload.png`}); 
  const box = await p.locator('#map').boundingBox();
  const loc = await p.evaluate(() => MV.loc);
  for (const [fx, fy, dlat, dlon] of [[0.2, 0.3, 0.0015, -0.002], [0.8, 0.7, -0.0015, 0.002]]) {
    await p.mouse.click(box.x + box.width*fx, box.y + box.height*fy); await p.waitForTimeout(400);
    await p.fill('#modal-body input', `${(loc.lat+dlat).toFixed(6)}, ${(loc.lon+dlon).toFixed(6)}`);
    await p.click('#modal-body .btn.prim'); await p.waitForTimeout(400);
  }
  await p.screenshot({path: `${OUT}/m4_calib.png`});
  await p.click('#modal-body .btn.prim'); await p.waitForTimeout(2000);   // finish
  await p.screenshot({path: `${OUT}/m5_onimage.png`});
  // tap the named point
  const pt = await p.evaluate(() => { const q = MV.sv.points.find(x => x.kind === 'snap'); const [x, y] = toScr(mercX(q.lon), mercY(q.lat)); return [x, y]; });
  await p.mouse.click(box.x + pt[0], box.y + pt[1]); await p.waitForTimeout(2000);
  await p.screenshot({path: `${OUT}/m6_point.png`});
  // a track point
  await p.click('#sheet-close');
  const tp = await p.evaluate(() => { const q = MV.sv.points.filter(x => x.kind === 'track')[3]; MV.sel = q; openPanel('point'); return q.id; });
  await p.waitForTimeout(1500);
  await p.screenshot({path: `${OUT}/m7_trackpt.png`});
  await p.click('#sheet-close');
  await p.click('#m-layers'); await p.waitForTimeout(400);
  await p.screenshot({path: `${OUT}/m8_settings.png`});
  await p.click('#sheet-close');
  await p.evaluate(() => setLang('en')); await p.click('#m-survey'); await p.waitForTimeout(500);
  await p.screenshot({path: `${OUT}/m9_surveys_en.png`});
  // landscape
  await p.setViewportSize({width: 852, height: 393}); await p.click('#sheet-close'); await p.waitForTimeout(1200);
  await p.screenshot({path: `${OUT}/m10_land.png`});
  const sv = await p.evaluate(() => ({n: MV.sv.points.length, base: MV.sv.base && MV.sv.base.ctrl.length, jpg: MV.sv.points.filter(x=>x.jpg).length}));
  const ok = !errs.length && sv.n >= 4 && sv.base === 2 && sv.jpg >= 1;
  console.log(ok ? 'MAP PASS' : 'MAP FAIL', JSON.stringify(sv), JSON.stringify(errs));
  process.exitCode = ok ? 0 : 1;
  await b.close();
})();
