const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:/Users/33393/.chromium-browser-snapshots/chromium/win64-1676344/chrome-win/chrome.exe',
    args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-proxy-server','--host-resolver-rules=MAP localhost 127.0.0.1']
  });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e=>console.log('goto-warn', e.message));
  await page.waitForTimeout(3000);
  console.log('TAB_TITLE=' + await page.title());
  const bodyText = (await page.evaluate(() => document.body.innerText)).slice(0, 500).replace(/\n/g,' | ');
  console.log('BODY_TEXT=' + bodyText);
  await page.screenshot({ path: 'C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/5173-shot.png', fullPage: false });
  console.log('SHOT_OK');
  await browser.close();
})().catch(e => { console.error('ERR', e); process.exit(1); });
