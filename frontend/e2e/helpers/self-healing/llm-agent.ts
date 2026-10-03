// frontend/e2e/helpers/self-healing/llm-agent.ts
import { executeAction } from './actions';
import { observe, type Observation } from './observe';
import type { Action, ActionType, CheckoutStep, StepContext, TargetSpec } from './types';

/**
 * LLM fallback for one checkout step: observe → ask the model for ONE action →
 * execute → verify, up to MAX_ITERATIONS. The model only ever picks elements by
 * semantic description and uses `{{PLACEHOLDER}}` tokens for secrets; we
 * substitute real values locally.
 */

export interface LlmTarget extends Omit<TargetSpec, 'frameUrlIncludes'> {
  frameIndex?: number;
}

export interface LlmDecision {
  thought: string;
  status: 'act' | 'done' | 'give_up';
  action?: { type: ActionType; target: LlmTarget; value?: string };
}

export interface PlannerInput {
  stepId: string;
  goal: string;
  allowSubmit: boolean;
  placeholders: string[];
  history: string[];
  observation: Observation;
  /** 0 = cheapest model; incremented after failures to escalate. */
  escalation: number;
}

export interface Planner {
  decide(input: PlannerInput): Promise<{ decision: LlmDecision; model: string }>;
}

export interface HealResult {
  success: boolean;
  actions: Action[];
  model: string;
  llmCalls: number;
  reason?: string;
}

const MAX_ITERATIONS = 6;
const MAX_ESCALATIONS = 2;

/** Express/alternative payment methods and the cancel/back link — the agent must never pick these. */
const FORBIDDEN_CLICK_RE = /\b(back to|cancel|link|klarna|cash ?app|bank|affirm|afterpay|google pay|apple pay|amazon pay|paypal)\b/i;
/** The final submit button ("Pay", "Pay $0.50", "Subscribe"…) — only allowed in the submit step. */
const SUBMIT_RE = /^\s*(pay|subscribe|buy|purchase|place order|complete (order|purchase)|confirm( payment)?)\s*(\$?[\d.,]+)?\s*$/i;

/** Returns a rejection reason, or null if the action is acceptable. */
export function checkGuards(
  action: NonNullable<LlmDecision['action']>,
  step: Pick<CheckoutStep, 'allowSubmit' | 'placeholders'>
): string | null {
  const label = action.target.name || action.target.text || '';
  if (action.type === 'click' && FORBIDDEN_CLICK_RE.test(label)) {
    return `Refused: "${label}" looks like an alternative/express payment method. Use the card form only.`;
  }
  if (action.type === 'click' && !step.allowSubmit && SUBMIT_RE.test(label)) {
    return `Refused: clicking the final "${label}" button is not allowed in this step.`;
  }
  if (action.value !== undefined) {
    const allowed = new Set(step.placeholders || []);
    for (const m of action.value.matchAll(/\{\{([A-Z_]+)\}\}/g)) {
      if (!allowed.has(m[1])) return `Refused: placeholder {{${m[1]}}} is not available in this step.`;
    }
    // A model must never invent card-like numbers — secrets come only via placeholders.
    if (/\d{4,}/.test(action.value.replace(/\{\{[A-Z_]+\}\}/g, '').replace(/\s/g, ''))) {
      return 'Refused: literal digits in value. Use a {{PLACEHOLDER}} for secret values.';
    }
  }
  if (action.target.by === 'css' && !action.target.css) return 'Refused: by=css requires css.';
  if (action.target.by === 'role' && !action.target.role) return 'Refused: by=role requires role.';
  if (['text', 'placeholder', 'label'].includes(action.target.by) && !action.target.text) {
    return `Refused: by=${action.target.by} requires text.`;
  }
  return null;
}

export function toTargetSpec(t: LlmTarget, frameKeys: string[]): TargetSpec {
  const { frameIndex, ...rest } = t;
  const spec: TargetSpec = { ...rest };
  if (frameIndex !== undefined && frameKeys[frameIndex]) spec.frameUrlIncludes = frameKeys[frameIndex];
  // Drop empty optional fields the model sometimes emits.
  for (const k of Object.keys(spec) as (keyof TargetSpec)[]) {
    if (spec[k] === '' || spec[k] === null) delete spec[k];
  }
  return spec;
}

