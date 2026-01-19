import { chromium } from 'playwright';

const BASE_URL = 'https://gen.nomadkaraoke.com';
const ADMIN_TOKEN = '9021de39710208e5dc31b6b28baf8e94172843fff177a9d5435135dd5388b6f8';
const JOB_ID = '26391a79';
const CACHE_BUSTER = Date.now();

async function test() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Log ALL console messages
  page.on('console', msg => {
    console.log('CONSOLE [' + msg.type() + ']:', msg.text());
  });

  console.log('\n=== URL Debug Test ===\n');

  try {
    // Set auth token
    await page.goto(BASE_URL + '/app', { waitUntil: 'networkidle' });
    await page.evaluate((token) => {
      localStorage.setItem('karaoke_access_token', token);
    }, ADMIN_TOKEN);

    // Navigate to review page and watch URL changes
    console.log('Navigating to review page...');

    // Watch URL changes
    let urlLog = [];
    page.on('framenavigated', frame => {
      if (frame === page.mainFrame()) {
        urlLog.push({ time: Date.now(), url: frame.url() });
      }
    });

    await page.goto(BASE_URL + '/app/jobs/' + JOB_ID + '/review/?_cb=' + CACHE_BUSTER, {
      waitUntil: 'domcontentloaded'
    });

    // Check sessionStorage before it gets consumed
    const storedPath = await page.evaluate(() => {
      return sessionStorage.getItem('spa-redirect-path');
    });
    console.log('SessionStorage spa-redirect-path:', storedPath);

    await page.waitForTimeout(3000);

    console.log('\nURL history:');
    urlLog.forEach((entry, i) => {
      console.log(i + 1 + '. ' + entry.url);
    });

    console.log('\nFinal URL:', page.url());

    // Check if history.replaceState is being called
    const historyState = await page.evaluate(() => {
      return {
        url: window.location.href,
        pathname: window.location.pathname,
        historyLength: window.history.length
      };
    });
    console.log('History state:', historyState);

    // Check body content
    const content = await page.evaluate(() => document.body.innerText.substring(0, 200));
    console.log('\nPage content preview:', content);

  } catch (e) {
    console.log('Error:', e.message);
  }

  await browser.close();
}

test().catch(console.error);
