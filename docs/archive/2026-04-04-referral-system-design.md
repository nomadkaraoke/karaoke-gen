# Referral Link System — Design Spec

**Date:** 2026-04-04
**Status:** Approved
**Scope:** karaoke-gen (backend + frontend + public-website)

## Overview

A referral system where any registered user can share a personalized link. Referred users get a time-limited discount on credit purchases. Referrers earn a cash kickback (percentage of actual revenue) paid out via Stripe Connect.

## Key Parameters

| Parameter | Default | Configurable? |
|-----------|---------|---------------|
| Discount for referred user | 10% | Per-link (admin) |
| Discount window | 30 days from signup | Per-link (admin) |
| Referrer kickback | 20% of amount charged | Per-link (admin) |
| Earning window | 1 year from referred signup | Per-link (admin) |
| Payout threshold | $20 | Global |
| Applies to | Credit purchases only | Fixed (excludes Made-for-You) |

## Data Model

### New collection: `referral_links`

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Unique, URL-safe. Auto-generated (8-char lowercase alphanumeric) or admin vanity (3-30 chars, alphanumeric + hyphens). |
| `owner_email` | string | Referrer's email |
| `display_name` | string | Shown on interstitial (defaults to user's name/email) |
| `custom_message` | string | Optional, max 200 chars. Shown on interstitial. |
| `discount_percent` | int | Default 10 |
| `kickback_percent` | int | Default 20 |
| `discount_duration_days` | int | Default 30 |
| `earning_duration_days` | int | Default 365 |
| `stripe_coupon_id` | string | Corresponding Stripe coupon for this discount % |
| `is_vanity` | bool | Whether admin-created vanity link |
| `enabled` | bool | Admin can disable (soft delete) |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `stats.clicks` | int | Interstitial page views |
| `stats.signups` | int | Users who signed up via this link |
| `stats.purchases` | int | Purchases attributed to this link |
| `stats.total_earned_cents` | int | Total kickback earned |

### New collection: `referral_earnings`

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Document ID |
| `referrer_email` | string | |
| `referred_email` | string | |
| `referral_code` | string | The code used |
| `stripe_session_id` | string | Payment that triggered this earning |
| `purchase_amount_cents` | int | Amount charged (after discount) |
| `earning_amount_cents` | int | Kickback amount |
| `status` | string | `pending` / `paid` / `refunded` |
| `created_at` | datetime | |
| `paid_at` | datetime | When included in a payout |
| `payout_id` | string | Links to payout record |

### New collection: `referral_payouts`

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Document ID |
| `referrer_email` | string | |
| `stripe_transfer_id` | string | Stripe Connect transfer ID |
| `amount_cents` | int | Total payout amount |
| `earnings_included` | list[string] | Earning doc IDs included |
| `status` | string | `processing` / `completed` / `failed` |
| `created_at` | datetime | |

### Additions to existing `users` collection

| Field | Type | Description |
|-------|------|-------------|
| `referral_code` | string | User's auto-generated referral code |
| `referred_by_code` | string | Code they signed up with (if any) |
| `referred_at` | datetime | When referral was attributed |
| `referral_discount_expires_at` | datetime | `referred_at` + discount duration |
| `stripe_connect_account_id` | string | For receiving payouts |

## Referral Link Formats

- **Primary:** `nomadkaraoke.com/r/CODE` — dedicated route, shows interstitial, redirects to landing page
- **Secondary:** `nomadkaraoke.com/?ref=CODE` — query param on any page, silent cookie set
- If both present, `/r/CODE` takes priority
- Cookie: 30-day expiry + localStorage backup

## Interstitial Page (`/r/CODE`)

A branded page shown when someone clicks a referral link:

- Referrer's `display_name` and `custom_message` (if set)
- Discount details: "Enjoy 10% off all credit purchases for 30 days"
- CTA button: "Get Started" → redirects to landing page
- Sets referral cookie before redirect
- Increments `stats.clicks` on the referral link doc

## Referral Attribution Flow

1. User visits `/r/CODE` or any page with `?ref=CODE`
2. Code stored in cookie (30-day expiry) + localStorage
3. User signs up or logs in
4. On first authentication, backend checks for referral code:
   - Validates code exists and is enabled
   - Validates code isn't the user's own (self-referral block)
   - Sets `referred_by_code`, `referred_at`, `referral_discount_expires_at` on user doc
   - Increments `stats.signups` on referral link doc
5. Attribution is one-time — first valid code wins, cannot be changed

## Checkout Flow (Discounted)

1. User initiates credit purchase
2. Backend checks `referral_discount_expires_at > now`
3. If active discount: creates Stripe checkout session with coupon for configured discount %
4. Discount visible on Stripe checkout page
5. After successful payment (webhook):
   a. Normal credit grant flow (unchanged)
   b. Check if user has `referred_by_code` and within earning window (1 year)
   c. If yes: calculate kickback = `purchase_amount_cents * kickback_percent / 100`
   d. Create `referral_earnings` doc with status `pending`
   e. Increment `stats.purchases` and `stats.total_earned_cents` on referral link doc
   f. Check referrer's total unpaid earnings — if >= $20 and Connect account exists, trigger payout

**Stripe coupon strategy:** One coupon per unique discount percentage (e.g., `referral-10pct`, `referral-15pct`). Reused across all links with the same rate. Applied at checkout session level.

## Stripe Connect & Payouts

### Onboarding

1. User visits Referrals dashboard, clicks "Connect your bank account"
2. Backend creates Stripe Connect Express account, returns onboarding link
3. User completes Stripe's onboarding (bank details, identity, tax info)
4. Backend stores `stripe_connect_account_id` on user doc
5. Stripe handles compliance (1099s for US referrers earning $600+/year)

### Payout Lifecycle

1. Earnings accumulate as `pending` docs in `referral_earnings`
2. When total unpaid earnings >= $20 AND referrer has Connect account:
   - Create Stripe Transfer to Connect account
   - Create `referral_payouts` doc with status `processing`
   - Mark included earnings as `paid`
3. If >= $20 but no Connect account: earnings accumulate, user sees prompt to connect
4. Stripe webhook confirms transfer → update payout status to `completed`

### Refund Handling

- If referred purchase is refunded before payout: earning doc marked `refunded`, deducted from unpaid balance
- If refunded after payout: refunded amount deducted from future earnings (negative balance carries forward)
- If referrer never earns enough to offset: written off (acceptable at this scale)

## Admin Features

### Referral Links Management

- List all referral links with stats
- Create vanity links: custom code, assign to user, override discount/kickback %
- Edit any link's parameters (discount %, kickback %, display name, message, duration)
- Disable/enable links (soft delete)
- Code validation: alphanumeric + hyphens, 3-30 chars, no collisions with reserved words (`admin`, `app`, `api`, `r`, `pricing`, etc.)

### Activity & Analytics

- Per-link stats: clicks, signups, conversions, revenue generated, total paid out
- Global referral activity feed: recent signups, purchases, earnings
- Payout oversight: pending/completed payouts, manually trigger or hold payouts

### Abuse Controls

- Disable any referral link
- Hold payouts for specific referrers (fraud review flag)
- Self-referral blocked at attribution time
- Leverage existing fingerprint/IP infrastructure: flag if referred user shares `device_fingerprint` or `signup_ip` with referrer
- Admin alerts for suspicious patterns (e.g., many signups with no purchases, shared fingerprints)

## Frontend: Referrer Experience

**New "Referrals" section in app dashboard:**

- Referral link with copy button
- Edit display name and custom message
- Stats: signups, purchases, total earned, pending balance
- Stripe Connect onboarding CTA (if not connected)
- Payout history (once connected)
- Lightweight — not a full affiliate dashboard

## Frontend: Referred User Experience

- **On signup with referral attribution:** Toast/banner — "You've got 10% off all credit purchases for 30 days!"
- **During discount window:** Discount badge on Buy Credits dialog, discount applied automatically at checkout
- **After discount expires:** Normal pricing, no visual changes
- No mention of referrer earning money

## Scope Exclusions

- Referral discounts do NOT apply to Made-for-You ($50) orders
- No multi-level/MLM referrals — only direct referrals earn
- No referral link for white-label tenants (default Nomad Karaoke portal only, for now)
- No public-facing leaderboard or referral tiers
