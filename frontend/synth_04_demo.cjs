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
    
    // REGISTER
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    const email = 'demo_' + Date.now() + '@example.com';
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
    
    // CREATE PROJECT
    const pr = await api('/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Demo', description: 'Fast demo' }) });
    const pid = pr.data?.id;
    console.log('Project:', pid);
    
    // UPLOAD + ANALYZE
    const jf = formData(path.join(DATA, '01_single_image/mountain_aerial.jpg'), 'file');
    const jr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': jf.ct }, body: jf.body });
    const jid = jr.data?.id;
    
    const tf = formData(path.join(DATA, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': tf.ct }, body: tf.body });
    const tid = tr.data?.id;
    
    const df = formData(path.join(DATA, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const dr = await api('/projects/' + pid + '/datasets', { method: 'POST', headers: { 'Content-Type': df.ct }, body: df.body });
    const did = dr.data?.id;
    
    // Depth on JPEG
    const da = await api('/projects/' + pid + '/datasets/' + jid + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parameters: { version: 'v1' } }) });
    const dj = da.data?.id;
    for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 2000)); const j = await api('/projects/' + pid + '/analysis/' + dj); if (j.data?.status === 'completed' || j.data?.status === 'failed') break; }
    console.log('Depth done');
    
    // Calibration on GeoTIFF
    const ca = await api('/projects/' + pid + '/datasets/' + tid + '/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: did } }) });
    const cj = ca.data?.id;
    for (let i = 0; i < 40; i++) { await new Promise(r => setTimeout(r, 2000)); const j = await api('/projects/' + pid + '/analysis/' + cj); if (j.data?.status === 'completed' || j.data?.status === 'failed') break; }
    console.log('Calibration done');
    
    // NAVIGATE TO TERRAIN WORKSPACE
    await page.goto('http://localhost:5173/projects/' + pid, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1500);
    await page.click('button:has-text(\"Terrain\")');
    await page.waitForTimeout(2000);
    
    // Select GeoTIFF dataset
    const sel = page.locator('select').first();
    if (await sel.count() > 0) await sel.selectOption({ label: 'mountain_georeferenced_rgb.tif' });
    await page.waitForTimeout(4000);
    
    // Switch to 3D view
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(3000);
    
    // Capture 3D view
    await page.screenshot({ path: SHOTS + '/DEMO_3D.png' });
    console.log('3D captured');
    
    // Try to adjust camera - look for controls
    const bodyText = await page.textContent('body');
    
    // Look for exaggeration slider
    const sliders = await page.locator('input[type=\"range\"]').all();
    console.log('Sliders found:', sliders.length);
    for (const s of sliders) {
        const label = await s.getAttribute('aria-label') || await s.getAttribute('title') || '';
        const val = await s.inputValue();
        console.log('  Slider:', label, 'value:', val);
    }
    
    // Try to set exaggeration to a good value
    if (sliders.length > 0) {
        for (const s of sliders) {
            const label = (await s.getAttribute('aria-label') || '').toLowerCase();
            if (label.includes('exaggeration') || label.includes('vertical') || label.includes('height')) {
                await s.fill('2');
                console.log('Set exaggeration to 2');
                break;
            }
        }
    }
    
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/DEMO_3D_exaggerated.png' });
    console.log('3D exaggerated captured');
    
    // Try to rotate camera for better angle
    const canvas = page.locator('canvas').first();
    if (await canvas.count() > 0) {
        const box = await canvas.boundingBox();
        if (box) {
            // Click and drag to rotate
            await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
            await page.mouse.down();
            await page.mouse.move(box.x + box.width / 2 + 200, box.y + box.height / 2 - 100, { steps: 20 });
            await page.mouse.up();
            console.log('Camera rotated');
        }
    }
    
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOTS + '/DEMO_3D_rotated.png' });
    console.log('3D rotated captured');
    
    // Try to zoom in
    if (sliders.length > 0) {
        for (const s of sliders) {
            const label = (await s.getAttribute('aria-label') || '').toLowerCase();
            if (label.includes('zoom') || label.includes('distance')) {
                await s.fill('500');
                console.log('Zoom adjusted');
                break;
            }
        }
    }
    
    await page.waitForTimeout(1000);
    await page.screenshot({ path: SHOTS + '/DEMO_3D_final.png' });
    console.log('Final 3D captured');
    
    // Demonstrate layers
    const layerBtn = page.locator('button:has-text(\"Relative Depth\")');
    if (await layerBtn.count() > 0) {
        await layerBtn.first().click();
        await page.waitForTimeout(1000);
        await page.screenshot({ path: SHOTS + '/DEMO_layer_relative.png' });
        console.log('Relative depth layer captured');
    }
    
    // Demonstrate measurements
    const measureBtn = page.locator('button:has-text(\"Measure\")');
    if (await measureBtn.count() > 0) {
        await measureBtn.first().click();
        await page.waitForTimeout(1000);
        await page.screenshot({ path: SHOTS + '/DEMO_measure.png' });
        console.log('Measure mode captured');
    }
    
    // Demonstrate flythrough
    const flyBtn = page.locator('button:has-text(\"Flythrough\")');
    if (await flyBtn.count() > 0) {
        await flyBtn.first().click();
        await page.waitForTimeout(1000);
        await page.screenshot({ path: SHOTS + '/DEMO_flythrough.png' });
        console.log('Flythrough captured');
    }
    
    // Go back to 3D view for final shot
    await page.click('button:has-text(\"3D Terrain\")');
    await page.waitForTimeout(2000);
    
    // Final screenshot
    await page.screenshot({ path: SHOTS + '/DEMO_FINAL.png' });
    console.log('FINAL screenshot captured');
    
    // Print workspace state
    const finalText = await page.textContent('body');
    console.log('Final state:', finalText.substring(0, 1500));
    
    await browser.close();
    console.log('=== DEMO COMPLETE ===');
})();