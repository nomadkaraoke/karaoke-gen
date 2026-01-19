import { chromium } from 'playwright';

const BASE_URL = 'https://gen.nomadkaraoke.com';
const ADMIN_TOKEN = '9021de39710208e5dc31b6b28baf8e94172843fff177a9d5435135dd5388b6f8';
const JOB_ID = '26391a79';
const CACHE_BUSTER = Date.now();

async function test() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const consoleErrors = [];
  const failedRequests = [];

  // Capture console errors
  page.on('console', msg => {
    const text = msg.text();
    const type = msg.type();
    if (type === 'error' || text.toLowerCase().includes('error') || text.toLowerCase().includes('fail')) {
      consoleErrors.push({ type, text });
    }
  });

  // Capture failed HTTP requests (excluding expected ones)
  page.on('response', response => {
    const status = response.status();
    const url = response.url();
    // Ignore YouTube, Cloudflare RUM, and static 404 redirects
    if (status >= 400 &&
        !url.includes('youtube.com') &&
        !url.includes('cdn-cgi/rum') &&
        !url.includes('__next._tree')) {
      failedRequests.push({
        url,
        status,
        statusText: response.statusText()
      });
    }
  });

  console.log('\n=== Final Production Test ===\n');

  try {
    // Step 1: Set auth token
    console.log('Step 1: Navigate to /app and set auth token');
    await page.goto(BASE_URL + '/app?_cb=' + CACHE_BUSTER, { waitUntil: 'networkidle' });
    await page.evaluate((token) => {
      localStorage.setItem('karaoke_access_token', token);
    }, ADMIN_TOKEN);
    await page.reload({ waitUntil: 'networkidle' });

    // Step 2: Navigate to review page
    console.log('Step 2: Navigate to /app/jobs/' + JOB_ID + '/review/');
    await page.goto(BASE_URL + '/app/jobs/' + JOB_ID + '/review/?_cb=' + CACHE_BUSTER, {
      waitUntil: 'networkidle',
      timeout: 30000
    });
    await page.waitForTimeout(5000);

    // Check URL (should be updated via history.replaceState)
    const currentUrl = page.url();
    console.log('\n--- URL Check ---');
    console.log('Final URL:', currentUrl);
    if (currentUrl.includes(JOB_ID + '/review')) {
      console.log('URL contains job ID and review path');
    } else if (currentUrl.endsWith('/app/jobs/')) {
      console.log('URL was NOT updated (still at /app/jobs/)');
    }

    // Check page content
    const bodyText = await page.evaluate(() => document.body.innerText);
    console.log('\n--- Page Content Check ---');
    if (bodyText.includes('Lyrics Transcription Review')) {
      console.log('Page header found: "Lyrics Transcription Review"');
    }
    if (bodyText.includes('Failed to load')) {
      console.log('ERROR: Page shows failure message');
    }

    // Check logo
    console.log('\n--- Logo Check ---');
    const logoLoaded = await page.evaluate(() => {
      const img = document.querySelector('img[alt="Nomad Karaoke"]');
      return img ? img.complete && img.naturalHeight > 0 : false;
    });
    console.log('Logo loaded:', logoLoaded);

    // Check for audio element
    console.log('\n--- Audio Check ---');
    const audioSrc = await page.evaluate(() => {
      const audio = document.querySelector('audio');
      return audio ? audio.src : null;
    });
    if (audioSrc) {
      console.log('Audio src:', audioSrc);
      if (audioSrc.includes('/api/review/')) {
        console.log('Audio URL uses correct /api/review/ path');
      }
    } else {
      console.log('No audio element found on page');
    }

    // Report failed requests
    console.log('\n--- Failed HTTP Requests (excluding expected) ---');
    const relevantFailures = failedRequests.filter(r =>
      !r.url.includes(JOB_ID + '/review') // Exclude initial 404 redirect
    );
    if (relevantFailures.length === 0) {
      console.log('No unexpected failed requests');
    } else {
      relevantFailures.forEach((req, i) => {
        console.log((i + 1) + '. HTTP ' + req.status + ' ' + req.statusText + ': ' + req.url.substring(0, 100));
      });
    }

    // Report console errors
    console.log('\n--- Console Errors ---');
    const relevantErrors = consoleErrors.filter(e =>
      !e.text.includes('404') // Exclude 404 console errors from initial redirect
    );
    if (relevantErrors.length === 0) {
      console.log('No relevant console errors');
    } else {
      relevantErrors.forEach((err, i) => {
        console.log((i + 1) + '. [' + err.type + '] ' + err.text.substring(0, 150));
      });
    }

    // Step 3: Test reload
    console.log('\n--- Reload Test ---');
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    const reloadUrl = page.url();
    const reloadBody = await page.evaluate(() => document.body.innerText);
    console.log('URL after reload:', reloadUrl);
    if (reloadBody.includes('Lyrics Transcription Review')) {
      console.log('Reload SUCCESS: Page still shows lyrics review');
    } else if (reloadBody.includes('Page not found')) {
      console.log('Reload FAILED: Shows "Page not found"');
    } else {
      console.log('Reload result unclear. Content preview:', reloadBody.substring(0, 100));
    }

  } catch (e) {
    console.log('Error:', e.message);
  }

  await browser.close();
}

test().catch(console.error);
