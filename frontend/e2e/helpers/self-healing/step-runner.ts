// frontend/e2e/helpers/self-healing/step-runner.ts
import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { executeAction } from './actions';
import { healStep, pollVerify, type Planner } from './llm-agent';
import { loadLearned, mergeLearned, serialize } from './learned-store';
import type { CheckoutStep, LearnedVariantsFile, Secrets, StepContext, StepOutcome } from './types';

/**
 * Runs a flow as a sequence of verified sub-goals:
 *   learned variants (newest first) → hand-written builtins → LLM fallback.
 * Each attempt is judged ONLY by the step's `verify` postcondition — never by
 * "the click didn't throw" — so a silently-wrong variant can't pass.
 */
export class SelfHealingRunner {
  readonly outcomes: StepOutcome[] = [];
  private readonly forceLlm: Set<string>;
  private readonly ctx: StepContext;

  constructor(
    page: Page,
    secrets: Secrets,
    private readonly planner: Planner | null,
    private readonly learned: LearnedVariantsFile = loadLearned(),
    opts: { forceLlmSteps?: string[] } = {}
  ) {
    this.ctx = { page, secrets };
    this.forceLlm = new Set(
      opts.forceLlmSteps ??
        (process.env.E2E_SELF_HEAL_FORCE_LLM || '').split(',').map((s) => s.trim()).filter(Boolean)
    );
  }

  async run(step: CheckoutStep): Promise<StepOutcome> {
    const outcome = await this.attempt(step);
    this.outcomes.push(outcome);
    const icon = { ok: '✓', healed: '🩹', skipped: '–', failed: '✘' }[outcome.status];
    console.log(`  ${icon} step ${step.id}: ${outcome.status}${'variant' in outcome ? ` (${outcome.via}: ${outcome.variant})` : ''}`);
    return outcome;
  }

  private async attempt(step: CheckoutStep): Promise<StepOutcome> {
    const ctx = this.ctx;
    ctx.lastFilled = undefined;
    const verifyMs = step.verifyTimeoutMs ?? 5_000;

    // Already satisfied (e.g. card fields visible without choosing "Card")?
    if (await step.verify(ctx).catch(() => false)) {
      return { stepId: step.id, status: 'ok', via: 'builtin', variant: 'already-satisfied' };
    }

    const forced = this.forceLlm.has(step.id);
    if (forced) console.log(`  ⚙️ step ${step.id}: E2E_SELF_HEAL_FORCE_LLM — skipping deterministic variants`);
    const aborted = (): StepOutcome | null => {
      const reason = step.abortReason?.();
      return reason ? { stepId: step.id, status: 'failed', reason, llmCalls: 0 } : null;
    };

    if (!forced) {
      for (const v of this.learned.steps[step.id] || []) {
        try {
          for (const a of v.actions) {
            const loc = await executeAction(ctx.page, a, ctx.secrets);
            if (a.type === 'fill') ctx.lastFilled = loc;
          }
          if (await pollVerify(step, ctx, verifyMs)) {
            return { stepId: step.id, status: 'ok', via: 'learned', variant: v.id };
          }
        } catch (e) {
          console.log(`    learned variant ${v.id} failed: ${(e as Error).message.split('\n')[0]}`);
        }
        const stop = aborted();
        if (stop) return stop;
      }
      for (const b of step.builtins) {
        try {
          await b.run(ctx);
          if (await pollVerify(step, ctx, verifyMs)) {
            return { stepId: step.id, status: 'ok', via: 'builtin', variant: b.name };
          }
        } catch (e) {
          console.log(`    builtin "${b.name}" failed: ${(e as Error).message.split('\n')[0]}`);
        }
        const stop = aborted();
        if (stop) return stop;
      }
    }

    if (step.optional && !forced) {
      return { stepId: step.id, status: 'skipped', reason: 'optional step: no variant applied (field may not exist in this layout)' };
    }
    if (!this.planner) {
      return { stepId: step.id, status: 'failed', reason: 'all deterministic variants failed and no LLM planner is configured (GEMINI_API_KEY unset)', llmCalls: 0 };
    }

    console.log(`  🤖 step ${step.id}: deterministic variants exhausted — LLM fallback`);
    const heal = await healStep(step, ctx, this.planner);
    if (heal.success && heal.actions.length) {
      return { stepId: step.id, status: 'healed', model: heal.model, actions: heal.actions, llmCalls: heal.llmCalls };
    }
    if (heal.success) {
      // Verified with zero actions (e.g. it only needed time) — nothing to learn.
      return { stepId: step.id, status: 'ok', via: 'builtin', variant: 'verified-after-wait' };
    }
    const reason = step.abortReason?.() || heal.reason || 'LLM fallback failed';
    return { stepId: step.id, status: 'failed', reason, llmCalls: heal.llmCalls };
  }

  get healed() {
    return this.outcomes.filter((o): o is Extract<StepOutcome, { status: 'healed' }> => o.status === 'healed');
  }

  /**
   * Write `<dir>/self-heal-report.json` always, and — only when the caller has
   * independently confirmed the whole flow succeeded — `<dir>/learned-variants.json`
   * containing the merged recipes, for the workflow to commit.
   */
  writeArtifacts(dir: string, opts: { flowVerified: boolean; runUrl?: string }): { learnedWritten: boolean } {
    fs.mkdirSync(dir, { recursive: true });
    const healed = this.healed;
    const llmCalls = this.outcomes.reduce((n, o) => n + ('llmCalls' in o ? o.llmCalls : 0), 0);
    let learnedWritten = false;
    let added: string[] = [];
    if (opts.flowVerified && healed.length) {
      const merged = mergeLearned(this.learned, healed, { runUrl: opts.runUrl });
      if (merged.changed) {
        fs.writeFileSync(path.join(dir, 'learned-variants.json'), serialize(merged.file));
        learnedWritten = true;
        added = merged.added.map((a) => `${a.stepId}:${a.variant.id}`);
      }
    }
    fs.writeFileSync(
      path.join(dir, 'self-heal-report.json'),
      JSON.stringify({ flowVerified: opts.flowVerified, llmCalls, learnedWritten, added, outcomes: this.outcomes }, null, 2)
    );
    return { learnedWritten };
  }
}
