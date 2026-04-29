# Plan: One-click magic link resend on verify failure

**Date:** 2026-04-29
**Branch:** `feat/sess-20260429-1035-verify-failure-resend`
**Trigger:** Customer `hojob2002@yahoo.com` repeatedly hit "Sign-in failed" because old 15-min magic links arrived after Yahoo's deferral window. PR #734 extended TTL to 24h, but the verify-failure UX still tells the user nothing actionable. Postmark migration is still in flight.

## Goal

When a user lands on `/auth/verify?token=...` and verification fails (expired or used token), instead of a static "Sign-in failed" wall, give them a single-click button that emails a fresh link to the same address that originally received the dead link.

## Scope

In:
- New backend endpoint `POST /api/users/auth/resend-from-token`
- Updated `/auth/verify` page error state UI
- New `lib/api.ts` client method
- Translation keys for 33 locales
- Backend unit tests + frontend Jest test

Out:
- Postmark migration (separate workstream)
- Auto-recovering cancelled jobs (separate concern)
- Changing token TTL again

## Backend design

**Endpoint:** `POST /api/users/auth/resend-from-token`

```python
class ResendFromTokenRequest(BaseModel):
    token: str

class ResendFromTokenResponse(BaseModel):
    status: str  # "sent" | "no_token"
    masked_email: Optional[str]  # "ho***@ya***.com" when sent
    message: str
```

**Behaviour:**
1. Look up `magic_links/{token}` in Firestore.
2. If doc missing → return `{status: "no_token", masked_email: None, message: ...}`. Frontend then degrades to "go to sign-in form".
3. If doc found → extract `email`, `tenant_id`, `referral_code`, mint a new magic link via `user_service.create_magic_link(...)`, send via `email_service.send_magic_link(...)`. Return `masked_email`.
4. Rate limit per-IP (reuse `is_signup_rate_limited` style or a new keyed limiter).
5. Locale-aware messages.

**Security notes:**
- The token in the URL is a 32-byte cryptographic secret — only the original recipient could possess it. Resending to the email already in the doc adds no new attack surface.
- We never accept an email from the request body — no enumeration risk.
- Even if the token was leaked, the new link still goes to the original verified address.

## Frontend design

`app/[locale]/auth/verify/page.tsx` — error state:

- Show partial email (when known) — "We sent a fresh sign-in link to **ho\*\*\*@ya\*\*\*.com**"
- Primary button: **"Email me a new link"** (only when token doc was found server-side; pre-fetched on first failure response or fetched on click)
- After click: spinner → success state ("Check your inbox at ho\*\*\*@ya\*\*\*.com — link is valid for 24 hours")
- If `status: "no_token"`: hide resend button, show "Go to sign-in" linking to `/app`

Two-step flow for the fetch:
1. On verify failure, immediately call `resendMagicLinkFromToken(token)` to learn whether the token exists. *No, keep it user-initiated* — don't auto-send (user might have come from email scanner and not actually want to re-trigger). Show the button, send on click.
2. To know whether to show the button, we need the masked email. Two options:
   - (a) Show the button optimistically; if backend returns `no_token`, swap to fallback message.
   - (b) Add a `GET /api/users/auth/token-info?token=...` that returns masked email or 404.
   
   Going with **(a)**: simpler, one fewer endpoint. UX impact is small — worst case is one click revealing "this link doesn't exist".

## Translation keys (en.json `auth` namespace)

- `linkExpiredOrUsedExplained` — "This sign-in link is no longer valid. It may have expired or already been used."
- `resendNewLinkButton` — "Email me a new link"
- `resending` — "Sending…"
- `resendSuccessTitle` — "Check your email"
- `resendSuccessBody` — "We've sent a new sign-in link to {maskedEmail}. It's valid for the next 24 hours."
- `resendNoTokenTitle` — "Couldn't find that link"
- `resendNoTokenBody` — "Please go back to the sign-in form to request a new one."
- `resendFailedTitle` — "Couldn't send a new link"
- `resendFailedBody` — "Please try again in a moment, or go back to sign in."

## Tests

**Backend** (`backend/tests/test_users_auth.py` or extend `test_anti_abuse.py`):
- Token exists, valid → returns `sent` with masked email, new doc created in Firestore
- Token exists, used → still resends (used links are still proof of original ownership)
- Token doesn't exist → returns `no_token`, no email sent
- Empty/whitespace token → 422
- Rate limit by IP after N requests in window

**Frontend** (`frontend/__tests__/auth/verify-page.test.tsx`):
- Error state shows resend button
- Click resend → calls API → shows success state with masked email
- API returns `no_token` → shows fallback message

## Files

- Add: `backend/api/routes/users.py` (new endpoint + request/response models)
- Add: `backend/models/user.py` (response models)
- Modify: `frontend/app/[locale]/auth/verify/page.tsx`
- Modify: `frontend/lib/api.ts` (new client method)
- Modify: `frontend/messages/en.json` + auto-translate to 32 others
- Add: tests as above
- Modify: `pyproject.toml` (version bump)

## Sequence

1. Backend endpoint + tests
2. Frontend client + verify page UI + Jest test
3. en.json keys + `python frontend/scripts/translate.py --target all`
4. Run `make test`
5. Bump version, PR, ship via `/shipit`
