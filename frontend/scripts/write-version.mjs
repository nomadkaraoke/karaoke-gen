#!/usr/bin/env node
/**
 * Emit public/version.json with build SHA + timestamp.
 *
 * Runs as `prebuild` before `next build`. The JSON file is served as a static
 * asset; the frontend fetches it at runtime to detect stale bundles.
 *
 * SHA sources (first match wins):
 *   1. NEXT_PUBLIC_COMMIT_SHA (set by CI — matches the value baked into bundles)
 *   2. CF_PAGES_COMMIT_SHA    (Cloudflare Pages fallback)
 *   3. GITHUB_SHA             (GitHub Actions fallback)
 *   4. git rev-parse HEAD     (local dev)
 *   5. "dev"                  (fallback)
 */
import { execSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

function gitSha() {
  try {
    return execSync('git rev-parse HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return null
  }
}

const sha =
  process.env.NEXT_PUBLIC_COMMIT_SHA ||
  process.env.CF_PAGES_COMMIT_SHA ||
  process.env.GITHUB_SHA ||
  gitSha() ||
  'dev'

const payload = {
  build_sha: sha,
  built_at: new Date().toISOString(),
}

const outPath = resolve(__dirname, '..', 'public', 'version.json')
mkdirSync(dirname(outPath), { recursive: true })
writeFileSync(outPath, JSON.stringify(payload, null, 2) + '\n', 'utf8')
console.log(`[write-version] wrote ${outPath}: ${JSON.stringify(payload)}`)
