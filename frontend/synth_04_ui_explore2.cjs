const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = 'http://localhost:8000/api/v1';
const SCREENSHOTS_DIR = 'D:/SIH_project/scratchpad/synth_04_screenshots';
const DATASET_DIR = 'D:/SIH_project/TERRAIN-X-TEST-DATA/04_synthetic_complete';

fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

async function apiRequest(endpoint, options = {}) {
    const url = BASE + endpoint;
    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }
    return { status: response.status, data };
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();
    
    console.log('=== STEP 1: Register ===');
    await page.goto('http://localhost:5173/register', { waitUntil: 'networkidle' });
    
    const email = 'ui_test_' + Date.now() + '@example.com';
    const password = 'TestPass123!';
    
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    await page.click('button:has-text("Create account")');
    
    await page.waitForTimeout(3000);
    await page.screenshot({ path: SCREENSHOTS_DIR + '/02_after_register.png', fullPage: true });
    console.log('Registration submitted, URL:', page.url());
    
    // Check if we're logged in
    const bodyText = await page.textContent('body');
    if (bodyText.includes('Dashboard') || bodyText.includes('Projects') || bodyText.includes('Create project')) {
        console.log('Successfully logged in after registration');
    } else {
        console.log('Login may have failed, body preview:', bodyText.substring(0, 300));
    }
    
    console.log('=== STEP 2: Create Project ===');
    // Look for create project button
    const createBtn = page.locator('button:has-text("Create project"), button:has-text("New project"), a:has-text("Create project")');
    if (await createBtn.count() > 0) {
        await createBtn.first().click();
        await page.waitForTimeout(1000);
        
        // Fill project name
        const nameInput = page.locator('input[type="text"], input[placeholder*="name"], input[placeholder*="Name"]');
        if (await nameInput.count() > 0) {
            await nameInput.first().fill('UI Validation Project');
        }
        
        // Click create/submit
        const submitBtn = page.locator('button:has-text("Create"), button:has-text("Submit"), button:has-text("Save")');
        if (await submitBtn.count() > 0) {
            await submitBtn.first().click();
        }
        
        await page.waitForTimeout(2000);
        await page.screenshot({ path: SCREENSHOTS_DIR + '/03_after_project_create.png', fullPage: true });
        console.log('Project created');
    } else {
        console.log('No create project button found');
        console.log('Body text:', bodyText.substring(0, 500));
    }
    
    // Get current state
    const finalBodyText = await page.textContent('body');
    console.log('Final body preview:', finalBodyText.substring(0, 500));
    
    await browser.close();
    console.log('=== EXPLORATION COMPLETE ===');
})();