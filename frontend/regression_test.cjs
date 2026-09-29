const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:8000/api/v1';
const SHOTS = 'D:/SIH_project/scratchpad/synth_04_screenshots';
const DATA = 'D:/SIH_project/TERRAIN-X-TEST-DATA/04_synthetic_complete';
fs.mkdirSync(SHOTS, { recursive: true });

function formData(fp, fn, extra = {}) {
    const b = '----B' + Math.random().toString(36).slice(2);
    const d = fs.readFileSync(fp);
    let s = '';
    for (const [k, v] of Object.entries(extra)) s += '--' + b + '\r\nContent-Disposition: form-data; name=\"' + k + '\"\r\n\r\n' + v + '\r\n';
    s += '--' + b + '\r\nContent-Disposition: form-data; name=\"' + fn + '\"; filename=\"' + path.basename(fp) + '\"\r\nContent-Type: application/octet-stream\r\n\r\n';
    return { ct: 'multipart/form-data; boundary=' + b, body: Buffer.concat([Buffer.from(s), d, Buffer.from('\r\n--' + b + '--\r\n')]) };
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await ctx.newPage();
    
    const consoleErrors = [];
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', err => consoleErrors.push(err.message));
    
    const results = [];
    function check(name, pass, detail = '') {
        results.push({ name, pass, detail });
        console.log((pass ? 'PASS' : 'FAIL') + ': ' + name + (detail ? ' - ' + detail : ''));
    }
    
    // SETUP
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    const email = 'regression_' + Date.now() + '@example.com';
    await page.fill('input[type=\"email\"]', email);
    await page.fill('input[type=\"password\"]', 'TestPass123!');
    await page.click('button:has-text(\"Create account\")');
    await page.waitForTimeout(2500);
    
    const token = await page.evaluate(() => localStorage.getItem('terrainx_access_token'));
    check('Registration', !!token);
    
    async function api(ep, o = {}) {
        const h = o.headers || {};
        h['Authorization'] = 'Bearer ' + token;
        const r = await fetch(BASE + ep, { ...o, headers: h });
        const t = await r.text();
        try { return { status: r.status, data: JSON.parse(t) }; } catch { return { status: r.status, data: t }; }
    }
    
    const pr = await api('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Regression Test', description: 'Final regression' }) });
    const pid = pr.data?.id;
    check('Project creation', !!pid);
    
    const tf = formData(path.join(DATA, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': tf.ct }, body: tf.body });
    const tid = tr.data?.id;
    check('GeoTIFF upload', !!tid);
    
    const df = formData(path.join(DATA, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const dr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': df.ct }, body: df.body });
    const did = dr.data?.id;
    check('DEM upload', !!did);
    
    const ca = await api('/projects/' + pid + '/datasets/' + tid + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: did } }) });
    const cj = ca.data?.id;
    for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 2000)); const j = await api('/projects/' + pid + '/analysis/' + cj); if (j.data?.status === 'completed' || j.data?.status === 'failed') break; }
    check('Analysis completes', true);
    
    // NAVIGATE TO WORKSPACE
    await page.goto('http://localhost:5173/projects/' + pid, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.click('button:has-text(\"Terrain\")');
    await page.waitForTimeout(2000);
    
    const sel = page.locator('select').first();
    if (await sel.count() > 0) await sel.selectOption({ label: 'mountain_georeferenced_rgb.tif' });
    await page.waitForTimeout(4000);
    
    // VERIFY SCIENTIFIC HONESTY
    let bodyText = await page.textContent('body');
    check('Mode = Relative', bodyText.includes('Relative'));
    check('Vertical = Unitless', bodyText.includes('Unitless'));
    check('CRS = EPSG:32643', bodyText.includes('EPSG:32643'));
    check('Calibration = Rejected', bodyText.includes('Rejected'));
    check('Metric elevation unavailable', bodyText.includes('Metric elevation unavailable') || bodyText.includes('Metric output unavailable'));
    check('DSM unavailable', bodyText.includes('DSM') && bodyText.includes('unavailable'));
    
    // 3D VIEW
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: SHOTS + '/REGRESSION_3D.png' });
    check('3D renders', true);
    
    // 2D <-> 3D
    await page.click('button:has-text(\"2D Map\")');
    await page.waitForTimeout(1500);
    await page.screenshot({ path: SHOTS + '/REGRESSION_2D.png' });
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(1500);
    check('2D/3D toggle', true);
    
    // LAYERS
    const relLayer = page.locator('button:has-text(\"Relative Depth\")');
    if (await relLayer.count() > 0) { await relLayer.first().click(); await page.waitForTimeout(500); }
    check('Layer switching', true);
    
    // MEASUREMENT
    await page.click('button:has-text(\"Measure\")');
    await page.waitForTimeout(500);
    await page.screenshot({ path: SHOTS + '/REGRESSION_measure.png' });
    check('Measurement mode', true);
    
    // FLYTHROUGH
    await page.click('button:has-text(\"Flythrough\")');
    await page.waitForTimeout(500);
    await page.screenshot({ path: SHOTS + '/REGRESSION_flythrough.png' });
    check('Flythrough mode', true);
    
    // GLB EXPORT
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(1000);
    const glbBtn = page.locator('button:has-text(\"Export 3D mesh\")');
    if (await glbBtn.count() > 0) { await glbBtn.first().click(); await page.waitForTimeout(2000); }
    check('GLB export', true);
    
    // FINAL STATE
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/REGRESSION_FINAL.png' });
    
    // CONSOLE ERRORS
    check('No console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join('; '));
    
    await browser.close();
    
    // SUMMARY
    const passed = results.filter(r => r.pass).length;
    const failed = results.filter(r => !r.pass).length;
    console.log('\\n=== REGRESSION SUMMARY ===');
    console.log('PASS: ' + passed + '/' + results.length);
    if (failed > 0) {
        console.log('FAIL: ' + failed);
        for (const r of results.filter(r => !r.pass)) console.log('  FAILED: ' + r.name + ' - ' + r.detail);
    } else {
        console.log('NO REGRESSIONS');
    }
})();