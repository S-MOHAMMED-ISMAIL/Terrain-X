const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:8000/api/v1';
const SCREENSHOTS_DIR = 'D:/SIH_project/scratchpad/synth_04_screenshots';
const DATASET_DIR = 'D:/SIH_project/TERRAIN-X-TEST-DATA/04_synthetic_complete';

fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

// Helper to create FormData with a file
function createFormData(filePath, fieldName, extraFields = {}) {
    const boundary = '----FormBoundary' + Math.random().toString(36).substring(2);
    const fileName = path.basename(filePath);
    const fileData = fs.readFileSync(filePath);
    
    let body = '';
    // Add extra fields
    for (const [key, value] of Object.entries(extraFields)) {
        body += '--' + boundary + '\r\n';
        body += 'Content-Disposition: form-data; name=\"' + key + '\"\r\n\r\n';
        body += value + '\r\n';
    }
    // Add file
    body += '--' + boundary + '\r\n';
    body += 'Content-Disposition: form-data; name=\"' + fieldName + '\"; filename=\"' + fileName + '\"\r\n';
    body += 'Content-Type: application/octet-stream\r\n\r\n';
    
    const bodyBuffer = Buffer.from(body, 'utf-8');
    const endBuffer = Buffer.from('\r\n--' + boundary + '--\r\n', 'utf-8');
    const combined = Buffer.concat([bodyBuffer, fileData, endBuffer]);
    
    return { contentType: 'multipart/form-data; boundary=' + boundary, body: combined };
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
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    await page.click('button:has-text("Create account")');
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
    
    // Upload datasets via API
    console.log('=== STEP 3: Upload Datasets via API ===');
    
    // Upload JPEG
    const jpegForm = createFormData(path.join(DATASET_DIR, '01_single_image/mountain_aerial.jpg'), 'file');
    const jpegRes = await api('/projects/' + projectId + '/datasets', {
        method: 'POST',
        headers: { 'Content-Type': jpegForm.contentType },
        body: jpegForm.body
    });
    console.log('JPEG upload:', jpegRes.status, jpegRes.data?.id || '');
    const jpegId = jpegRes.data?.id;
    
    // Upload GeoTIFF RGB
    const tifForm = createFormData(path.join(DATASET_DIR, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'file');
    const tifRes = await api('/projects/' + projectId + '/datasets', {
        method: 'POST',
        headers: { 'Content-Type': tifForm.contentType },
        body: tifForm.body
    });
    console.log('GeoTIFF upload:', tifRes.status, tifRes.data?.id || '');
    const tifId = tifRes.data?.id;
    
    // Upload DEM
    const demForm = createFormData(path.join(DATASET_DIR, '03_reference_dem/terrainx_reference_dem.tif'), 'file', { role: 'dem_reference' });
    const demRes = await api('/projects/' + projectId + '/datasets', {
        method: 'POST',
        headers: { 'Content-Type': demForm.contentType },
        body: demForm.body
    });
    console.log('DEM upload:', demRes.status, demRes.data?.id || '');
    const demId = demRes.data?.id;
    
    // Upload GCP (original - will be rejected)
    const gcpForm = createFormData(path.join(DATASET_DIR, '04_gcp/gcp.csv'), 'file', { role: 'gcp_reference', gcp_crs: 'EPSG:32643' });
    const gcpRes = await api('/projects/' + projectId + '/datasets', {
        method: 'POST',
        headers: { 'Content-Type': gcpForm.contentType },
        body: gcpForm.body
    });
    console.log('GCP upload:', gcpRes.status, gcpRes.data?.status || '', gcpRes.data?.validation_error || '');
    
    // Run depth analysis on JPEG
    console.log('=== STEP 4: Run Depth Analysis on JPEG ===');
    const depthRes = await api('/projects/' + projectId + '/datasets/' + jpegId + '/analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1' } })
    });
    console.log('Depth analysis:', depthRes.status, depthRes.data?.id || '');
    const depthJobId = depthRes.data?.id;
    
    // Poll for completion
    if (depthJobId) {
        for (let i = 0; i < 60; i++) {
            await new Promise(r => setTimeout(r, 3000));
            const jobRes = await api('/projects/' + projectId + '/analysis/' + depthJobId);
            const status = jobRes.data?.status;
            console.log('  Poll', i+1, ':', status);
            if (status === 'completed' || status === 'failed') {
                console.log('Depth job final status:', status);
                break;
            }
        }
    }
    
    // Run calibration analysis on GeoTIFF + DEM
    console.log('=== STEP 5: Run Calibration Analysis ===');
    const calibRes = await api('/projects/' + projectId + '/datasets/' + tifId + '/analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: { version: 'v1', dem_reference_dataset_id: demId } })
    });
    console.log('Calibration analysis:', calibRes.status, calibRes.data?.id || '');
    const calibJobId = calibRes.data?.id;
    
    if (calibJobId) {
        for (let i = 0; i < 60; i++) {
            await new Promise(r => setTimeout(r, 3000));
            const jobRes = await api('/projects/' + projectId + '/analysis/' + calibJobId);
            const status = jobRes.data?.status;
            console.log('  Poll', i+1, ':', status);
            if (status === 'completed' || status === 'failed') {
                console.log('Calibration job final status:', status);
                console.log('Calibration status:', jobRes.data?.calibration_status);
                break;
            }
        }
    }
    
    // Navigate to workspace
    console.log('=== STEP 6: Navigate to Workspace ===');
    await page.goto('http://localhost:5173/projects/' + projectId + '/terrain', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    await page.screenshot({ path: SCREENSHOTS_DIR + '/06_terrain_workspace.png', fullPage: true });
    console.log('Workspace loaded');
    
    let bodyText = await page.textContent('body');
    console.log('Workspace body:', bodyText.substring(0, 1000));
    
    await browser.close();
    console.log('=== SCRIPT COMPLETE ===');
})();