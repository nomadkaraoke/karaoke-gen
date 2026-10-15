# Free-to-paid conversion emails

Added 2026-10 for BACKLOG Frog #2 ("plug the gen revenue leaks"). Funnel context: 44% of signups finish a free video, but only 6% ever pay, and buyers decide fast (median about 5h after signup). Also, 45 users opened lyrics review and let the job expire at 48h.

| Email | When | Stream | Gate |
|---|---|---|---|
| **E1** "Your karaoke video is done. What's next?" + `CONVERSION_OFFER_PERCENT`% off any credit pack for `CONVERSION_OFFER_HOURS` | `CONVERSION_E1_DELAY_HOURS` (2h) after a never-paid consumer's **first** video completes | Postmark `broadcast` | `CONVERSION_EMAILS_ENABLED` |
| **E2** "Your N% off ends in N hours" | inside the last `CONVERSION_E2_HOURS_BEFORE_EXPIRY` (8h) of the offer, only if still unpaid | Postmark `broadcast` | `CONVERSION_EMAILS_ENABLED` |
| **R2** "N hours left to finish your karaoke video" | 42h into lyrics review (expiry is 48h; the existing reminder fires at 24h) | `outbound` (transactional) | `REVIEW_LAST_CHANCE_ENABLED` |

All of these are **off by default** and are switched on via Cloud Run env once Andrew approves the copy.

## How it works
- `backend/services/conversion_emails.py`:
  - `on_job_completed` runs from `JobManager` right after `total_jobs_completed` is incremented. It sets `conversion_e1_due_at` plus `first_video_artist`/`first_video_title` on eligible users. Excluded: tenant, Made For You and test accounts, anyone who has paid, and anyone who isn't on their first completion.
  - `process_conversion_emails` runs hourly at the end of the `/api/internal/process-stale-reviews` Cloud Scheduler job. It sends E1 and E2 and is idempotent per user (`conversion_e1_sent_at`, `conversion_e2_sent_at`). A due time older than 24h is skipped (`conversion_e1_skipped=true`), so turning the flag on later never mails a backlog.
- Sending E1 grants `promo_discount_percent` / `promo_discount_expires_at`. `POST /api/users/credits/checkout` applies Stripe coupon `conversion-offer-{pct}pct` when it beats any active referral discount. The buy dialog shows a "Your N% off is applied at checkout" banner (`promo_discount_*` on `UserPublic`). The E1/E2 button links to `/app?buy=1`, which opens the buy dialog.
- **Broadcast stream:** Postmark-managed unsubscribe (`UnsubscribeHandlingType: Postmark`) appends the unsubscribe link and suppresses opted-out recipients; a suppressed send fails with 406 and isn't retried. The CAN-SPAM postal address comes from `COMMERCIAL_EMAIL_POSTAL_ADDRESS`, which defaults to the address published on nomadkaraoke.com.
- Copy lives in `backend/translations/*.json` under `emails.conversion.*` and `emails.reviewLastChance.*` (33 locales).

## Measuring (30 days, per D4 of the 2026-10-14 business review)
- Sends: Firestore `email_log` with `email_type` = `conversion_offer`, `conversion_offer_reminder`, `review_last_chance`.
- Clicks and signups: links carry `utm_source=email&utm_medium=email&utm_campaign=conv_e1|conv_e2|review_last_chance`.
- Purchases: Stripe coupon `conversion-offer-{pct}pct` redemptions. Users with `conversion_e1_sent_at` and `total_spent > 0`.
- Reviews rescued: jobs with `state_data.last_chance_reminder_sent` that then completed.

## Turning it on
Add `CONVERSION_EMAILS_ENABLED=true,REVIEW_LAST_CHANCE_ENABLED=true` (and optionally `CONVERSION_OFFER_PERCENT=NN`) to the backend `--set-env-vars` list in `.github/workflows/ci.yml` (the "Deploy to Cloud Run" step) and ship it as a PR. The next deploy applies it. Roll back the same way.
