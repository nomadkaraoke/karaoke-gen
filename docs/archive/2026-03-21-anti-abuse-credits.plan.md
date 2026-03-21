# Plan: Anti-Abuse Improvements for Free Credits System

**Created:** 2026-03-21
**Branch:** feat/sess-20260321-0043-anti-abuse-credits
**Status:** Implemented

## Overview

Three improvements to prevent abuse of the 2-free-credits-per-account system:

1. **Per-IP signup rate limiting** — max 2 new accounts per IP per 24 hours
2. **Device fingerprinting** — detect same-browser multi-account abuse
3. **Credits on verification, not creation** — move welcome credits from account creation to first magic link verification

These are ordered by implementation complexity (simplest first) and can be shipped independently.

## Background

Investigation of 60 recent signups (2026-03-20) found disposable email abuse and potential multi-account patterns. The disposable email detection (DeBounce + verifymail.io, shipped in v0.144.0) blocks throwaway email services, but doesn't prevent someone from creating multiple accounts with real emails from the same device/IP.

## Part 1: Per-IP Signup Rate Limiting

### Problem

No limit on how many accounts can be created from a single IP address. An abuser can create unlimited accounts from the same machine.

### Approach

Track IP addresses on magic link requests (signup trigger). Before creating a magic link, check how many **distinct new accounts** were created from that IP in the last 24 hours. If >= 2, silently reject (return 200 to prevent enumeration, same pattern as blocked email/IP).

**Why track at magic link request, not user creation?**
- Magic link request is the user-facing action (they enter email, click "send")
- User creation happens inside `get_or_create_user()` which is called during verification
- Rate limiting at the entry point prevents even sending the email

### Data model

Use the existing `magic_links` collection — it already stores `ip_address` and `email`. Query: count distinct emails with magic links created from this IP in the last 24 hours where the email doesn't already exist as a user.

No new collection needed. Just a query at magic link request time.

### Implementation

**File: `backend/api/routes/users.py` — `send_magic_link()` endpoint**

After the disposable domain check and blocked email/IP checks (lines 157-175), add:

```python
# Per-IP signup rate limit (only for new users)
existing_user = user_service.get_user(email)
if existing_user is None:
    # This is a new signup — check IP rate limit
    recent_signups = user_service.count_recent_signups_from_ip(
        ip_address, hours=24
    )
    if recent_signups >= 2:
        logger.warning(
            f"IP signup rate limit hit: {ip_address} "
            f"({recent_signups} signups in 24h)"
        )
        # Silent reject (anti-enumeration)
        return SendMagicLinkResponse(
            status="success",
            message="If this email is registered, you will receive a login link."
        )
```

**File: `backend/services/user_service.py`**

Add method:

```python
def count_recent_signups_from_ip(self, ip_address: str, hours: int = 24) -> int:
    """Count distinct new-user magic link requests from this IP in the last N hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    # Query magic_links where ip_address matches and created_at > cutoff
    links = (
        self.db.collection("magic_links")
        .where("ip_address", "==", ip_address)
        .where("created_at", ">=", cutoff.isoformat())
        .stream()
    )
    # Count distinct emails that are NOT existing users
    emails = set()
    for link in links:
        data = link.to_dict()
        email = data.get("email", "")
        # Only count if this email resulted in a new user creation
        user_doc = self.db.collection("gen_users").document(email).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            created_at = user_data.get("created_at", "")
            if created_at >= cutoff.isoformat():
                emails.add(email)
    return len(emails)
```

**Optimization note:** This approach queries Firestore for each magic link email. For production, a simpler approach would be to add a Firestore composite index on `magic_links(ip_address, created_at)` and count distinct emails in the result set. At our volume (~60 signups/day), the extra reads are negligible.

**Alternative simpler approach:** Instead of querying magic_links, track signups directly by adding `signup_ip` to the `gen_users` document at creation time. Then query `gen_users` where `signup_ip == ip AND created_at >= cutoff`. This is cleaner and avoids cross-collection queries.

**Recommended: Use the simpler approach.** Add `signup_ip` field to User model, set it in `get_or_create_user()`, and query it for rate limiting.