export async function pollVerify(step: CheckoutStep, ctx: StepContext, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  do {
    if (await step.verify(ctx).catch(() => false)) return true;
    await ctx.page.waitForTimeout(500);
  } while (Date.now() < deadline);
  return false;
}

export async function healStep(step: CheckoutStep, ctx: StepContext, planner: Planner): Promise<HealResult> {
  const history: string[] = [];
  const executed: Action[] = [];
  let escalation = 0;
  let llmCalls = 0;
  let model = '';

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const observation = await observe(ctx.page, ctx.secrets);
    let decision: LlmDecision;
    try {
      const res = await planner.decide({
        stepId: step.id,
        goal: step.goal,
        allowSubmit: !!step.allowSubmit,
        placeholders: step.placeholders || [],
        history,
        observation,
        escalation,
      });
      llmCalls++;
      decision = res.decision;
      model = res.model;
    } catch (e) {
      return { success: false, actions: executed, model, llmCalls, reason: `planner error: ${(e as Error).message}` };
    }
    console.log(`    🤖 [${model}] ${decision.status}: ${decision.thought}`);

    if (decision.status === 'give_up') {
      history.push(`You gave up: ${decision.thought}`);
      if (++escalation > MAX_ESCALATIONS) break;
      continue;
    }
    if (decision.status === 'done' || !decision.action) {
      if (await pollVerify(step, ctx, 3_000)) return { success: true, actions: executed, model, llmCalls };
      history.push('You said the goal is done, but the automated check says it is NOT. Look again.');
      escalation = Math.min(escalation + 1, MAX_ESCALATIONS);
      continue;
    }

    const refusal = checkGuards(decision.action, step);
    if (refusal) {
      history.push(refusal);
      continue;
    }
    const action: Action = {
      type: decision.action.type,
      target: toTargetSpec(decision.action.target, observation.frameKeys),
      ...(decision.action.value !== undefined ? { value: decision.action.value } : {}),
    };
    try {
      const loc = await executeAction(ctx.page, action, ctx.secrets);
      if (action.type === 'fill') ctx.lastFilled = loc;
      executed.push(action);
      history.push(`OK: ${JSON.stringify(action)}`);
    } catch (e) {
      history.push(`FAILED: ${JSON.stringify(action)} — ${(e as Error).message.split('\n')[0]}`);
      escalation = Math.min(escalation + 1, MAX_ESCALATIONS);
      continue;
    }
    // Clicks can trigger slow transitions (accordion, redirect); after a fill,
    // a short check suffices — the model will usually need more actions.
    const verifyMs = action.type === 'click' ? step.verifyTimeoutMs ?? 5_000 : 2_000;
    if (await pollVerify(step, ctx, verifyMs)) {
      return { success: true, actions: executed, model, llmCalls };
    }
  }
  return { success: false, actions: executed, model, llmCalls, reason: 'goal not verified after LLM attempts' };
}

// ---------------------------------------------------------------------------
// Gemini planner
// ---------------------------------------------------------------------------

/** Cheapest first; escalation walks down the list. Unavailable model ids are skipped. */
export const DEFAULT_MODELS = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-3.8-flash'];

const SYSTEM_PROMPT = `You are a careful browser-automation assistant completing ONE sub-goal on a Stripe hosted checkout page during an automated end-to-end test of our own product.
You receive: the sub-goal, a history of your previous actions and their results, and an observation of every frame (ARIA snapshot + interactive element attributes + a screenshot where input contents are masked).
Respond with exactly ONE next action (status "act"), or status "done" if the sub-goal is already achieved, or "give_up" if it is impossible.

Rules:
- Pay ONLY with the card form. Never click Link, Klarna, Cash App, Bank, Affirm, Google/Apple Pay or any other alternative/express method.
- Never type real data. Use ONLY the {{PLACEHOLDER}} tokens listed for this step as values (e.g. "{{CARD_NUMBER}}"). Non-secret literals (e.g. a country name for a select) are allowed.
- Do not click the final Pay/submit button unless the step explicitly allows it.
- Identify targets by STABLE semantics so the same description works on future runs:
  prefer by="role" with role + accessible name; else by="label"/"placeholder"/"text"; use by="css" only with stable attributes (id, name, autocomplete, data-testid), never generated class names or nth-child.
- Set frameIndex to the frame[N] that contains the element.
- If the right element exists but is hidden behind an overlay or visually hidden, set includeHidden=true.
- Do not repeat an action that already FAILED with the same target; try a different description.`;

