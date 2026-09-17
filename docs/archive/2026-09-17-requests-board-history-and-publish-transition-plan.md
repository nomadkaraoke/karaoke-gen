# Requests Board — Admin History View, Public "Recently Made" List, & Publish-Transition Fix

Date: 2026-09-17
Branch: `feat/sess-20260916-2353-requests-board-history`

## Context / discovery

Andrew asked whether we can see past community requests and whether our first real user
successfully got a video. Investigation (Firestore + `email_log`):

- **Only 2 requests ever.** Both stuck at `status == "in_progress"`:
  - Admin go-live test request — Sep 3 (submitted by `admin@`, job since deleted).
  - A real community user's request — **Sep 15** (first genuine board user; PII kept out of this
    public repo — see the private session record / Firestore for specifics).
- **First real user was fully served.** The job completed, rendered all finals, and published to
  YouTube (brand assigned, GDrive + Dropbox). `email_log` confirms magic-link → `action_reminder`
  (lyrics review) → `job_completion` emails all sent to the requester; he emailed a thank-you. ✅
  (He was notified via the **standard job-owner** path, not the community fan-out.)

### Root-cause bug found
The community publish transition (`song_request_service.mark_published` + voter fan-out in
`_notify_community_voters`) is invoked **only** inside `backend/workers/youtube_queue_processor.py`
(the quota-managed `youtube_upload_queue` path). The first real user's job got its YouTube URL via
the **direct distribution path** during finalization (`processing_metadata.distribution.youtube_video_url`),
which never enqueues to `youtube_upload_queue`. Result: `_notify_community_voters` never ran, so the
request stayed `in_progress`, `youtube_url` stayed empty, `voters_notified` stayed `False`.

Impact today is minor (he was the only voter, and got the standard completion email anyway), but:
- The board's public **"Recently made"** section (`frontend/app/[locale]/requests/client.tsx:317`,
  driven by `list_published()` = `status == "published"`) will **never populate** while this bug
  exists — so it looks empty/broken.
- Any request with *other* up-voters would silently fail to send the "your track is live" fan-out.

## Scope (agreed with Andrew)

1. **Admin history view** of ALL community requests (his explicit ask).
2. **Public "recently made" list** on the board — already coded, needs the publish transition fixed
   + a backfill to light up.
3. **Fix the publish-transition bug** (prerequisite for #2; correctness for all future picks).

## Plan

### Part A — Publish transition fix (backend)
- Extract the "job just got a YouTube URL → advance its community request" logic into a single
  reusable helper (e.g. `song_request_service.on_job_published(job_id, youtube_url)` or a shared
  `_notify_community_voters` callable) that: looks up the request by `state_data.community_request_id`,
  calls `mark_published`, and fans out voter emails (retry-safe via existing `notified_voters` /
  `voters_notified` flags).
- Call it from **both** publish paths:
  - existing `youtube_queue_processor` site (line ~94) — unchanged behavior;
  - the **direct distribution finalize path** — add the hook where `distribution.youtube_video_url`
    is set. Locate that write and call the shared helper there (idempotent, so double-firing is safe).
- Keep it idempotent so re-runs / both-paths-firing don't double-email.

### Part B — Backfill the first real request
- Run the reconcile endpoint (Part below) in prod to mark the stuck request `published` with its
  YouTube URL. (No voter email needed — the sole voter is the owner, already notified.) This makes
  it appear in the board's "Recently made" list.

### Part C — Admin history view (backend + frontend)
- **Backend:** `GET /api/admin/community-requests` (admin-auth) returning ALL `song_requests`,
  every status, with: artist/title, status, `submitted_by`, `owner_email`, `vote_count`, `job_id`,
  `youtube_url`, `created_at`, `picked_at`, `review_state`. Reuse existing admin auth + response
  model patterns from `admin.py` (see `community-reviews`).
- **Frontend:** new admin page `frontend/app/admin/community-requests/` (English-only admin surface;
  no `[locale]` counterpart) — a table with status badges, requester, link to the job review page
  (`/app/jobs#/{job_id}/review`), and YouTube link. Add nav entry alongside `community-reviews`.

### Testing
- Backend unit: shared helper fires from the distribution path; idempotency; request lookup by
  `state_data.community_request_id`; admin endpoint returns all statuses + is admin-gated.
- Frontend: admin page renders rows, links resolve.
- Follow `docs/TESTING.md`; run `make test`.

## Open questions
- Exact location of the direct-distribution YouTube-URL write (need to grep the finalize/distribute
  worker) — confirm during implementation.
- Whether to also surface the admin history inside the existing `/admin/community-reviews` page vs a
  new page. Leaning new page for clarity.
