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
    
    // Register
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    const email = 'polish_' + Date.now() + '@example.com';
    await page.fill('input[type=\"email\"]', email);
    await page.fill('input[type=\"password\"]', 'TestPass123!');
    await page.click('button:has-text(\"Create account\")');
    await page.waitForTimeout(2500);
    
    const token = await page.evaluate(() => localStorage.getItem('terrainx_access_token'));
    if (!token) { console.log('NO TOKEN'); await browser.close(); return; }
    
    async function api(ep, o = {}) {
        const h = o.headers || {};
        h['Authorization'] = 'Bearer ' + token;
        const r = await fetch(BASE + ep, { ...o, headers: h });
        const t = await r.text();
        try { return { status: r.status, data: JSON.parse(t) }; } catch { return { status: r.status, data: t }; }
    }
    
    // Create project + upload + analyze
    const pr = await api('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Polish Demo', description: 'UI polish validation' }) });
    const pid = pr.data?.id;
    
    const jf = formData(path.join(DATA, '01_single_image/mountain_aerial.jpg'), 'file');
    const jr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': jf.ct }, body: jf.body });
    const jid = jr.data?.id;
    
    const tf = formData(path.join(DATA, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': tf.ct }, body: tf.body });
    const tid = tr.data?.id;
    
    const df = formData(path.join(DATA, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const dr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': df.ct }, body: df.body });
    const did = dr.data?.id;
    
    // Depth + calibration
    const da = await api('/projects/' + pid + '/datasets/' + jid + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parameters: { version: 'v1' } }) });
    const dj = da.data?.id;
    for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 2000)); const j = await api('/projects/' + pid + '/analysis/' + dj); if (j.data?.status === 'completed' || j.data?.status === 'failed') break; }
    
    const ca = await api('/projects/' + pid + '/datasets/' + tid + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: did } }) });
    const cj = ca.data?.id;
    for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 2000)); const j = await api('/projects/' + pid + '/analysis/' + cj); if (j.data?.status === 'completed' || j.data?.status === 'failed') break; }
    
    // Navigate to terrain workspace
    await page.goto('http://localhost:5173/projects/' + pid, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.click('button:has-text(\"Terrain\")');
    await page.waitForTimeout(2000);
    
    // Select GeoTIFF dataset
    const sel = page.locator('select').first();
    if (await sel.count() > 0) await sel.selectOption({ label: 'mountain_georeferenced_rgb.tif' });
    await page.waitForTimeout(4000);
    
    // 1. 3D hero view
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: SHOTS + '/POLISH_3D_hero.png' });
    console.log('1. 3D hero captured');
    
    // 2. 2D map
    await page.click('button:has-text(\"2D Map\")');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/POLISH_2D_map.png' });
    console.log('2. 2D map captured');
    
    // 3. Layers panel
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/POLISH_layers.png' });
    console.log('3. Layers captured');
    
    // 4. Measurement mode
    await page.click('button:has-text(\"Measure\")');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/POLISH_measure.png' });
    console.log('4. Measure captured');
    
    // 5. Flythrough
    await page.click('button:has-text(\"Flythrough\")');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/POLISH_flythrough.png' });
    console.log('5. Flythrough captured');
    
    // 6. Calibration rejection (open results drawer)
    await page.click('button:has-text(\"Show results\")');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/POLISH_calibration.png' });
    console.log('6. Calibration captured');
    
    // 7. Responsive - tablet
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/POLISH_tablet.png' });
    console.log('7. Tablet captured');
    
    // 8. Responsive - mobile
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/POLISH_mobile.png' });
    console.log('8. Mobile captured');
    
    // Final - back to 3D hero
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/POLISH_FINAL.png' });
    console.log('FINAL captured');
    
    await browser.close();
    console.log('=== POLISH SCREENSHOTS COMPLETE ===');
})();