const RESPONSE_SCHEMA = {
  type: 'object',
  properties: {
    thought: { type: 'string', description: 'One short sentence of reasoning.' },
    status: { type: 'string', enum: ['act', 'done', 'give_up'] },
    action: {
      type: 'object',
      properties: {
        type: { type: 'string', enum: ['click', 'fill', 'select', 'check', 'uncheck', 'press'] },
        target: {
          type: 'object',
          properties: {
            frameIndex: { type: 'integer' },
            by: { type: 'string', enum: ['role', 'text', 'css', 'placeholder', 'label'] },
            role: { type: 'string' },
            name: { type: 'string' },
            exact: { type: 'boolean' },
            text: { type: 'string' },
            css: { type: 'string' },
            includeHidden: { type: 'boolean' },
          },
          required: ['by'],
        },
        value: { type: 'string' },
      },
      required: ['type', 'target'],
    },
  },
  required: ['thought', 'status'],
};

function isModelUnavailable(e: unknown): boolean {
  const msg = String((e as Error)?.message || e);
  return /\b404\b|not found|NOT_FOUND|is not supported|unsupported model|invalid model/i.test(msg);
}

export function buildPrompt(input: PlannerInput): string {
  return [
    `## Sub-goal (step "${input.stepId}")\n${input.goal}`,
    `## Allowed placeholders\n${input.placeholders.length ? input.placeholders.map((p) => `{{${p}}}`).join(', ') : '(none)'}`,
    `## Final Pay/submit button allowed in this step: ${input.allowSubmit ? 'YES' : 'NO'}`,
    `## History\n${input.history.length ? input.history.map((h, i) => `${i + 1}. ${h}`).join('\n') : '(no actions yet)'}`,
    `## Observation (page ${input.observation.url})\n${input.observation.text}`,
  ].join('\n\n');
}

export class GeminiPlanner implements Planner {
  private readonly unavailable = new Set<string>();

  constructor(
    private readonly apiKey: string,
    private readonly models: string[] = DEFAULT_MODELS
  ) {}

  static fromEnv(): GeminiPlanner | null {
    const key = process.env.GEMINI_API_KEY;
    if (!key) return null;
    const models = (process.env.E2E_SELF_HEAL_MODELS || '').split(',').map((m) => m.trim()).filter(Boolean);
    return new GeminiPlanner(key, models.length ? models : DEFAULT_MODELS);
  }

  async decide(input: PlannerInput): Promise<{ decision: LlmDecision; model: string }> {
    // Lazy import: the SDK is only needed when a fallback actually fires.
    const { GoogleGenAI } = await import('@google/genai');
    // vertexai:false — Vertex AI is disabled in the nomadkaraoke GCP project; this
    // key bills to the AI Studio project (see project_no_ai_spend_in_gcp_project).
    const ai = new GoogleGenAI({ apiKey: this.apiKey, vertexai: false });
    const candidates = this.models.filter((m) => !this.unavailable.has(m));
    const ordered = candidates.slice(Math.min(input.escalation, Math.max(candidates.length - 1, 0)));
    let lastErr: unknown;
    for (const model of ordered) {
      try {
        const parts: Array<{ text: string } | { inlineData: { mimeType: string; data: string } }> = [
          { text: buildPrompt(input) },
        ];
        if (input.observation.screenshotJpegBase64) {
          parts.push({ inlineData: { mimeType: 'image/jpeg', data: input.observation.screenshotJpegBase64 } });
        }
        const res = await ai.models.generateContent({
          model,
          contents: [{ role: 'user', parts }],
          config: {
            systemInstruction: SYSTEM_PROMPT,
            responseMimeType: 'application/json',
            responseJsonSchema: RESPONSE_SCHEMA,
            temperature: 0,
            httpOptions: { timeout: 45_000 },
          },
        });
        const decision = JSON.parse(res.text || '{}') as LlmDecision;
        if (!decision.status) throw new Error(`malformed response: ${res.text?.slice(0, 200)}`);
        return { decision, model };
      } catch (e) {
        lastErr = e;
        if (isModelUnavailable(e)) {
          console.log(`    🤖 model ${model} unavailable — skipping (${String((e as Error).message).slice(0, 120)})`);
          this.unavailable.add(model);
        } else {
          console.log(`    🤖 model ${model} error — trying next (${String((e as Error).message).slice(0, 120)})`);
        }
      }
    }
    throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
  }
}
