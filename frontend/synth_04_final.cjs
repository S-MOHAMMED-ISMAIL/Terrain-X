const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:8000/api/v1';
const SCREENSHOTS_DIR = 'D:/SIH_project/scratchpad/synth_04_screenshots';
const DATASET_DIR = 'D:/SIH_project/TERRAIN-X-TEST-DATA/04_synthetic_complete';

fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

function createFormData(filePath, fieldName, extraFields = {}) {
    const boundary = '----FormBoundary' + Math.random().toString(36).substring(2);
    const fileName = path.basename(filePath);
    const fileData = fs.readFileSync(filePath);
    let body = '';
    for (const [key, value] of Object.entries(extraFields)) {
        body += '--' + boundary + '\r\nContent-Disposition: form-data; name=\"' + key + '\"\r\n\r\n' + value + '\r\n';
    }
    body += '--' + boundary + '\r\nContent-Disposition: form-data; name=\"' + fieldName + '\"; filename=\"' + fileName + '\"\r\nContent-Type: application/octet-stream\r\n\r\n';
    const bodyBuffer = Buffer.from(body, 'utf-8');
    const endBuffer = Buffer.from('\r\n--' + boundary + '--\r\n', 'utf-8');
    return { contentType: 'multipart/form-data; boundary=' + boundary, body: Buffer.concat([bodyBuffer, fileData, endBuffer]) };
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();
    
    // Register
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    const email = 'ui_final_' + Date.now() + '@example.com';
    const password = 'TestPass123!';
    await page.fill('input[type=\"email\"]', email);
    await page.fill('input[type=\"password\"]', password);
    await page.click('button:has-text(\"Create account\")');
    await page.waitForTimeout(3000);
    
    const token = await page.evaluate(() => localStorage.getItem('terrainx_access_token'));
    if (!token) { console.log('No token'); await browser.close(); return; }
    
    async function api(endpoint, options = {}) {
        const headers = options.headers || {};
        headers['Authorization'] = 'Bearer ' + token;
        const response = await fetch(BASE + endpoint, { ...options, headers });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); } catch { data = text; }
        return { status: response.status, data };
    }
    
    // Create project
    const projectRes = await api('/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Final Validation', description: 'Final UI validation result' })
    });
    const projectId = projectRes.data?.id;
    
    // Upload datasets
    const jpegForm = createFormData(path.join(DATASET_DIR, '01_single_image/mountain_aerial.jpg'), 'file');
    const jpegRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': jpegForm.contentType }, body: jpegForm.body });
    const jpegId = jpegRes.data?.id;
    
    const tifForm = createFormData(path.join(DATASET_DIR, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tifRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': tifForm.contentType }, body: tifForm.body });
    const tifId = tifRes.data?.id;
    
    const demForm = createFormData(path.join(DATASET_DIR, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const demRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': demForm.contentType }, body: demForm.body });
    const demId = demRes.data?.id;
    
    // Run analyses
    const depthRes = await api('/projects/' + projectId + '/datasets/' + jpegId + '/analysis', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1' } })
    });
    const depthJobId = depthRes.data?.id;
    for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const jobRes = await api('/projects/' + projectId + '/analysis/' + depthJobId);
        if (jobRes.data?.status === 'completed' || jobRes.data?.status === 'failed') break;
    }
    
    const calibRes = await api('/projects/' + projectId + '/datasets/' + tifId + '/analysis', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: demId } })
    });
    const calibJobId = calibRes.data?.id;
    for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const jobRes = await api('/projects/' + projectId + '/analysis/' + calibJobId);
        if (jobRes.data?.status === 'completed' || jobRes.data?.status === 'failed') break;
    }
    
    // Navigate to terrain workspace with GeoTIFF
    await page.goto('http://localhost:5173/projects/' + projectId, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await page.click('button:has-text(\"Terrain\")');
    await page.waitForTimeout(3000);
    
    // Select GeoTIFF dataset
    const datasetSelect = page.locator('select').first();
    if (await datasetSelect.count() > 0) {
        await datasetSelect.selectOption({ label: 'mountain_georeferenced_rgb.tif' });
    }
    await page.waitForTimeout(5000);
    
    // Capture final state
    await page.screenshot({ path: SCREENSHOTS_DIR + '/FINAL_result.png', fullPage: true });
    console.log('Final screenshot captured:', SCREENSHOTS_DIR + '/FINAL_RESULT.png');
    
    // Also capture with 3D view
    const terrain3d = page.locator('button:has-text(\"3D Terrain\")');
    if (await terrain3d.count() > 0) {
        await terrain3d.first().click();
        await page.waitForTimeout(3000);
        await page.screenshot({ path: SCREENSHOTS_DIR + '/FINAL_3D.png', fullPage: true });
        console.log('Final 3D screenshot captured');
    }
    
    // Keep browser open for 30 seconds so user can see it
    console.log('Keeping browser open for 30 seconds...');
    await page.waitForTimeout(30000);
    
    await browser.close();
    console.log('=== FINAL SCREENSHOT COMPLETE ===');
})();