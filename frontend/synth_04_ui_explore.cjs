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
    
    // Enable console logging
    page.on('console', msg => {
        if (msg.type() === 'error') console.log('  [CONSOLE ERROR]', msg.text());
    });
    
    console.log('=== STEP 1: Register/Login ===');
    await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
    
    // Register a new user
    const email = 'ui_test_' + Date.now() + '@example.com';
    const password = 'TestPass123!';
    
    // Fill registration form
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    
    // Look for register button or link
    const registerBtn = page.locator('button:has-text("Register"), a:has-text("Register"), button:has-text("Sign up")');
    if (await registerBtn.count() > 0) {
        await registerBtn.first().click();
        console.log('Clicked register button');
    } else {
        // Try signing in directly (user might not exist yet)
        await page.click('button:has-text("Sign in")');
        console.log('Clicked sign in button');
    }
    
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SCREENSHOTS_DIR + '/02_after_login.png', fullPage: true });
    
    // Check if we need to handle a registration form differently
    const currentUrl = page.url();
    console.log('Current URL after login attempt:', currentUrl);
    
    // Get page content to understand the state
    const bodyText = await page.textContent('body');
    console.log('Body text preview:', bodyText.substring(0, 500));
    
    await browser.close();
    console.log('=== EXPLORATION COMPLETE ===');
})();