# Mobile app backend support (handoff, app checkout return, Google/Apple sign-in) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Nomad Karaoke mobile app (nomad-mobile Phase 1, "Make it") what it needs from gen: a web-review handoff link, a checkout that returns to the app, Google and Apple sign-in, a refund flag in the job summary, and local-stack fakes to test them.

**Architecture:** Everything goes in the existing FastAPI routes and services, plus one small change to the Next.js `/auth/verify` page. Sign-in endpoints reuse the email-code sign-in's user creation and session path and return the same `VerifyMagicLinkResponse`. Fakes are gated on fake mode (`FAKE_WORKERS`), exactly like the existing `test_support` routes.

**Tech Stack:** FastAPI, Firestore (emulator in tests), PyJWT with JWKS (`jwt.PyJWKClient`) for Google and Apple ID tokens, Next.js frontend.

**Spec:** `/Users/andrew/Projects/nomadkaraoke/nomad-mobile-phase1-make-pay-watch/docs/superpowers/specs/2026-10-17-mobile-phase1-make-it-design.md` §5 and §6. The executor reads it too.

## Global Constraints
- Every new auth path ends in the **same user-creation and session code** as `POST /api/users/auth/verify-code` (`backend/api/routes/login_code.py`): welcome credit, attribution, referral, disposable-email, signup-cap, blocklist and tenant rules. Factor out a shared helper rather than copying.
- Tokens are never long-lived in URLs. Handoff tokens are single-use with a TTL of **120 seconds or less**.
- Fake-token acceptance exists **only** when fake mode is on (the same switch that mounts `test_support`). Tests prove it's rejected otherwise.
- The return-URL feature must not create an open redirect. The client sends `return_to: "app"`, never a URL.
- Blocking Firestore and JWKS calls stay off the event loop (`def` routes or `asyncio.to_thread`). See the BLOCKING_CALLS guard and `docs/LESSONS-LEARNED.md`.
- New response models use `response_model=` (the mobile app generates its client from OpenAPI).
- Version bump: `pyproject.toml` 0.277.1 → 0.278.0 (minor: new endpoints). Re-read main's version before merging, in case it moved.
- Config values, all non-secret:
  - `GOOGLE_OAUTH_CLIENT_IDS`: comma-separated audiences, default empty. Empty means `/auth/google` returns 503 `google_not_configured`, except for fake tokens in fake mode.
  - `APPLE_SIGNIN_AUDIENCES`: default `com.nomadkaraoke.mobile`.
  - `MOBILE_APP_SCHEME`: default `nomadkaraoke`.

## Review Focus
1. **A handoff link opened twice, or after 2 minutes:** the second open gets the web's normal "link expired, sign in" state, not an error page or a silent sign-in.
2. **A Google token whose `email_verified` is false, or whose `aud` is a different client:** 401 `invalid_token`, and no user is created.
3. **An email already registered by magic link signing in with Google:** the same user and the same credits, with no second welcome credit. (Add a test in Task 4.)
4. **Apple relay addresses (`@privaterelay.appleid.com`):** they are accepted as a normal email and must not trip the disposable-email block. (Add a test in Task 4.)
5. **A checkout cancelled from the app:** the bounce redirects to `nomadkaraoke://checkout?status=cancelled`, and a Stripe webhook never fires. (Add a test in Task 2.)

---

### Task 1: Handoff token → signed-in web page

**Files:**
- Modify: `backend/api/routes/login_code.py` (or `users.py` beside `/auth/verify`; follow whichever holds the auth routes), `backend/services/user_service.py`, and `frontend/app/[locale]/auth/verify/page.tsx`
- Test: `backend/tests/test_handoff_token.py` and the frontend jest test beside the verify page (follow existing test locations)

**Interfaces:**
- Produces `POST /api/users/auth/handoff-token`, authenticated with a Bearer session.
  - Body: `HandoffTokenRequest {target: Literal["job_review"], job_id: str, locale?: str}`
  - Response: `HandoffTokenResponse {url: str, expires_in_seconds: int}`
  - The URL is `{FRONTEND_URL}/{locale}/auth/verify?token=<t>&handoff=1`.
  - After verifying, the web lands on `/app/jobs#/{job_id}/review`, through the existing `job_review:<id>` purpose → `redirect_path` mapping in `/auth/verify` (`users.py` around line 400).
- Errors:
  - 401 `invalid_session`
  - 404 `not_found`: the job is missing or not owned. Use the same 404 for both.
  - 409 `not_in_review`: the status isn't `awaiting_review` or `in_review`.

