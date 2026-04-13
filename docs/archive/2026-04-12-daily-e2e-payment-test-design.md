# Daily E2E Payment + Happy Path Test

**Date:** 2026-04-12
**Status:** Design

## Motivation

On April 10, a Stripe SDK upgrade caused all payment webhooks to 500 for ~5 days. Users paid real money but never received credits or confirmation emails. The existing `stripe-credit-purchase.spec.ts` test uses a simulated test-webhook endpoint — a completely different code path from real Stripe Checkout — and would never have caught this.

This design adds a daily E2E test that exercises the **real payment flow** (Stripe Checkout with a real credit card), then uses the purchased credit to run the existing happy path job creation test.

## What We're Building

A single GitHub Actions workflow (`e2e-daily.yml`) that replaces the current `e2e-happy-path.yml`. It runs two sequential Playwright test stages daily at 6 AM UTC:

1. **Stage 1 — Credit Purchase** (~3-5 min): Sign up a fresh test user with referral code `e2etest70` (70% discount), purchase 1 credit ($3.00) via real Stripe Checkout, verify credits land and email arrives.
2. **Stage 2 — Happy Path** (~20-30 min): Same test user spends the purchased credit to create a karaoke job through the full generation pipeline.

If Stage 1 fails, Stage 2 is skipped. Both stages send Discord + email alerts on failure.

## Architecture

### Workflow: `.github/workflows/e2e-daily.yml`

Replaces `e2e-happy-path.yml`. Same schedule (daily 6 AM UTC), same manual trigger options, but with two sequential test runs.

```yaml
jobs:
  e2e-credit-purchase:
    # Stage 1: Buy credits via real Stripe Checkout
    # Outputs: test user email + session token for Stage 2

  e2e-happy-path:
    needs: e2e-credit-purchase
    # Stage 2: Use purchased credit for full job generation
    # Uses token from Stage 1 (skip signup)
```

The two stages are separate GitHub Actions jobs (not separate Playwright test files run in one job) so that:
- Stage 2 is cleanly skipped when Stage 1 fails
- Each stage has independent artifact uploads and failure reporting
- Stage 1's session token is passed to Stage 2 via job outputs

### Test File: `frontend/e2e/production/credit-purchase-real.spec.ts`

New Playwright test for Stage 1. Exercises the real user payment flow:

```
1. Navigate to gen.nomadkaraoke.com/?ref=e2etest70
2. Sign up via magic link (testmail.app email capture)
3. After auth, verify referral discount badge shows 70% off
4. Open Buy Credits dialog
5. Select 1-credit package — verify discounted price ($3.00)
6. Click Checkout → redirect to checkout.stripe.com
7. Fill card details (from env: E2E_STRIPE_CARD_NUMBER, E2E_STRIPE_CARD_EXPIRY, E2E_STRIPE_CARD_CVC)
8. Submit payment → redirect to /payment/success
9. Verify "Payment Successful" page with 1 credit balance
10. Verify confirmation email via testmail.app
11. Verify credit balance via admin API = 1
12. Output session token for Stage 2
```

### Test File: `frontend/e2e/production/happy-path-real-user.spec.ts` (modified)

The existing happy path test is modified to accept a pre-authenticated session token (it already supports `E2E_TEST_TOKEN`). No changes needed to the core test logic — Stage 2 just passes the token from Stage 1.

### Notifications

Both stages report failures via:

- **Email**: SendGrid to `andrew@nomadkaraoke.com` (existing pattern)
- **Discord**: POST to `DISCORD_ALERT_WEBHOOK_URL` secret with an embed showing which stage failed, the workflow URL, and a summary

Discord notification format:
```
🚨 E2E Daily Test FAILED — Stage 1: Credit Purchase
Run #123 | 2026-04-12 06:05 UTC
Details: https://github.com/nomadkaraoke/karaoke-gen/actions/runs/XXX
```

On success, a brief confirmation is posted to Discord (no email on success — keep inbox clean).

### Existing Workflow: `e2e-happy-path.yml`

Deleted and replaced by `e2e-daily.yml`. The new workflow is a strict superset — it runs the same happy path test with the addition of the credit purchase stage before it.

The manual `workflow_dispatch` trigger is preserved with the same inputs (`debug_mode`, `browser`, `use_test_token`), plus a new `skip_payment` input that skips Stage 1 for quick happy-path-only iterations.

## Stripe Checkout Automation

The test fills a real credit card on Stripe's hosted checkout page (`checkout.stripe.com`). This is the most fragile part of the test since Stripe can change their UI.

### Selector Strategy

Stripe Checkout uses well-known iframe-based card input fields. The test:

