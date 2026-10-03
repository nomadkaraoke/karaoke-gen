// frontend/e2e/helpers/self-healing/learned-store.ts
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import type { Action, LearnedVariant, LearnedVariantsFile } from './types';

/**
 * learned-variants.json holds step recipes the LLM fallback discovered on live
 * runs. The daily E2E workflow opens an auto-merging PR whenever a run adds one,
 * so the next run replays it deterministically (no LLM) — "1 Stripe change =
 * 1 auto-merged data PR".
 */
export const LEARNED_VARIANTS_PATH = path.join(__dirname, 'learned-variants.json');

/** Keep only the newest N per step: Stripe A/B-tests layouts, so a few recent ones are useful; ancient ones are noise. */
export const MAX_LEARNED_PER_STEP = 5;

export function emptyFile(): LearnedVariantsFile {
  return { version: 1, steps: {} };
}

export function loadLearned(file = LEARNED_VARIANTS_PATH): LearnedVariantsFile {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as LearnedVariantsFile;
    if (parsed?.version === 1 && parsed.steps && typeof parsed.steps === 'object') return parsed;
    console.warn(`  ⚠️ ${file} has unexpected shape — ignoring learned variants`);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') {
      console.warn(`  ⚠️ Could not read ${file}: ${(e as Error).message} — ignoring learned variants`);
    }
  }
  return emptyFile();
}

/** Stable id from the actions only (not timestamps), so re-learning the same recipe dedupes. */
export function variantId(actions: Action[]): string {
  return crypto.createHash('sha256').update(JSON.stringify(actions)).digest('hex').slice(0, 12);
}

/**
 * Merge newly-healed recipes into the file (pure — returns a new object).
 * Returns `changed: false` when every recipe was already known.
 */
export function mergeLearned(
  current: LearnedVariantsFile,
  healed: Array<{ stepId: string; actions: Action[]; model?: string }>,
  meta: { runUrl?: string; now?: string } = {}
): { file: LearnedVariantsFile; changed: boolean; added: Array<{ stepId: string; variant: LearnedVariant }> } {
  const file: LearnedVariantsFile = JSON.parse(JSON.stringify(current));
  const added: Array<{ stepId: string; variant: LearnedVariant }> = [];
  for (const h of healed) {
    if (!h.actions.length) continue;
    const id = variantId(h.actions);
    const list = file.steps[h.stepId] || [];
    if (list.some((v) => v.id === id)) continue;
    const variant: LearnedVariant = {
      id,
      learnedAt: meta.now || new Date().toISOString(),
      ...(meta.runUrl ? { runUrl: meta.runUrl } : {}),
      ...(h.model ? { model: h.model } : {}),
      actions: h.actions,
    };
    // Newest first — the most recent layout is the likeliest to be live.
    file.steps[h.stepId] = [variant, ...list].slice(0, MAX_LEARNED_PER_STEP);
    added.push({ stepId: h.stepId, variant });
  }
  return { file, changed: added.length > 0, added };
}

export function serialize(file: LearnedVariantsFile): string {
  return JSON.stringify(file, null, 2) + '\n';
}
