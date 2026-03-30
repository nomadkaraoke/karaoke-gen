# Plan: Pre-compute Credit Evaluation at Magic Link Send Time

**Created:** 2026-03-25
**Branch:** feat/sess-20260324-2308-welcome-credit-abuse
**Status:** Draft

## Overview

Move the AI credit evaluation from verify-magic-link time to send-magic-link time. By the time the user clicks the link in their email (typically 10-20+ seconds later), the decision is already made and verification is instant. If they click very fast, fall back to computing inline (same experience as today).

## Requirements

- [ ] Run credit evaluation asynchronously after sending the magic link email
- [ ] Store the evaluation result on the magic link document in Firestore
- [ ] At verify time, read pre-computed result if available
- [ ] Fall back to inline evaluation if result isn't ready yet (preserves "preparing" UX)
- [ ] Only evaluate new users (existing users skip evaluation already)
- [ ] No change to the user-facing experience — just faster

## Technical Approach

**Key insight:** `send_magic_link` already has all the signals needed (email, IP, fingerprint) and already creates the user via `get_or_create_user()`. We just need to run the evaluation after sending the email and store the result.

**Threading:** Use `threading.Thread` to run evaluation in background so the magic link response returns immediately. FastAPI's async doesn't help here since the evaluation is sync (Firestore + Gemini calls).

**Storage:** Add `credit_eval_decision`, `credit_eval_reasoning`, and `credit_eval_error` fields to the magic link Firestore document. The verify endpoint already reads this document.

## Implementation Steps

1. [ ] Add evaluation result fields to magic link document schema
2. [ ] Create helper function that runs evaluation and writes result to magic link doc
3. [ ] Call helper in background thread from `send_magic_link` (new users only)
4. [ ] Update `verify_magic_link` to check for pre-computed result before calling evaluation
5. [ ] Update tests
6. [ ] Bump version

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/api/routes/users.py` | Modify | Trigger background eval in send_magic_link; read pre-computed result in verify_magic_link |
| `backend/services/user_service.py` | Modify | Add method to store eval result on magic link doc |
| `backend/models/user.py` | Modify | Add optional eval fields to MagicLinkToken model |

## Testing Strategy

- Update existing anti-abuse tests to verify pre-computation flow
- Test fallback path (no pre-computed result → inline evaluation)
- Test that send_magic_link still returns quickly (doesn't block on eval)

## Rollback Plan

The fallback path IS the current behavior. If pre-computation breaks, verify just runs evaluation inline as before.
