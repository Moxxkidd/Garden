const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:8899/docs.html');
  await page.click('text=开始体验');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'docs-screenshot.png' });
  await browser.close();
})();
