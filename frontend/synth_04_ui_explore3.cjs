const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:8000/api/v1';
const SCREENSHOTS_DIR = 'D:/SIH_project/scratchpad/synth_04_screenshots';
const DATASET_DIR = 'D:/SIH_project/TERRAIN-X-TEST-DATA/04_synthetic_complete';

fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

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
    console.log('Registered, URL:', page.url());
    
    // Get token
    const token = await page.evaluate(() => localStorage.getItem('terrainx_access_token'));
    console.log('Token:', token ? 'found' : 'not found');
    
    if (!token) {
        console.log('No token, cannot proceed');
        await browser.close();
        return;
    }
    
    // Helper for API calls
    async function api(endpoint, options = {}) {
        const url = BASE + endpoint;
        const headers = options.headers || {};
        headers['Authorization'] = 'Bearer ' + token;
        const response = await fetch(url, { ...options, headers });
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
    
    if (!projectId) {
        console.log('Failed to create project');
        await browser.close();
        return;
    }
    
    // Navigate to project
    console.log('=== STEP 3: Navigate to Project ===');
    await page.goto('http://localhost:5173/projects/' + projectId, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SCREENSHOTS_DIR + '/03_project_page.png', fullPage: true });
    console.log('Project page loaded');
    
    // Look for upload button
    const uploadBtn = page.locator('button:has-text("Upload"), button:has-text("Add"), a:has-text("Upload")');
    console.log('Upload buttons found:', await uploadBtn.count());
    
    // Look for file input
    const fileInputs = await page.locator('input[type="file"]').all();
    console.log('File inputs found:', fileInputs.length);
    
    // Get page content
    const bodyText = await page.textContent('body');
    console.log('Body preview:', bodyText.substring(0, 800));
    
    await browser.close();
    console.log('=== EXPLORATION COMPLETE ===');
})();