### Edge cases

- **Shared IPs (offices, universities, VPNs):** 2 per day is generous enough for legitimate use. If a real user is blocked, they can try again tomorrow or contact support.
- **IPv6 addresses:** Rate limit on the full address. Don't try to normalize /64 subnets — too complex and most residential IPv6 users get a stable address.
- **Existing users logging in:** Rate limit only applies to new signups, not returning users.
- **`169.254.169.126` (load balancer IP):** This is the LB IP seen on sessions/magic_links — need to ensure we're using the real client IP from X-Forwarded-For. Check how `ip_address` is set in the magic link endpoint. (From investigation: it uses `http_request.client.host` which should be the real IP behind Cloud Run's proxy, but verify.)

### Tests

- Unit test: `count_recent_signups_from_ip` returns correct count
- Unit test: rate limit triggered → silent 200 response
- Unit test: existing user login not rate limited
- Unit test: 1 signup from IP → allowed, 2nd → allowed, 3rd → blocked
- Integration test: magic link endpoint returns 200 (silent reject) when IP limit hit

---

## Part 2: Device Fingerprinting

### Problem

A determined abuser can use multiple IPs (VPN, mobile data, etc.) to bypass IP rate limiting. Device fingerprinting catches same-browser multi-account abuse regardless of IP.

### Approach

