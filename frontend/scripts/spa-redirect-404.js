#!/usr/bin/env node
/**
 * Post-build script for 404.html
 *
 * Previously, this injected SPA redirect logic for path-based routing.
 * Now that we use hash-based routing (e.g., /app/jobs/#/{jobId}/review),
 * the redirect is no longer needed - the hash is handled client-side and
 * never sent to the server.
 *
 * This script is kept as a no-op for backwards compatibility with the build process.
 */

console.log('Hash-based routing enabled - no SPA redirect injection needed');
