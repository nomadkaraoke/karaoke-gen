# Plan: AI-Powered Credit Gating

**Created:** 2026-03-23
**Branch:** feat/sess-20260323-1716-ai-credit-gating
**Status:** Implemented

## Overview

Replace the automatic welcome/feedback credit grants with an AI-powered evaluation step. When a user triggers a credit grant (email verification or feedback submission), instead of immediately granting credits, we collect abuse signals and ask Gemini to make a grant/deny decision. Suspected abusers get a friendly rejection email (CC'd to Andrew) instead of free credits.

## Requirements

- [ ] Welcome credit grants go through AI evaluation before granting
- [ ] Feedback credit grants go through AI evaluation before granting
- [ ] AI evaluates: fingerprint correlations, IP correlations, IP geo info, user agent, signup recency patterns
- [ ] Approved users get credits as before (seamless experience)
- [ ] Rejected users get a friendly-but-firm email explaining the situation, CC'd to andrew@beveridge.uk
- [ ] Rejection email invites them to reply if it's a mistake
- [ ] Decision is logged to Firestore for audit trail
- [ ] Fail-open: if AI evaluation fails, grant credits (don't punish users for our errors)
- [ ] Admin can override decisions via existing admin credit grant endpoint

## Technical Approach

### Architecture

```
User verifies email / submits feedback
  ↓
Collect abuse signals (sync, fast):
  - User's fingerprint + IP
  - Other accounts with same fingerprint (from correlations)
  - Other accounts with same IP
  - IP geolocation info
  - Recent signup count from same IP/fingerprint (last 24h)
  - User agent string
  - For feedback: the feedback content itself
  ↓
Call Gemini (vertexai/gemini-2.5-pro-preview) with structured prompt:
  - Present all signals
  - Ask for JSON response: {decision: "grant" | "deny", reasoning: string, confidence: number}
  ↓
If "grant": grant credits normally, log decision
If "deny": skip credit grant, send rejection email, log decision
If AI call fails: grant credits (fail-open), log error
```

### Key Design Decisions

1. **Two-phase verification**: The magic link verify endpoint returns immediately with session + a `credit_status: "evaluating"` field. The frontend shows a "preparing your account" loading screen, then polls/waits for the evaluation result via a new lightweight endpoint. This keeps the auth flow snappy while the AI evaluation runs in the background of the same request or via a short follow-up call. The frontend then shows either a celebratory "2 free credits!" interstitial or a "sorry, couldn't grant credits" interstitial before navigating to the dashboard.

2. **Fail-open**: If Gemini is down or returns an error, we grant credits. Better to let an occasional abuser through than block legitimate users.

3. **Use google-genai directly**: The project already has `langchain-google-genai` installed. We can use the simpler `google.generativeai` library directly for a focused prompt/response, or use the existing LangChain setup. We'll use `google.generativeai` for simplicity since this is a single-turn evaluation, not an agentic workflow.

4. **Structured JSON output**: Ask Gemini for structured output with decision, reasoning, and confidence to make decisions parseable and auditable.

5. **Log all decisions**: Store every evaluation in a `credit_evaluations` Firestore collection for audit trail and to tune the prompt over time.

## Implementation Steps

### Step 1: Add google-generativeai dependency
- Add `google-generativeai` to pyproject.toml (lightweight, direct Gemini access)
- Verify it works with Vertex AI auth in the nomadkaraoke project

### Step 2: Create credit evaluation service
- New file: `backend/services/credit_evaluation_service.py`
- `CreditEvaluationService` class with:
  - `evaluate_credit_grant(email, grant_type, feedback_content=None) -> CreditEvaluation`
  - Collects all abuse signals by calling existing services (user_service, ip_geolocation_service)
  - Builds prompt with signals
  - Calls Gemini for decision
  - Logs decision to Firestore
  - Returns structured result

### Step 3: Build the Gemini prompt
- System prompt explaining the role: "You are an anti-abuse evaluator for a karaoke video service..."
- Present all signals in a structured format
- Request JSON response: `{decision: "grant"|"deny", reasoning: "...", confidence: 0.0-1.0}`
- Include clear guidelines: same fingerprint across accounts = very suspicious, same IP = somewhat suspicious, etc.

### Step 4: Create rejection email template
- Add `send_credit_denied_email(email, reasoning)` to EmailService
- Friendly-but-firm tone
- CC andrew@beveridge.uk
- Invite reply if it's a mistake
- Use existing email template infrastructure (branding, styling)

### Step 5: Integrate into welcome credit flow
- Modify `grant_welcome_credits_if_eligible()` in user_service.py
- Before granting: call `CreditEvaluationService.evaluate_credit_grant(email, "welcome")`
- If denied: send rejection email, return False
- If error: log warning, grant anyway (fail-open)

### Step 6: Integrate into feedback credit flow
- Modify feedback endpoint in users.py
- Before granting credits: evaluate with feedback content included
- If denied: return different response message, send rejection email
- If error: grant anyway

### Step 7: Add Firestore collection for audit trail
- Collection: `credit_evaluations`
- Document fields: email, grant_type, decision, reasoning, confidence, signals_snapshot, model_used, evaluated_at
- TTL: keep indefinitely (small volume, high audit value)

### Step 8: Tests
- Unit tests for signal collection
- Unit tests for prompt building
- Unit tests for decision parsing (grant/deny/error cases)
- Integration test for the evaluation service with mocked Gemini
- Integration test for welcome credit flow with evaluation
- Integration test for feedback credit flow with evaluation
- Test fail-open behavior

### Step 9: Configuration
- Add env vars: `CREDIT_EVAL_MODEL` (default: "gemini-2.5-pro-preview"), `CREDIT_EVAL_ENABLED` (default: true)
- Add to config.py

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Add google-generativeai dependency |
| `backend/config.py` | Modify | Add credit evaluation config vars |
| `backend/services/credit_evaluation_service.py` | Create | New service: collect signals, call Gemini, log decision |
| `backend/services/user_service.py` | Modify | Integrate evaluation into welcome credit grant |
| `backend/api/routes/users.py` | Modify | Integrate evaluation into feedback credit grant |
| `backend/services/email_service.py` | Modify | Add credit denied email template |
| `backend/tests/test_credit_evaluation.py` | Create | Tests for evaluation service |
| `backend/tests/test_anti_abuse.py` | Modify | Update welcome credit tests for evaluation |
| `frontend/app/auth/verify/page.tsx` | Modify | Add "preparing account" loading + credit result interstitial |
| `frontend/lib/api.ts` | Modify | Add credit evaluation status endpoint |

## Testing Strategy

- **Unit tests**: Signal collection, prompt building, response parsing, fail-open behavior
- **Integration tests**: Full evaluation flow with mocked Gemini responses
- **E2E tests**: Manual verification in production with test accounts
- **Edge cases**: Gemini timeout, malformed response, missing signals, first-ever user (no correlations)

## Open Questions

1. **Gemini model**: Using `gemini-3.1-pro-preview` as specified — already available in the nomadkaraoke GCP project.

2. **Latency UX**: Frontend shows a friendly "preparing your account" loading screen while evaluation runs. Then transitions to either a celebratory "2 free credits!" message (with gentle reminder not to abuse) or a "sorry, couldn't grant free credits" message with a buy credits CTA. User clicks through to dashboard from either.

3. **Should we also gate the existing rate limiter?** Currently `is_signup_rate_limited()` silently blocks at 2 accounts per IP/fingerprint per 24h. The AI evaluation is complementary — rate limiter blocks obvious rapid abuse, AI evaluation catches more subtle patterns.

## Rollback Plan

Set `CREDIT_EVAL_ENABLED=false` in Cloud Run env vars → immediate rollback to auto-grant behavior. No data model changes needed. The evaluation service is purely additive.
