# Admin: Locale/Language/Country Visibility + Email History

**Branch:** `feat/sess-20260822-2340-admin-locale-emails`
**Date:** 2026-08-23

Two independent admin-facing features so support/intervention work can (a) know what
language/country a user is using the product in, and (b) see the exact emails ever sent
to a user, rendered as they appeared in the inbox with full delivery metadata.

Decisions (confirmed with user):
- **Email history:** Postmark live query (rich metadata, ~45d retention) **+** persist every
  future send to Firestore for permanent history beyond 45 days. Merge both sources.
- **Locale:** Full capture — add a real per-job `locale` (any of 33 langs from the actual
  `Accept-Language` header), surface the user's UI language, and derive country from IP.

---

## Feature A — Locale / language / country

### Backend

1. **New helper** `get_full_locale_from_request(request)` in `backend/i18n.py`
   - Returns the first language tag from `Accept-Language` **without** narrowing to en/es/de
     (e.g. `pt`, `ja`, `zh`). Keep the existing `get_locale_from_request` untouched (emails
     still need en/es/de).

2. **Job model** `backend/models/job.py`
   - Add `locale: Optional[str] = None` to `Job` (~L239-531) and `JobCreate` (~L534-655).

3. **Capture on job creation** (populate `locale` from `get_full_locale_from_request`):
   - URL jobs: `backend/api/routes/jobs.py` (~L79-197)
   - File upload: `backend/api/routes/file_upload.py`
   - Audio search: `backend/api/routes/audio_search.py`
   - Made-for-you: capture at checkout (`backend/api/routes/users.py` create_made_for_you_checkout,
     ~L801) → put `locale` in Stripe session metadata → read in the `checkout.session.completed`
     webhook (~L1313) when the job is created.

4. **Dashboard summary projection**: add `'locale'` to `SUMMARY_FIELD_PATHS`
   (`backend/services/firestore_service.py` ~L200-220) so it appears in `list_jobs_summary`.
   (Top-level field → only this constant, not `_SUMMARY_STATE_DATA_KEYS`.)

5. **User UI locale (full value)** `backend/models/user.py`
   - Add `ui_locale: Optional[str] = None` (keep existing `locale` en/es/de for email rendering).
   - Populate in `verify_magic_link` (`users.py` ~L441-446) from the full Accept-Language, and
     update it opportunistically on job creation (cheap `update_user` when it changes).

6. **Surface in admin responses**
   - `UserDetailResponse` (`users.py` ~L1753-1780): add `locale`, `ui_locale`.
   - `get_user_detail` return (~L1954): include them; add `locale` to each `recent_jobs` entry (~L1919-1925).

### Frontend

7. **`LocaleBadge` component** `frontend/components/admin/locale-badge.tsx`
   - Input: a locale string. Renders a small pill: flag emoji + human language name.
   - Language name via `Intl.DisplayNames(['en'], { type: 'language' })`; flag via a
     locale→region map (reuse `countryCodeToFlag` from `frontend/lib/ip-geolocation.ts:78`).
   - Graceful fallback for unknown/empty locale (`🌐 —`).

8. **Types**: add `locale` to `Job` (`frontend/lib/api.ts` ~L105-152) and `locale`/`ui_locale`
   to `AdminUserDetail` (~L2256-2301) + the recent-jobs entry type.

9. **Placements**
   - Jobs table (`frontend/app/admin/jobs/page.tsx` ~L2142-2211): `LocaleBadge` in the row
     (language only — cheap, no IP lookup per row).
   - Job detail info grid (`jobs/page.tsx` ~L1113-1179): Language cell (`LocaleBadge`) +
     Country via existing `<IpInfo ip={creation_ip} />`.
   - User detail (`frontend/app/admin/users/detail/page.tsx`): `LocaleBadge` in the header
     (~L342-348) / Account Info card. Country already shown via `<IpInfo>`.

Note: keep IP→country lookups to the single-record detail views (cached in the
`ip_geolocation` Firestore collection). The jobs *list* shows language only, to avoid N
geolocation calls per page.

---