1. Waits for the Stripe Checkout page to load (URL matches `checkout.stripe.com`)
2. Locates card input fields by their element IDs within Stripe's iframes (`cardNumber`, `cardExpiry`, `cardCvc`)
3. Falls back to `[name="cardnumber"]`, `[name="exp-date"]`, `[name="cvc"]` attribute selectors
4. Uses role-based selectors for the Pay button (`getByRole('button', { name: /pay/i })`)
5. All interactions have 30s timeouts with screenshots on failure

### Card Details Storage

Stored as GitHub Actions secrets:

| Secret | Example | Notes |
|--------|---------|-------|
| `E2E_STRIPE_CARD_NUMBER` | `4111111111111111` | Real virtual card from Capital One |
| `E2E_STRIPE_CARD_EXPIRY` | `12/28` | MM/YY format |
| `E2E_STRIPE_CARD_CVC` | `123` | 3-digit CVC |
| `E2E_STRIPE_CARDHOLDER_NAME` | `E2E Test` | Name on card (if Stripe requires it) |

### Stripe Checkout Page Handling

Stripe Checkout is a full page redirect (not an embedded iframe). The test:

1. Clicks "Checkout" in BuyCreditsDialog
2. Waits for navigation to `checkout.stripe.com`
3. Stripe's card fields are embedded in iframes within the checkout page — the test must locate the correct iframe, then fill fields within it
4. After filling all fields and clicking Pay, waits for redirect back to `gen.nomadkaraoke.com/*/payment/success`

### Resilience

- **Retries**: Playwright production config already has `retries: 2`
- **Screenshots**: Captured on every step and on failure
- **Video**: Full video recording for debugging
- **Timeout**: 5-minute timeout for the entire Stripe interaction (generous for page loads)

## Referral Discount Verification

The test user signs up via `?ref=e2etest70` which attributes them to the `e2etest70` referral link with 70% discount. Verifications:

1. After auth, the BuyCreditsDialog shows the "Active Referral Discount" badge with "70% off"
2. The 1-credit package shows $3.00 (not $10.00)
3. The Stripe Checkout total is $3.00

The referral link `e2etest70` must be pre-configured in production with:
- `discount_percent`: 70
- `kickback_percent`: 0 (no payouts for test purchases)
- `duration_days`: 36500 (effectively permanent — 100 years)

## New GitHub Actions Secrets

| Secret | Source | Purpose |
|--------|--------|---------|
| `E2E_STRIPE_CARD_NUMBER` | User-provided | Virtual credit card number |
| `E2E_STRIPE_CARD_EXPIRY` | User-provided | Card expiry MM/YY |
| `E2E_STRIPE_CARD_CVC` | User-provided | Card CVC |
| `E2E_STRIPE_CARDHOLDER_NAME` | User-provided | Cardholder name |
| `DISCORD_ALERT_WEBHOOK_URL` | GCP `discord-alert-webhook` | Discord failure alerts |

Existing secrets reused: `TESTMAIL_API_KEY`, `TESTMAIL_NAMESPACE`, `E2E_ADMIN_TOKEN`, `E2E_BYPASS_KEY`, `SENDGRID_API_KEY`.

## Cost

- 1 credit package at 70% off = **$3.00/day** (~$90/month)
- Stripe processing fees: ~$0.60/transaction ($3.00 * 2.9% + $0.30)
- Total: **~$3.60/day, ~$108/month**

## Files Changed

| File | Change |
|------|--------|
| `frontend/e2e/production/credit-purchase-real.spec.ts` | **New** — Real Stripe Checkout payment test |
| `frontend/e2e/helpers/stripe-checkout.ts` | **New** — Helper for filling Stripe Checkout page |
| `.github/workflows/e2e-daily.yml` | **New** — Combined payment + happy path workflow |
| `.github/workflows/e2e-happy-path.yml` | **Delete** — Replaced by e2e-daily.yml |

## Out of Scope

- Testing other credit packages (3, 5, 10) — 1 credit is sufficient to verify the flow
- Testing promotion codes (separate from referral coupons)
- Refund testing
- Made-for-you payment flow (has its own test)
- Modifying the existing `stripe-credit-purchase.spec.ts` — that test still has value as a fast API-level check

## Risks

1. **Stripe Checkout UI changes**: Most likely failure mode. Mitigated by Discord alerts, video recordings, and resilient selectors. Fix turnaround: update selectors, typically <1 hour.
2. **Card decline**: Virtual card could expire or hit limits. Mitigated by Discord alert — replace card and update secret.
3. **Referral code expiry**: The `e2etest70` link must have a very long duration. Set to 100 years.
4. **Test user accumulates credits**: Harmless — credits pile up on disposable testmail.app accounts. Each day creates a new test user.
