# Vanity Referral URL — Approve/Deny Queue (rename-in-place)

**Date:** 2026-08-30
**Branch:** feat/sess-20260830-2049-vanity-referral-approval

## Problem

When a user requests a vanity referral code, the backend only emails Andrew — no
record is persisted. To "approve", the admin had to click **Create Vanity Link** and
retype the owner email + desired code, which creates a *second* link alongside the
user's auto-generated one (split stats, two live codes). The **Edit** modal cannot
change a link's code. There is no approve/deny concept and no notification to the user.
Net: confusing and unintuitive.

## Decisions (confirmed with Andrew)

1. **Approve = rename in place.** Change the user's existing link's `code` to the
   vanity code (stats preserved, one unified link). Old code doc is kept but **disabled**
   with a `renamed_to` pointer so stale links 404 cleanly.
2. **Full approve/deny queue.** Persist requests in Firestore; show a "Pending Vanity
   Requests" banner on `/admin/referrals` with one-click Approve/Deny; email the user on
   approval.

## Backend

### Model (`backend/models/referral.py`)
- `VanityRequestDoc`: `id`, `owner_email`, `current_code`, `desired_code`,
  `status` ("pending"|"approved"|"denied"), `created_at`, `resolved_at`,
  `resolved_by`, `note`.
- `VanityRequestResponse` for the admin list.

### Service (`backend/services/referral_service.py`)
- `REFERRAL_VANITY_REQUESTS_COLLECTION = "referral_vanity_requests"`.
- `create_vanity_request(owner_email, current_code, desired_code)` — doc id =
  owner_email (a re-request overwrites the pending one). Resets status→pending.
- `list_vanity_requests(status="pending")`, `get_vanity_request(request_id)`.
- `rename_link(old_code, new_code)` → `(success, link, message)`:
  1. validate new code (vanity pattern + reserved + not taken by a *different* link),
  2. copy old doc → new doc (`is_vanity=True`, stats + config preserved, `updated_at` bumped),
  3. migrate `referred_by_code` on `gen_users` and `referral_code` on `referral_earnings`
     that point at `old_code` → `new_code`,
  4. disable old doc (`enabled=False`, `renamed_to=new_code`).
- `approve_vanity_request(request_id, resolved_by)` — re-fetch owner's *enabled* link,
  rename it to `desired_code`, mark request approved.
- `deny_vanity_request(request_id, resolved_by, note=None)`.
- **Fix `get_or_create_link`**: prefer an `enabled` link for the owner; only fall back to
  a disabled one / create if none enabled. Prevents the renamed old alias resurfacing.

### Routes (`backend/api/routes/referrals.py`)
- `request_vanity_url`: also `create_vanity_request(...)` (keep the existing email).
- `GET /admin/vanity-requests?status=pending`
- `POST /admin/vanity-requests/{request_id}/approve` → approve + email user
  ("Your vanity referral URL is live: nomadkaraoke.com/r/<code>").
- `POST /admin/vanity-requests/{request_id}/deny` → deny (+ optional note; no email by default).

## Frontend (`/admin/referrals` — English-only, no i18n)
- `frontend/lib/types.ts`: `VanityRequest`.
- `frontend/lib/api.ts` `adminApi`: `listVanityRequests`, `approveVanityRequest`,
  `denyVanityRequest`.
- `frontend/app/admin/referrals/page.tsx`: "Pending Vanity Requests" Card above the
  links table — one row per request: `owner — current → desired`, `[Approve] [Deny]`
  with per-request loading + error, then reload requests + links.

## Tests
- Backend unit (`backend/tests/test_referral_service.py` + routes):
  rename preserves stats & disables old; attribution (`referred_by_code`) migrated;
  `get_or_create_link` prefers enabled; approve renames + marks approved;
  approve fails cleanly if desired code was taken meanwhile; deny marks denied;
  request persisted on `me/vanity-request`.
- Frontend: light Jest test that the banner renders a pending request and calls approve.
- Bump `pyproject.toml`.