- [ ] Step 1: Write failing tests:
  - `test_handoff_returns_url_for_owned_review_job`: the URL contains `handoff=1` and a token; `expires_in_seconds <= 120`.
  - `test_handoff_404_for_other_users_job`
  - `test_handoff_409_when_not_in_review`
  - `test_handoff_401_without_session`
  - `test_handoff_token_single_use`: verifying the token twice → the second fails.
  - `test_handoff_token_expires`: freeze or advance time by 121s → verify fails.
- [ ] Step 2: Run `pytest backend/tests/test_handoff_token.py -v`. Expect FAIL.
- [ ] Step 3: Implement.
  - Mint with the existing magic-link machinery (`create_admin_login_token` path), extended with a seconds-level expiry: add `expiry_seconds: Optional[int]`, or an equivalent that keeps existing callers unchanged.
  - Purpose: `job_review:<id>`.
  - Confirm the magic-link verify already marks tokens used. If not, enforce single use for tokens minted here.
  - Reuse the ownership and status logic from `kjbox.py::_mint_review_link`. Lift the shared part into a helper both call.
- [ ] Step 4: Change the frontend verify page: when `handoff=1` is in the query, verify immediately, without the "Complete Sign-In" tap. Email links never carry the parameter, so the scanner guard stays for them. Add a jest test: with `handoff=1` it calls verify on mount; without it, it waits for the tap.
- [ ] Step 5: Tests pass. Commit `feat(auth): handoff-token endpoint for mobile → signed-in web review`.

### Task 2: Checkout that returns to the app

**Files:**
- Modify: `backend/api/routes/users.py` (`create_checkout`, `CreateCheckoutRequest`), `backend/services/fake_workers/checkout.py` if it builds its own URLs
- Test: `backend/tests/test_checkout_app_return.py`

**Interfaces:**
- `CreateCheckoutRequest` gains `return_to: Optional[Literal["app"]] = None`. When it's `"app"`, gen sets:
  - `success_url = {API_BASE}/api/users/credits/app-return?status=success&session_id={CHECKOUT_SESSION_ID}`
  - `cancel_url = {API_BASE}/api/users/credits/app-return?status=cancelled`

  `API_BASE` is the public backend URL. Use the existing setting gen uses for absolute API links, which is `LOCAL_STACK_API_URL` on the local stack; find it, and don't invent a new one unless none exists.
- `GET /api/users/credits/app-return?status=success|cancelled[&session_id=…]` → **302** to `{MOBILE_APP_SCHEME}://checkout?status=<status>`. Any other `status` → `cancelled`. `session_id` isn't forwarded. Response body: a tiny HTML fallback with a "Return to the app" link, for browsers that block scheme redirects.

- [ ] Step 1: Failing tests:
  - `test_checkout_return_to_app_sets_bounce_urls`: mock `StripeService.create_checkout_session` and assert the `success_url` / `cancel_url` it receives.
  - `test_checkout_default_urls_unchanged`: without `return_to`.
  - `test_app_return_redirects_success`: 302 with `Location: nomadkaraoke://checkout?status=success`.
  - `test_app_return_redirects_cancelled_for_unknown_status`
  - In fake mode, `test_fake_checkout_cancel_reaches_app_return`: create a checkout with `return_to:"app"`, POST the fake checkout's cancel → it redirects to the app-return URL. No credits are added.
- [ ] Step 2: Run, and expect FAIL. Step 3: implement. Step 4: tests pass.
- [ ] Step 5: Commit `feat(credits): checkout return_to=app with https bounce to the mobile app scheme`.

### Task 3: Job summary refund flag + fakes for mobile tests

**Files:**
- Modify:
  - `backend/services/firestore_service.py` (`SUMMARY_FIELD_PATHS`)
  - `backend/api/routes/jobs.py` (`_SUMMARY_STATE_DATA_KEYS`, if `credit_refunded` lives in state_data; check where `_refund_credit_for_job` writes it)
  - `backend/services/fake_workers/config.py`
  - `backend/services/fake_workers/pipeline.py`
  - the fake search (find where `search_result_count` is applied)
- Test: extend `backend/tests/` fake-worker tests (follow existing files for the fake pipeline and fake config).

**Interfaces:**
- The job summary (`GET /api/jobs?fields=summary`) includes `credit_refunded` wherever the full job has it.
- New fake-config knobs:
  - `youtube_url_enabled: bool = True`: when true, the fake video step sets `state_data.youtube_url = "https://youtu.be/dQw4w9WgXcQ"` for non-private jobs.
  - `search_confidence: Literal["perfect","recommended","unsure"] = "perfect"`: shapes fake search results so gen's own frontend ranking (`frontend/lib/audio-search-utils.ts` `getSearchConfidence`) yields tier 1, 2 or 3 for the requested artist and title.
    - `unsure`: titles that don't match the request, low seeders, YouTube or other.
    - Read `getSearchConfidence` to pick values that land in each tier, and assert it in a frontend jest test feeding the three fake result shapes, so the fake and the ranking can't drift.