## Feature B — Email history ("emails sent to this user")

### Backend

10. **Persist every send** — single chokepoint in `EmailService`
    (`backend/services/email_service.py`). Route all sends through one private
    `_log_and_send(...)` that calls `self.provider.send_email(...)`, captures the Postmark
    `MessageID` from the response, then best-effort writes a Firestore `email_log` doc:
    `{ recipient, cc, bcc, from, subject, html_content, text_content, email_type,
    message_stream, postmark_message_id, created_at }`.
    - Mechanical: replace the ~15 `self.provider.send_email(` call sites with `self._log_and_send(`.
    - Add optional `email_type` kwarg to each `send_*` for categorization (default inferred/None).
    - Wrap the Firestore write in try/except — **logging must never break a send**.
    - Capture `MessageID`: `PostmarkEmailProvider.send_email` must return it (change return to
      include message id, or add an out-param) — currently it discards the response body.

11. **`PostmarkAdminService`** `backend/services/postmark_admin_service.py`
    (model on `stripe_admin_service.py`):
    - `get_user_email_history(email)` → `GET https://api.postmarkapp.com/messages/outbound?recipient=<email>&count=...`
      using `X-Postmark-Server-Token` (env `POSTMARK_SERVER_TOKEN`). Merge with Firestore
      `email_log` docs for that recipient; dedupe by `postmark_message_id`; sort by sent-at desc.
    - `get_email_detail(message_id)` → `GET /messages/outbound/{id}/details` (full `HtmlBody`,
      subject, status, opens/clicks, bounce). Fallback to the Firestore `email_log` doc for
      messages older than Postmark's 45-day retention.

12. **Admin endpoints** `backend/api/routes/admin.py` (pattern: `get_user_payments` ~L2969,
    guarded by `Depends(require_admin)`):
    - `GET /admin/users/{email}/emails` → summary list.
    - `GET /admin/emails/{message_id}` → full detail (HTML + metadata); `?source=log` for
      Firestore-only records.

### Frontend

13. **API client** `frontend/lib/api.ts`: `adminApi.getUserEmails(email)` +
    `adminApi.getEmailDetail(id, source?)`, plus `UserEmailSummary` / `EmailDetail` interfaces.

14. **User detail page**: new **"Emails sent to this user"** `<Card>` (rows: subject,
    type badge, sent-at, delivery-status badge). Row click → **`EmailPreviewDialog`**:
    - Left/main: `<iframe srcDoc={html} sandbox>` so the email renders exactly as in the inbox
      (isolated styles), inside `<DialogContent>` + `<ScrollArea>`.
    - Side panel: To / From / CC / BCC / Subject / sent-at / status / open & click counts /
      bounce reason.

---

## Testing

- **Backend unit**: `get_full_locale_from_request` parsing; job `locale` persisted on each
  creation path; `_log_and_send` writes an `email_log` doc (Postmark mocked) and never raises
  when Firestore fails; `PostmarkAdminService` list/detail with `requests` mocked incl. the
  45-day fallback; admin endpoints with the service mocked + `require_admin` auth.
- **Frontend unit (jest)**: `LocaleBadge` rendering (known/unknown locale); email list + modal
  render; iframe `srcDoc` wiring.
- **Prod E2E (Playwright)**: optional — user-detail page shows the emails card + opens a
  preview for a known recipient.

## Infra / ops

- No new secrets (`postmark-server-token` already wired into the API service).
- New Firestore collection `email_log`. Add a composite index (recipient asc, created_at desc)
  if the merge query needs it — note in PR; Firestore will surface the index hint on first query.
- Bump `tool.poetry.version` in `pyproject.toml`.

## Risks / notes

- Postmark `MessageID` isn't captured today — must thread it through the provider return.
- Not all `send_*` funnel through `send_email` today (e.g. `send_magic_link` calls
  `self.provider.send_email` directly), which is exactly why the `_log_and_send` chokepoint
  replaces the provider call sites rather than wrapping `send_email`.
- `User.locale` stays en/es/de (emails depend on it); the true UI language lives in the new
  `ui_locale`.
