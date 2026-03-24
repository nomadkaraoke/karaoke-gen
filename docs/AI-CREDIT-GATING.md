# AI-Powered Credit Gating

## Overview

Free credit grants (welcome credits and feedback bonus credits) are evaluated by an AI model before being approved. This prevents multi-account abuse where users create multiple accounts to farm free karaoke generation credits.

The system is **fail-closed**: if anything goes wrong (AI error, unclear response, network issue), credits are NOT granted automatically. Instead, the admin is notified to review manually.

## How It Works

```
User verifies email (or submits feedback)
  ↓
Collect abuse signals:
  • Device fingerprint correlations (other accounts with same fingerprint)
  • IP address correlations (other accounts from same IP)
  • IP geolocation (country, ISP, VPN detection)
  • Recent signup patterns (new accounts from same IP/fingerprint in last 24h)
  • User agent string
  • For feedback: the submitted feedback content
  ↓
Clean user? (no correlations, no recent signup spike)
  → YES: Grant credits immediately (no AI call needed)
  → NO: Call Gemini for evaluation
  ↓
Gemini decision:
  • "grant" → Grant credits, user sees celebratory welcome screen
  • "deny"  → No credits, user gets rejection email (CC'd to Andrew)
  • Error/uncertain → No credits, user told "team will review shortly",
                      Andrew gets review email with admin link
```

## Decision Outcomes

| Scenario | Credits | User Experience | Admin Notification |
|----------|---------|-----------------|-------------------|
| Clean user (no correlations) | Granted | "2 free credits!" interstitial | None |
| AI approves | Granted | Same | None |
| AI denies (abuse detected) | Not granted | "Couldn't grant free credits" + buy CTA | CC on rejection email to user |
| AI error / uncertain | Not granted | "Team will review shortly" + buy CTA | Review email with admin page link |
| Evaluation disabled | Granted | Same as clean | None |

## Frontend Flow

The verify page (`/auth/verify`) shows these states in sequence:

1. **"Verifying your sign-in link..."** — Token validation
2. **"Preparing your account..."** — AI evaluation running (2-5 seconds)
3. One of:
   - **Credits granted** — Celebratory screen with credit count, gentle reminder not to abuse, "Start Creating Karaoke" button
   - **Credits denied** — "Couldn't grant free credits" with buy credits CTA and link to dashboard
   - **Credits pending review** — "Team will review shortly" with buy credits CTA and "Explore the App" button
   - **Success** (returning user) — "Welcome back!" with auto-redirect

## Emails

### To User: Credit Denied
- Subject: "About your Nomad Karaoke free credits"
- CC: andrew@beveridge.uk
- Tone: Friendly but firm — explains costs, invites reply if mistake
- Includes buy credits link

### To Admin: Review Needed
- Subject: "Credit review needed: user@example.com (welcome)"
- To: andrew@beveridge.uk
- Includes direct link to user's admin page (`/admin/users/detail?email=...`)
- Explains what to do: grant credits if legitimate, do nothing if not

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `CREDIT_EVAL_ENABLED` | `true` | Set to `false` to disable AI evaluation and auto-grant all credits |
| `CREDIT_EVAL_MODEL` | `gemini-3.1-pro-preview` | Gemini model to use for evaluation |

### Emergency Rollback

Set `CREDIT_EVAL_ENABLED=false` in Cloud Run environment variables. This immediately reverts to the old behavior of auto-granting all credits without AI evaluation.

## Audit Trail

Every evaluation is logged to the `credit_evaluations` Firestore collection:

```json
{
  "id": "uuid",
  "email": "user@example.com",
  "grant_type": "welcome",
  "decision": "grant",
  "reasoning": "No suspicious correlations found — clean user",
  "confidence": 1.0,
  "error": null,
  "model": "gemini-3.1-pro-preview",
  "signals_snapshot": {
    "fingerprint_match_count": 0,
    "ip_match_count": 0,
    "recent_signups_ip": 1,
    "recent_signups_fp": 1,
    "user_ip": "1.2.3.4",
    "user_fingerprint": "abc123..."
  },
  "evaluated_at": "2026-03-23T21:00:00Z"
}
```

## Admin Workflow

When you receive a "Credit review needed" email:

1. Click the "Review User" link to go to their admin page
2. Check their abuse signals: fingerprint matches, IP matches, job history
3. If legitimate: Use the admin credits endpoint to grant 2 credits manually
4. If abusive: No action needed — they already have 0 credits

Note: For `pending_review` cases, the `welcome_credits_granted` flag is NOT set, so the system won't interfere if you grant credits manually later.

## Architecture

### Files

| File | Purpose |
|------|---------|
| `backend/services/credit_evaluation_service.py` | Core service: signal collection, Gemini prompt, response parsing, audit logging |
| `backend/config.py` | `CREDIT_EVAL_ENABLED`, `CREDIT_EVAL_MODEL` config |
| `backend/services/user_service.py` | `grant_welcome_credits_if_eligible()` integration |
| `backend/api/routes/users.py` | Feedback credit flow integration, verify response includes `credit_status` |
| `backend/services/email_service.py` | `send_credit_denied_email()`, `send_credit_review_needed_email()` |
| `backend/models/user.py` | `VerifyMagicLinkResponse.credit_status` field |
| `frontend/app/auth/verify/page.tsx` | 5-state verify flow with interstitials |
| `frontend/lib/types.ts` | `credit_status` type on verify response |

### Signal Collection

The evaluation service calls these existing methods to collect signals:

- `user_service.find_users_by_fingerprint()` — other accounts with same device fingerprint
- `user_service.find_users_by_signup_ip()` — other accounts from same IP
- `user_service.count_recent_signups_from_ip()` — new accounts from same IP in last 24h
- `user_service.count_recent_signups_from_fingerprint()` — new accounts from same fingerprint in last 24h
- `ip_geolocation_service.lookup_ip()` — country, ISP, org info
- Session collection query — latest user agent

### Gemini Prompt

The prompt instructs Gemini to:
- **DENY** if fingerprint matches other accounts (strong multi-accounting signal)
- **DENY** if IP matches + other suspicious patterns (same UA, no spend)
- **GRANT** if appears genuine or insufficient evidence
- **When in doubt, GRANT** — false positives worse than false negatives

The response is structured JSON: `{"decision": "grant"|"deny", "reasoning": "...", "confidence": 0.0-1.0}`

### Quick-Grant Optimization

If no correlations are found at all (no fingerprint matches, no IP matches, recent signup counts ≤ 1), the system skips the AI call entirely and grants immediately. This means most legitimate users experience zero additional latency.
