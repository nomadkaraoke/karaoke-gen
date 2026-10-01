# Admin "Re-render" for any completed job (v0.256.0)

## Why

Renderer fixes (first case: RTL/Hebrew rendering, v0.255.0) need already-delivered
videos regenerated. Edit sends the customer back through review and recycles the
brand code; the tenant "Re-render with current theme" (v0.253.0/#1091) only covers
unpublished tenant jobs and re-snapshots the theme. This adds an admin-only button
that rebuilds any completed job end to end with no review.

## What

- `POST /api/admin/jobs/{job_id}/rerender` (`require_admin`), body
  `{"notify_customer": false}`. See `docs/API.md`.
- Service: `backend/services/admin_rerender_service.py` (`AdminRerenderService`,
  `validate_admin_rerender`, `suppress_customer_notifications`).
- Frontend: admin-only "Re-render" button on completed job cards
  (`components/job/OutputLinks.tsx`) with a confirm dialog (explains: current
  renderer, no review, YouTube deleted + re-uploaded with a new URL, GDrive/Dropbox
  replaced, brand code kept) and an "Email the customer when done" checkbox (default
  off). Strings in `messages/*.json` (33 locales).

## Flow

1. Validate: `complete` (or `failed` with `state_data.admin_rerender`), outputs not
   deleted, not prep/finalise-only, no visibility change in progress, reviewed lyrics
   + instrumental selection present.
2. Firestore transaction claims `complete → lyrics_complete` with
   `regen_restore_status = review_complete` (shared `claim_for_rerender`, same as the
   theme re-render), sets the `admin_rerender` marker (`notify_customer`,
   `brand_code`, `previous_outputs`), clears the dead links (`youtube_url`,
   `dropbox_link`, `gdrive_files`, ...) but **not** `brand_code`, and drops
   `file_urls.screens` / `videos.with_vocals`. Style fields are untouched.
3. Deletes regenerated artifacts (screens, with_vocals, `finals/*.mov` — the encoder
   globs `**/*Title*.mov`) via the shared `delete_regenerated_artifacts`.
4. Deletes published outputs via the new shared
   `backend/services/published_outputs_cleanup.py` (also now used by Edit): YouTube,
   Dropbox (effective dist path, so private jobs hit the private folder), GDrive (with
   `cleanup_mirror=False` — same brand code, so the kjbox GCS mirror object is
   overwritten in place instead of disappearing for ~20 min). Results go to the
   marker, the timeline and `log_to_job`. Failures don't abort.
5. Triggers the screens worker → render → video worker (encode + distribute). Worker
   ids are generation-keyed, so the encoder cache can't return the old render.
6. Video worker: `rerender_brand_code()` now also reads `admin_rerender.brand_code` →
   `keep_brand_code` (no new allocation). On success it clears the marker and
   transitions to COMPLETE with `notify=not suppress_customer_notifications(job)`.

## Decisions

- **Existing style**, not current theme (tenant endpoint unchanged).
- **Delete up front, keep brand code** (Andrew). Distribution re-publishes under the
  same code; YouTube URL changes.
- **No customer notification by default.** `JobManager.transition_to_state(...,
  notify=False)` skips the completion email + push. The orchestrator also passes
  `notify_user=False` to a quota-deferred YouTube queue entry so the "your video is on
  YouTube" follow-up is skipped. Normal jobs and the tenant theme re-render always
  notify (covered by tests). The community voter fan-out (`notify_community_publish`)
  still runs: it's idempotent (voters already notified aren't re-emailed) and it
  updates the request's YouTube URL.
- **Trigger failure → `failed`** (marker kept) rather than restoring `complete`, since
  the published outputs may already be gone. `/retry` re-runs the admin re-render
  while screens are missing; after that the normal render/video retry ladder resumes
  it (marker → same brand code and notification choice).

## Caveats

- YouTube cost per re-render: delete (50) + duplicate search (100) + upload (1600) +
  thumbnail (50) units. With quota exhausted the upload is queued, so the track is off
  YouTube until the queue drains.
- If the up-front YouTube delete fails, the server-side upload's duplicate-by-title
  check deletes the old video before uploading; GDrive uploads replace same-named
  files; Dropbox uploads overwrite.
- `jobs_completed` on the user is incremented again on completion (same as Edit and
  the tenant re-render).
