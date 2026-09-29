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
    console.log('=== STEP 1: Register ===');
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    const email = 'ui_test_' + Date.now() + '@example.com';
    const password = 'TestPass123!';
    await page.fill('input[type=\"email\"]', email);
    await page.fill('input[type=\"password\"]', password);
    await page.click('button:has-text(\"Create account\")');
    await page.waitForTimeout(3000);
    console.log('Registered');
    
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
    console.log('=== STEP 2: Create Project ===');
    const projectRes = await api('/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'UI Validation Project', description: 'Automated UI validation' })
    });
    const projectId = projectRes.data?.id;
    console.log('Project ID:', projectId);
    if (!projectId) { await browser.close(); return; }
    
    // Upload datasets
    console.log('=== STEP 3: Upload Datasets ===');
    const jpegForm = createFormData(path.join(DATASET_DIR, '01_single_image/mountain_aerial.jpg'), 'file');
    const jpegRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': jpegForm.contentType }, body: jpegForm.body });
    const jpegId = jpegRes.data?.id;
    console.log('JPEG:', jpegRes.status, jpegId);
    
    const tifForm = createFormData(path.join(DATASET_DIR, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tifRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': tifForm.contentType }, body: tifForm.body });
    const tifId = tifRes.data?.id;
    console.log('GeoTIFF:', tifRes.status, tifId);
    
    const demForm = createFormData(path.join(DATASET_DIR, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const demRes = await api('/projects/' + projectId + '/datasets', { method: 'POST', headers: { 'Content-Type': demForm.contentType }, body: demForm.body });
    const demId = demRes.data?.id;
    console.log('DEM:', demRes.status, demId);
    
    // Run depth analysis on JPEG
    console.log('=== STEP 4: Depth Analysis ===');
    const depthRes = await api('/projects/' + projectId + '/datasets/' + jpegId + '/analysis', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1' } })
    });
    const depthJobId = depthRes.data?.id;
    for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const jobRes = await api('/projects/' + projectId + '/analysis/' + depthJobId);
        if (jobRes.data?.status === 'completed' || jobRes.data?.status === 'failed') {
            console.log('Depth job:', jobRes.data.status);
            break;
        }
    }
    
    // Run calibration analysis
    console.log('=== STEP 5: Calibration Analysis ===');
    const calibRes = await api('/projects/' + projectId + '/datasets/' + tifId + '/analysis', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: demId } })
    });
    const calibJobId = calibRes.data?.id;
    for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const jobRes = await api('/projects/' + projectId + '/analysis/' + calibJobId);
        if (jobRes.data?.status === 'completed' || jobRes.data?.status === 'failed') {
            console.log('Calibration job:', jobRes.data.status, 'calibration:', jobRes.data.calibration_status);
            break;
        }
    }
    
    // Navigate to project page and click Terrain tab
    console.log('=== STEP 6: Navigate to Terrain Workspace ===');
    await page.goto('http://localhost:5173/projects/' + projectId, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    
    // Click on Terrain tab
    const terrainTab = page.locator('button:has-text(\"Terrain\")');
    if (await terrainTab.count() > 0) {
        await terrainTab.first().click();
        console.log('Clicked Terrain tab');
        await page.waitForTimeout(5000);
    } else {
        console.log('Terrain tab not found');
    }
    
    await page.screenshot({ path: SCREENSHOTS_DIR + '/07_terrain_workspace.png', fullPage: true });
    
    let bodyText = await page.textContent('body');
    console.log('Workspace body:', bodyText.substring(0, 2000));
    
    // Look for workspace elements
    const buttons = await page.locator('button').all();
    console.log('Buttons in workspace:', buttons.length);
    for (const btn of buttons) {
        const text = await btn.textContent();
        if (text.trim()) console.log('  Button:', text.trim().substring(0, 60));
    }
    
    await browser.close();
    console.log('=== SCRIPT COMPLETE ===');
})();