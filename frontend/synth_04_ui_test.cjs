const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();
    
    const screenshotsDir = 'D:/SIH_project/scratchpad/synth_04_screenshots';
    fs.mkdirSync(screenshotsDir, { recursive: true });
    
    console.log('Opening TERRAIN-X...');
    await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
    await page.screenshot({ path: screenshotsDir + '/01_landing.png', fullPage: true });
    console.log('Landing page captured');
    console.log('Page title:', await page.title());
    
    const inputs = await page.locator('input').all();
    console.log('Input elements found:', inputs.length);
    for (const input of inputs) {
        const type = await input.getAttribute('type');
        const placeholder = await input.getAttribute('placeholder');
        console.log('  Input:', JSON.stringify({ type, placeholder }));
    }
    
    const buttons = await page.locator('button').all();
    console.log('Button elements found:', buttons.length);
    for (const btn of buttons) {
        const text = await btn.textContent();
        console.log('  Button:', text.trim());
    }
    
    await browser.close();
    console.log('Done');
})();