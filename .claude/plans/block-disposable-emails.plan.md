# Plan: Block Disposable Email Registration

**Created:** 2026-03-19
**Branch:** feat/sess-20260319-1332-disposable-email-abuse
**Status:** Draft

## Overview

Users are signing up with disposable email addresses (yopmail.com, emailhook.site, etc.) to get unlimited free credits (2 per account). The `EmailValidationService` already exists with 160+ disposable domains but is **never called during signup**. We need to wire it into the magic link flow and show a friendly rejection message.

## Context

- The `EmailValidationService` (`backend/services/email_validation_service.py`) is fully implemented with disposable domain detection, email/IP blocklists, and admin management APIs
- The admin blocklist management routes exist (`backend/api/routes/rate_limits.py`) and work
- The magic link endpoint (`POST /api/users/auth/magic-link` in `backend/api/routes/users.py:133`) **never calls** the validation service
- Currently 1 yopmail user and 1 emailhook.site user in `gen_users`
- Each new account gets 2 free welcome credits automatically

## Requirements

- [x] Reject disposable email domains at the magic link endpoint (backend)
- [x] Return a **specific, distinguishable error** so the frontend can show a friendly message (not the generic "email enumeration" response)
- [x] Show a warm, friendly message in the AuthDialog explaining why and encouraging real email use
- [x] Add `emailhook.site` to the default disposable domains list
- [x] Add backend unit tests for the integration
- [x] Add frontend test coverage for the new error state

## Design Decision: Explicit Rejection vs Silent Failure

The current magic link endpoint returns a generic success message to prevent email enumeration. For disposable emails, we will **explicitly reject** with a 422 status and a specific error message. This is the right trade-off because:

1. We *want* the user to know their disposable email won't work (so they can use a real one)
2. Disposable domain detection is not sensitive information — the blocklist is public knowledge
3. The friendly message serves our goal of converting them to real email users

## Implementation Steps

### Step 1: Backend — Add disposable email check to magic link endpoint

**File:** `backend/api/routes/users.py`

Add the `EmailValidationService` check **after** email lowercasing (line 158) and **before** tenant validation (line 164). Return a 422 with a friendly message.

```python
# Check for disposable email domains
email_validation = get_email_validation_service()
if email_validation.is_disposable_domain(email):
    logger.warning(f"Blocked disposable email signup attempt: {_mask_email(email)}")
    raise HTTPException(
        status_code=422,
        detail="disposable_email_not_allowed"
    )
```

Use a machine-readable error code (`disposable_email_not_allowed`) so the frontend can match on it and show the custom friendly message. This keeps the backend response lean and puts the UX copy in the frontend where it belongs.

### Step 2: Backend — Also check email and IP blocklists

In the same location, add checks for explicitly blocked emails and IPs:

```python
if email_validation.is_email_blocked(email):
    logger.warning(f"Blocked email signup attempt: {_mask_email(email)}")
    # Silent reject — return generic success to prevent enumeration
    return SendMagicLinkResponse(
        status="success",
        message="If this email is registered, you will receive a sign-in link shortly."
    )

ip_address = http_request.client.host if http_request.client else None
if ip_address and email_validation.is_ip_blocked(ip_address):
    logger.warning(f"Blocked IP signup attempt: {ip_address}")
    return SendMagicLinkResponse(
        status="success",
        message="If this email is registered, you will receive a sign-in link shortly."
    )
```

Note: Blocked emails/IPs get silent rejection (anti-enumeration). Disposable domains get explicit rejection (we want the user to switch to a real email).

### Step 3: Backend — Add `emailhook.site` to default disposable domains

**File:** `backend/services/email_validation_service.py`

Add `emailhook.site` to `DEFAULT_DISPOSABLE_DOMAINS` set.

### Step 4: Frontend — Handle the disposable email error in AuthDialog

**File:** `frontend/components/auth/AuthDialog.tsx`

The `sendMagicLink` call in `auth.ts` already catches errors and sets `error` from the API response message. The `handleResponse` in `api.ts` throws an `ApiError` with the `detail` string for non-200 responses.

Update `handleSendMagicLink` to detect the `disposable_email_not_allowed` error code and show a friendly multi-line message instead of the terse error string:

```tsx
const success = await sendMagicLink(email.trim().toLowerCase())
if (success) {
  setStep("sent")
}
// If error contains our specific code, override with friendly message
// (the error state is already set by the auth store)
```

We need to detect the specific error. The cleanest approach: check the error string in the `displayError` rendering. If it matches `disposable_email_not_allowed`, render a custom friendly block instead of the red error text.

The friendly message:

> **We don't support disposable email addresses**
>
> We'd love to help you create karaoke videos! Please use your regular email address so we can send you a notification when your video is ready.
>
> We promise — no spam, no marketing emails, ever. We only email you about your karaoke videos.

### Step 5: Backend tests

**File:** `backend/tests/test_email_validation_integration.py` (new)

Test cases:
1. Magic link with disposable email returns 422 with `disposable_email_not_allowed`
2. Magic link with legitimate email proceeds normally
3. Magic link with blocked email returns silent success (no actual email sent)
4. Magic link with blocked IP returns silent success

### Step 6: Frontend tests

**File:** `frontend/components/__tests__/AuthDialog.test.tsx` (new or extend existing)

Test cases:
1. Submitting a disposable email shows the friendly rejection message
2. The friendly message contains the key copy (no spam promise, use real email)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/api/routes/users.py` | Modify | Add email validation checks to magic link endpoint |
| `backend/services/email_validation_service.py` | Modify | Add `emailhook.site` to default domains |
| `frontend/components/auth/AuthDialog.tsx` | Modify | Handle disposable email error with friendly message |
| `backend/tests/test_email_validation_integration.py` | Create | Integration tests for the validation in signup flow |

## Testing Strategy

- **Unit tests:** Validate `is_disposable_domain` catches yopmail, emailhook.site (already covered in existing tests)
- **Integration tests:** New test file verifying the magic link endpoint rejects disposable emails with 422
- **Frontend tests:** Verify the AuthDialog shows friendly message on disposable email error
- **Manual verification:** Try signing up with `test@yopmail.com` on dev server to see the UX

## Rollback Plan

Remove the validation check from `send_magic_link()` in `users.py`. The `EmailValidationService` itself remains unchanged (it's already deployed and used by admin routes).