Use [FingerprintJS](https://github.com/nicedayfor/fingerprintjs) (open source, free) to generate a browser fingerprint on the frontend. Send it with the magic link request. Store it on the user document. Check it during signup rate limiting alongside IP.

**Why FingerprintJS open source (not Pro)?**
- Free, no API key needed
- Runs entirely client-side
- ~60-70% accuracy for identifying unique browsers (good enough as a signal, not sole blocker)
- Pro version ($0/mo for 20k identifications) is more accurate but adds external dependency

### Data flow

```
Frontend: Generate fingerprint → send with magic link request
Backend:  Store fingerprint on magic link → check at signup
          Store fingerprint on user document → cross-reference
```

### Frontend implementation

**File: `frontend/lib/fingerprint.ts` (new)**

```typescript
import FingerprintJS from "@nicedayfor/fingerprintjs";

let cachedFingerprint: string | null = null;

export async function getDeviceFingerprint(): Promise<string> {
  if (cachedFingerprint) return cachedFingerprint;
  const fp = await FingerprintJS.load();
  const result = await fp.get();
  cachedFingerprint = result.visitorId;
  return cachedFingerprint;
}
```

**File: `frontend/lib/auth.ts` — modify `requestMagicLink()`**

Add fingerprint to the magic link request body:

```typescript
const fingerprint = await getDeviceFingerprint();
const response = await fetch("/api/users/auth/magic-link", {
  method: "POST",
  body: JSON.stringify({ email, device_fingerprint: fingerprint }),
});
```

### Backend implementation

**File: `backend/api/routes/users.py`**

Add `device_fingerprint: Optional[str] = None` to `SendMagicLinkRequest` model.

Pass it through to `create_magic_link()` and store on the magic link document.

**File: `backend/models/user.py`**

Add `device_fingerprint: Optional[str] = None` to both `MagicLinkToken` and `User` models.

**File: `backend/services/user_service.py`**

- In `create_magic_link()`: store `device_fingerprint` on the magic link doc
- In `get_or_create_user()`: store `device_fingerprint` on the user doc (from the magic link that triggered creation)
- Add `count_recent_signups_from_fingerprint()`: similar to IP rate limit but by fingerprint

### Rate limiting with fingerprint

Combine IP and fingerprint signals:

```python
# In send_magic_link endpoint, for new users:
ip_signups = user_service.count_recent_signups_from_ip(ip_address, hours=24)
fp_signups = user_service.count_recent_signups_from_fingerprint(
    device_fingerprint, hours=24
) if device_fingerprint else 0

if ip_signups >= 2 or fp_signups >= 2:
    # Silent reject
    ...
```

### Edge cases

- **No fingerprint sent (old clients, API users, bots):** Fall back to IP-only rate limiting. Don't require fingerprint — it's an additional signal, not a hard requirement.
- **Fingerprint collision:** FingerprintJS open source has ~60-70% uniqueness. Two different people could get the same fingerprint. The 2-per-day limit is generous enough to handle occasional collisions.
- **Incognito/private browsing:** FingerprintJS still works reasonably well in incognito (canvas, WebGL, fonts are still consistent). Accuracy drops slightly.
- **Browser extensions that block fingerprinting:** Fall back to IP-only.

### Tests

- Frontend: Unit test for fingerprint generation and caching
- Backend: Unit test for fingerprint storage on magic link and user
- Backend: Unit test for fingerprint-based rate limiting
- Backend: Integration test for combined IP + fingerprint rate limiting
- Backend: Test that missing fingerprint gracefully degrades to IP-only

### Dependencies

```bash
# Frontend
npm install @nicedayfor/fingerprintjs
```

No backend dependencies needed (fingerprint is just a string).

---

## Part 3: Credits on Verification, Not Creation

### Problem

Currently, 2 welcome credits are granted in `get_or_create_user()` — at account creation time, before the user has verified their email. This means:
- Unverified accounts get credits (wasted if user never verifies)
- The user creation can be triggered by requesting a magic link (no verification needed)
- An attacker could create accounts and use credits without ever clicking a magic link

### Approach

Move welcome credit granting from `get_or_create_user()` to the magic link verification endpoint (`verify`). Credits are only granted after the user successfully clicks the magic link (proving email ownership).

### Implementation

**File: `backend/services/user_service.py` — `get_or_create_user()`**

Remove welcome credit granting:

```python
# BEFORE (lines 125-142):
user = User(email=email, credits=NEW_USER_FREE_CREDITS, ...)
credit_transaction = CreditTransaction(
    amount=NEW_USER_FREE_CREDITS, reason="welcome_credit", ...
)

# AFTER:
user = User(email=email, credits=0, ...)
# No credit transaction at creation
```

**File: `backend/services/user_service.py` — new method**

```python
def grant_welcome_credits_if_eligible(self, email: str) -> bool:
    """Grant welcome credits on first verification, if not already granted."""
    user = self.get_user(email)
    if not user:
        return False

    # Check if welcome credits were already granted
    for txn in user.credit_transactions:
        if txn.reason == "welcome_credit":
            return False  # Already received

    # Grant credits
    self.add_credits(
        email=email,
        amount=NEW_USER_FREE_CREDITS,
        reason="welcome_credit",
    )
    return True
```

**File: `backend/api/routes/users.py` — `verify` endpoint**

After successful verification (line ~283), add:

```python
# Grant welcome credits on first verification
credits_granted = user_service.grant_welcome_credits_if_eligible(user.email)
if credits_granted:
    logger.info(f"Granted {NEW_USER_FREE_CREDITS} welcome credits to {_mask_email(user.email)}")
```

### Migration consideration

Existing users who signed up before this change already have their welcome credits. The `grant_welcome_credits_if_eligible()` method checks for existing `welcome_credit` transactions, so it won't double-grant.

**Edge case: Users who signed up but haven't verified yet.** These users have credits but haven't verified. After the change, they'll still have their credits (we don't revoke). When they eventually verify, the method sees the existing `welcome_credit` transaction and skips. No action needed.

### Frontend impact

The verify response already includes the user's credit balance. The frontend should show a "You received 2 free credits!" message on first verification. Check if this already exists in the welcome flow.

**File: `frontend/app/auth/verify/page.tsx`**

The verify page already handles first-login detection (line ~57 checks for `is_first_login` in the response). We may need to add `credits_granted` to the verify response so the frontend can show a celebratory message.

### Tests

- Unit test: `get_or_create_user()` creates user with 0 credits
- Unit test: `grant_welcome_credits_if_eligible()` grants credits on first call
- Unit test: `grant_welcome_credits_if_eligible()` skips if already granted
- Integration test: full flow — create user (0 credits) → verify magic link (2 credits)
- Integration test: verify twice → credits only granted once

