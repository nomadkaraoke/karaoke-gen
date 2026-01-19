#!/usr/bin/env node
/**
 * Post-build script to add SPA redirect logic to 404.html
 *
 * GitHub Pages serves 404.html for all non-existent paths.
 * This script injects a redirect that preserves the URL for SPA routing.
 *
 * For /app/jobs/* URLs:
 * 1. Stores the original path in sessionStorage
 * 2. Redirects to /app/jobs/ (the pre-rendered catch-all page)
 * 3. client.tsx reads sessionStorage and restores the URL via history.replaceState
 */

const fs = require('fs');
const path = require('path');

const outDir = path.join(__dirname, '..', 'out');
const html404Path = path.join(outDir, '404.html');

// The redirect script to inject - runs before React loads
const redirectScript = `<script>
(function() {
  // SPA redirect for dynamic routes on GitHub Pages
  var path = window.location.pathname;
  // Handle /app/jobs/* routes (e.g., /app/jobs/abc123/review/)
  if (path.startsWith('/app/jobs/') && path !== '/app/jobs/') {
    // Store the full path (including search and hash) for restoration after redirect
    sessionStorage.setItem('spa-redirect-path', path + window.location.search + window.location.hash);
    // Redirect to the catch-all page
    window.location.replace('/app/jobs/');
  }
})();
</script>`;

// Read 404.html
let html;
try {
  html = fs.readFileSync(html404Path, 'utf-8');
} catch (err) {
  console.error('Error reading 404.html:', err.message);
  process.exit(1);
}

// Inject the redirect script right after <head>
const injectedHtml = html.replace('<head>', '<head>' + redirectScript);

// Write back
try {
  fs.writeFileSync(html404Path, injectedHtml);
  console.log('Injected SPA redirect script into 404.html');
} catch (err) {
  console.error('Error writing 404.html:', err.message);
  process.exit(1);
}
