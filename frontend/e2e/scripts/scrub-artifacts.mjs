#!/usr/bin/env node
// frontend/e2e/scripts/scrub-artifacts.mjs
/**
 * Scrub secrets from Playwright output before it is uploaded as a GitHub
 * Actions artifact. karaoke-gen is a PUBLIC repo, so any signed-in GitHub user
 * can download run artifacts — anything left in test-results/ is published.
 *
 * What it does, recursively under each given path:
 *   - deletes Playwright traces (any *.zip — trace.zip, or <sha>.zip inside an
 *     HTML report): they hold every request header
 *     (X-Admin-Token) and every typed value, and can't be scrubbed in place;
 *   - deletes e2e-session-token.txt (a live bearer token for the test user);
 *   - with --drop-videos, deletes *.webm (videos of the Stripe page show the card);
 *   - in text files, replaces the literal values of known secret env vars and
 *     card-number-shaped digit runs; with --blank-inputs also blanks <input
 *     value="…"> attributes and ARIA-snapshot textbox values (error-context.md);
 *   - finally deletes any text file that still contains a secret value.
 *
 * Usage (CI): node e2e/scripts/scrub-artifacts.mjs [--drop-videos] [--blank-inputs] <path>...
 * Secret values are read from the env vars in SECRET_ENV_VARS (unset ones are ignored).
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SECRET_ENV_VARS = [
  'E2E_ADMIN_TOKEN',
  'KARAOKE_ADMIN_TOKEN',
  'KARAOKE_ACCESS_TOKEN',
  'TESTMAIL_API_KEY',
  'GEMINI_API_KEY',
  'E2E_STRIPE_CARD_NUMBER',
  'E2E_STRIPE_CARD_EXPIRY',
  'E2E_STRIPE_CARD_CVC',
  'E2E_SESSION_TOKEN',
  'E2E_TEST_TOKEN',
  'E2E_BYPASS_KEY',
];

// Everything else is treated as text and checked (unknown/no extension included).
const BINARY_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.webm', '.mp4', '.woff', '.woff2', '.ttf']);
const ALWAYS_DELETE = new Set(['e2e-session-token.txt']);
// Short values (CVC "123", "09/29") would scrub unrelated text everywhere;
// --blank-inputs covers them where they actually appear (form fields).
const MIN_LITERAL_LENGTH = 5;

/** Literal variants of a secret worth searching for (card numbers appear grouped in 4s in the UI). */
export function secretVariants(value) {
  const v = (value || '').trim();
  if (v.replace(/\s/g, '').length < MIN_LITERAL_LENGTH) return [];
  const out = new Set([v]);
  const digits = v.replace(/[\s-]/g, '');
  if (/^\d{12,19}$/.test(digits)) {
    out.add(digits);
    out.add(digits.replace(/(\d{4})(?=\d)/g, '$1 '));
    out.add(digits.replace(/(\d{4})(?=\d)/g, '$1-'));
  }
  return [...out];
}

export function scrubText(text, secretValues, { blankInputs = false } = {}) {
  let out = text;
  if (blankInputs) {
    out = out
      // HTML dumps: <input ... value="4242 ...">
      .replace(/(<input\b[^>]*?\bvalue=)("[^"]*"|'[^']*')/gi, '$1"[redacted]"')
      // Playwright error-context.md ARIA snapshot: `- textbox "Card number" [ref=e1]:` then `- text: 4242…` on the next line,
      // or the value inline after the colon.
      .replace(/^(\s*- (?:textbox|combobox|spinbutton|searchbox)\b[^\n]*?):[ \t]+\S.*$/gm, '$1: [value redacted]')
      .replace(/^(\s*- (?:textbox|combobox|spinbutton|searchbox)\b[^\n]*:\n(?:\s*- \/placeholder:[^\n]*\n)?\s*- text:)[^\n]*$/gm, '$1 [value redacted]');
  }
  // Anything shaped like a card number (13-19 digits, optional space/dash separators)
  out = out.replace(/\b(?:\d[ -]?){12,18}\d\b/g, '[redacted-digits]');
  for (const secret of secretValues) {
    for (const variant of secretVariants(secret)) out = out.split(variant).join('[redacted]');
  }
  return out;
}

export function containsSecret(text, secretValues) {
  return secretValues.some((s) => secretVariants(s).some((v) => text.includes(v)));
}

function* walk(p) {
  if (!fs.existsSync(p)) return;
  const st = fs.statSync(p);
  if (st.isFile()) {
    yield p;
    return;
  }
  for (const entry of fs.readdirSync(p)) yield* walk(path.join(p, entry));
}

export function scrubPaths(paths, secretValues, { dropVideos = false, blankInputs = false } = {}) {
  const stats = { deleted: 0, scrubbed: 0, deletedUnscrubbable: 0 };
  for (const root of paths) {
    for (const file of walk(root)) {
      const base = path.basename(file);
      const ext = path.extname(file).toLowerCase();
      if (ALWAYS_DELETE.has(base) || ext === '.zip' || (dropVideos && ext === '.webm')) {
        fs.rmSync(file);
        stats.deleted++;
        continue;
      }
      if (BINARY_EXTENSIONS.has(ext)) continue;
      const original = fs.readFileSync(file, 'utf8');
      const cleaned = scrubText(original, secretValues, { blankInputs });
      if (containsSecret(cleaned, secretValues)) {
        fs.rmSync(file);
        stats.deletedUnscrubbable++;
        continue;
      }
      if (cleaned !== original) {
        fs.writeFileSync(file, cleaned);
        stats.scrubbed++;
      }
    }
  }
  return stats;
}

function main(argv) {
  const flags = new Set(argv.filter((a) => a.startsWith('--')));
  const paths = argv.filter((a) => !a.startsWith('--'));
  if (!paths.length) {
    console.error('usage: scrub-artifacts.mjs [--drop-videos] [--blank-inputs] <path>...');
    process.exit(2);
  }
  const secretValues = SECRET_ENV_VARS.map((k) => process.env[k]).filter(Boolean);
  const stats = scrubPaths(paths, secretValues, {
    dropVideos: flags.has('--drop-videos'),
    blankInputs: flags.has('--blank-inputs'),
  });
  console.log(
    `scrub-artifacts: ${secretValues.length} secret value(s) checked; deleted ${stats.deleted} trace/token/video file(s), ` +
      `scrubbed ${stats.scrubbed} text file(s), deleted ${stats.deletedUnscrubbable} unscrubbable file(s)`
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
