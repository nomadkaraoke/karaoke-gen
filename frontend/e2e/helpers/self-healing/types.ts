// frontend/e2e/helpers/self-healing/types.ts
import type { Locator, Page } from '@playwright/test';

/**
 * Serializable description of an element. Produced by the LLM fallback and
 * persisted in learned-variants.json, so it must stay plain JSON and must
 * describe the element by *stable semantics* (role/name/text/attributes), never
 * by snapshot refs or indexes that change per run.
 */
export interface TargetSpec {
  /** Substring of the frame URL (host + path, no query) to search first. */
  frameUrlIncludes?: string;
  by: 'role' | 'text' | 'css' | 'placeholder' | 'label';
  role?: string;
  name?: string;
  /** Exact (case-sensitive, whole-string) name/text match. Default false = case-insensitive substring. */
  exact?: boolean;
  text?: string;
  css?: string;
  /** Match elements hidden from the accessibility tree / not visible (clicks become dispatchEvent). */
  includeHidden?: boolean;
}

export type ActionType = 'click' | 'fill' | 'select' | 'check' | 'uncheck' | 'press';

/**
 * One UI action. `value` may contain secret placeholders like `{{CARD_NUMBER}}`
 * which are substituted at execution time — real secrets never appear in an
 * Action that is sent to or produced by the LLM, or persisted to disk.
 */
export interface Action {
  type: ActionType;
  target: TargetSpec;
  value?: string;
}

/** Values the runner may substitute into `{{PLACEHOLDER}}` tokens. */
export type Secrets = Record<string, string>;

export interface StepContext {
  page: Page;
  secrets: Secrets;
  /** The element most recently filled during the current step (reset per step) — lets fill-step verifies check the value landed in a plausible field. */
  lastFilled?: Locator;
}

/** A deterministic, hand-written way to accomplish a step. */
export interface BuiltinVariant {
  name: string;
  run: (ctx: StepContext) => Promise<void>;
}

export interface CheckoutStep {
  id: string;
  /** Natural-language goal handed to the LLM fallback. */
  goal: string;
  builtins: BuiltinVariant[];
  /** Postcondition: did the step actually achieve its goal? Must be side-effect free. */
  verify: (ctx: StepContext) => Promise<boolean>;
  /** How long verify may poll after each variant before declaring it failed. */
  verifyTimeoutMs?: number;
  /**
   * Optional steps (e.g. a ZIP field that only some layouts have) never call
   * the LLM and never fail the run — a missing field is caught by the submit
   * step's verify instead, whose LLM fallback can fill whatever is required.
   */
  optional?: boolean;
  /** Whether the LLM may click the final Pay/submit button in this step. */
  allowSubmit?: boolean;
  /** Placeholders the LLM may use in this step, e.g. ['CARD_NUMBER']. */
  placeholders?: string[];
}

export interface LearnedVariant {
  id: string;
  learnedAt: string;
  runUrl?: string;
  model?: string;
  actions: Action[];
}

export interface LearnedVariantsFile {
  version: 1;
  steps: Record<string, LearnedVariant[]>;
}

export type StepOutcome =
  | { stepId: string; status: 'ok'; via: 'builtin' | 'learned'; variant: string }
  | { stepId: string; status: 'healed'; model: string; actions: Action[]; llmCalls: number }
  | { stepId: string; status: 'skipped'; reason: string }
  | { stepId: string; status: 'failed'; reason: string; llmCalls: number };