---

## Implementation Steps (All 3 Parts)

### Phase 1: Credits on verification (Part 3) — lowest risk, highest impact
1. [ ] Move credit granting from `get_or_create_user()` to new `grant_welcome_credits_if_eligible()`
2. [ ] Call `grant_welcome_credits_if_eligible()` in verify endpoint
3. [ ] Add `credits_granted` to verify response
4. [ ] Update frontend verify page to show credit grant message
5. [ ] Unit + integration tests
6. [ ] Version bump, ship

### Phase 2: Per-IP signup rate limiting (Part 1) — simple, effective
7. [ ] Add `signup_ip` field to User model
8. [ ] Set `signup_ip` in `get_or_create_user()` (passed from magic link endpoint)
9. [ ] Add `count_recent_signups_from_ip()` method
10. [ ] Add rate limit check in `send_magic_link()` endpoint (before sending email)
11. [ ] Unit + integration tests
12. [ ] Version bump, ship

### Phase 3: Device fingerprinting (Part 2) — most complex
13. [ ] Install FingerprintJS in frontend
14. [ ] Create `frontend/lib/fingerprint.ts`
15. [ ] Send fingerprint with magic link request
16. [ ] Add `device_fingerprint` to MagicLinkToken and User models
17. [ ] Store fingerprint in `create_magic_link()` and `get_or_create_user()`
18. [ ] Add `count_recent_signups_from_fingerprint()` method
19. [ ] Combine IP + fingerprint rate limiting in `send_magic_link()`
20. [ ] Unit + integration tests (frontend + backend)
21. [ ] Version bump, ship

## Files to Create/Modify

| File | Action | Part | Description |
|------|--------|------|-------------|
| `backend/services/user_service.py` | Modify | 1,2,3 | Rate limit methods, credit granting changes, fingerprint storage |
| `backend/api/routes/users.py` | Modify | 1,2,3 | Rate limit check, credit grant on verify, fingerprint passthrough |
| `backend/models/user.py` | Modify | 1,2 | Add `signup_ip`, `device_fingerprint` fields |
| `backend/tests/test_user_service.py` | Modify | 1,2,3 | Tests for all new methods |
| `backend/tests/test_email_validation_integration.py` | Modify | 1 | Rate limit integration test |
| `frontend/lib/fingerprint.ts` | Create | 2 | FingerprintJS wrapper |
| `frontend/lib/auth.ts` | Modify | 2 | Send fingerprint with magic link request |
| `frontend/app/auth/verify/page.tsx` | Modify | 3 | Show credit grant message |
| `pyproject.toml` | Modify | 1,2,3 | Version bumps |
| `frontend/package.json` | Modify | 2 | Add FingerprintJS dependency |

## Testing Strategy

- **Unit tests**: Mock Firestore for rate limit counting, credit granting, fingerprint storage
- **Integration tests**: TestClient for magic link → silent reject flow, verify → credit grant flow
- **E2E test**: After deploy, attempt signup with disposable domain (blocked), then attempt 3 signups from same IP (3rd blocked)

## Open Questions

- [x] Should we show a user-facing message when IP rate limited, or always silently reject? **Recommendation: silent reject (anti-enumeration), same as blocked email/IP.**
- [x] Should fingerprint rate limiting be a hard block or just a signal for review? **Recommendation: hard block (same as IP), since the 2-per-day limit is generous.**
- [x] Should we backfill `signup_ip` for existing users from their first magic link/session? **Recommendation: no, not worth the complexity. Only applies to new signups going forward.**
- [x] Should the feedback-for-credits flow also require verification? **Recommendation: it already requires completed jobs, which implies verification. No change needed.**

## Rollback Plan

Each part can be rolled back independently:
1. **Credits on verification:** Revert code → new users get credits at creation again. Existing verified users unaffected.
2. **IP rate limiting:** Revert code → no rate limiting. `signup_ip` field remains on docs but unused.
3. **Fingerprinting:** Revert code → fingerprint not sent/stored. FingerprintJS dependency can be removed.

No data migrations needed for any rollback.