- [ ] Step 1: Failing tests:
  - `test_summary_includes_credit_refunded`
  - `test_fake_video_sets_youtube_url_when_public`
  - `test_fake_video_no_youtube_url_when_disabled`
  - `test_fake_search_confidence_shapes` (backend: the shape of the results)
  - `fake-search-tiers.test.ts` (frontend: tier 1/2/3)
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS.
- [ ] Step 5: Commit `feat(local-stack): refund flag in job summary + youtube/search-confidence fake knobs`.

### Task 4: Google and Apple sign-in

**Files:**
- Create: `backend/services/social_identity.py` (token verification)
- Modify: `backend/api/routes/login_code.py` (endpoints next to email-code, sharing its user and session helper), `backend/config.py`
- Test: `backend/tests/test_social_signin.py`

**Interfaces:**
- `verify_google_id_token(id_token: str, audiences: list[str]) -> VerifiedIdentity`
  - JWKS: `https://www.googleapis.com/oauth2/v3/certs`
  - Issuers: `accounts.google.com` and `https://accounts.google.com`
  - Requires `email_verified` to be true.
- `verify_apple_id_token(id_token: str, audiences: list[str]) -> VerifiedIdentity`
  - JWKS: `https://appleid.apple.com/auth/keys`
  - Issuer: `https://appleid.apple.com`
  - `email_verified` may be the string `"true"`.
- Both:
  - `VerifiedIdentity = {email: str (lowercased), provider: Literal["google","apple"], subject: str}`.
  - Raise `InvalidIdentityToken` on any failure.
  - Cache `PyJWKClient` per URL.
- `POST /api/users/auth/google` and `POST /api/users/auth/apple`
  - Body: `SocialSignInRequest {id_token: str, locale?: str, device_fingerprint?: str, referral_code?: str, attribution?: dict}`, mirroring `send-code`'s optional fields.
  - Response: `VerifyMagicLinkResponse`.
  - Errors:
    - 401 `invalid_token`
    - 503 `google_not_configured`
    - 422 for blocked or disposable emails, the same as send-code. Relay emails are **not** blocked.
- Fake mode: an `id_token` of the form `fake:<email>` is accepted without a network call, `provider` taken from the route. Without fake mode, `fake:` tokens → 401.

- [ ] Step 1: Failing tests. Use a locally generated RSA key and a monkeypatched JWKS client, so there are no network calls.
  - `test_google_valid_token_creates_user_and_session`
  - `test_google_existing_email_same_user_no_second_welcome_credit` (Review Focus 3)
  - `test_google_unverified_email_401`
  - `test_google_wrong_audience_401`
  - `test_google_expired_401`
  - `test_google_not_configured_503`
  - `test_apple_valid_token_string_email_verified`
  - `test_apple_relay_email_allowed` (Review Focus 4)
  - `test_fake_token_accepted_in_fake_mode`
  - `test_fake_token_rejected_outside_fake_mode`
  - `test_social_signin_respects_blocklist`
- [ ] Step 2: FAIL.
- [ ] Step 3: Implement. Extract the post-verification "find or create user, grant welcome credit, create session, build `VerifyMagicLinkResponse`" code from `verify-code` into one helper used by all three.
- [ ] Step 4: Run `pytest backend/tests/test_social_signin.py backend/tests/ -k "login_code or social" -v`. Expect PASS, and the existing email-code tests still pass.
- [ ] Step 5: Commit `feat(auth): Google and Apple ID-token sign-in for the mobile app`.

### Task 5: Docs, full test run, ship
- [ ] Step 1: Update `docs/API.md` (four new endpoints and the `return_to`), `docs/TESTING.md` § Local stack (the new knobs, fake social tokens, handoff, the app-return bounce), and `docs/ARCHITECTURE.md` if it lists auth methods.
- [ ] Step 2: `make test 2>&1 | tail -n 500`. Everything passes.
- [ ] Step 3: Bump the version to 0.278.0, then follow the gen `/shipit` flow: coderabbit → PR → CI → merge → verify the deploy (`curl https://api.nomadkaraoke.com/api/health` version; `GET /api/users/credits/app-return?status=cancelled` returns 302 to `nomadkaraoke://`).
- [ ] Step 4: Google OAuth client IDs are public (not secrets). Default `GOOGLE_OAUTH_CLIENT_IDS` in `backend/config.py` to the real web client ID(s) the controller provides, so no Pulumi or env change is needed; the env var still overrides it. Record the clients in gen's infra docs (`docs/ARCHITECTURE.md` auth section or the infra doc gen uses).
