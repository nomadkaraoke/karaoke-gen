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
4. Deletes published outputs (only for destinations it will re-publish to — see
   review fix 6) via the new shared
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

## Review fixes (second commit)

1. **Stale deferred YouTube upload.** The re-render cancels the job's
   `youtube_upload_queue` entry (`cancel_upload`: `queued`/`failed` → `cancelled`;
   a `processing` entry is reported, not stopped). The queue processor defers any
   entry while the job has an active admin re-render and isn't `complete` (the
   finals are being rebuilt), and suppresses the follow-up + voter emails while a
   silent re-render's marker is present, even for legacy entries without
   `notify_user`.
2. **Retry is admin-only** while an active `admin_rerender` marker exists: a
   customer's `POST /api/jobs/{id}/retry` gets 403 `jobs.adminRerenderRetryAdminOnly`
   ("contact support"), for every retry branch, not just the re-run one.
3. **Marker scoped to its run.** The marker records the job's `review_token` at
   claim; `active_admin_rerender()` ignores it once the token changes (any trip back
   through review mints a new one). `suppress_customer_notifications`,
   `rerender_brand_code`, kept outputs and retry eligibility all go through it. The
   admin reset, admin restart, admin delete-outputs, Edit and private→public
   visibility flows also delete the marker outright.
4. **Legacy video worker path** drops `admin_rerender`/`theme_rerender` from the
   state_data map it rewrites and applies the same notification/kept-output rules.
5. **No stuck LYRICS_COMPLETE.** Everything after the claim (audit log, artifact
   deletes, output deletes, queue cancel, screens trigger) is wrapped: any exception
   marks the job `failed` (`error_details.stage = "admin_rerender"`) with the marker
   kept, and the endpoint returns 500 "retry it".
6. **Only delete what will be re-published.** `plan_republish()` mirrors the video
   worker: YouTube needs `enable_youtube_upload` on a non-private job plus configured
   credentials; Dropbox needs effective `dropbox_path` + `brand_prefix`; GDrive needs
   effective `gdrive_folder_id`. Outputs for other destinations are left in place:
   `cleanup_results[dest] = {"status": "kept", reason}`, a warning in the response /
   marker / timeline / job log, their state_data link isn't cleared, and the video
   worker keeps that link (`kept_outputs`) instead of overwriting it with `None`.
7. **Discord + community voters.** The orchestrator's Discord "new video" post is
   skipped when `notify_customer` is false. `notify_community_publish(...,
   notify_voters=False)` still marks the request published with the new URL but
   emails no voters.
8. **Edit's Dropbox folder name changed.** Edit now uses the shared
   `dropbox_folder_path()`, which sanitises artist/title like the uploader does
   (`sanitize_filename`). Before, Edit built the raw `"{brand} - {Artist} - {Title}"`
   and silently missed folders for names with special characters (`/`, `?`, `"`,
   curly quotes, ...). Deliberate fix; covered by a test.
9. **No blocking I/O on the event loop.** The publish plan (YouTube credential
   lookup), the Firestore claim transaction and all post-claim GCS/YouTube/Dropbox/
   GDrive work run via `asyncio.to_thread`; deletions still complete before the
   screens worker is triggered (so before distribution).

## Second review pass

1. **Admin restart** folds the marker deletion into the restart's own atomic
   update, so a rejected restart (e.g. 400 on `preserve_audio_stems`) keeps it.
2. **Community reconcile.** `notify_community_publish(notify_voters=False)` calls
   `mark_voter_fanout_suppressed` (`voters_notified=True` + `voter_fanout_suppressed`
   audit flag), so `reconcile_community_publishes` pass 2 doesn't email voters later.
   Genuinely-unnotified normal publishes are still retried.
3. **In-flight deferred upload vs claim.** After a queued upload finishes, the
   processor re-reads the job. If an admin re-render claimed it meanwhile it never
   writes `youtube_url` or emails. If the re-render re-publishes YouTube
   (`marker.republish_youtube`), the just-uploaded old-finals video is deleted and
   the entry cancelled (safest: no stale public video, and the re-render uploads
   the new one). Otherwise the upload is kept as the job's YouTube output via
   `marker.kept_outputs.youtube_url`, so completion keeps the link.
4. **Marker cleared atomically with COMPLETE.** `transition_to_state(...,
   extra_updates={"state_data.admin_rerender": DELETE_FIELD})` writes it in the
   same Firestore update as the status; a failed transition keeps the marker.
5. **Completion counter.** Admin re-render completions pass
   `count_completion=False`, so `users.total_jobs_completed` (which gates feedback
   prompts) isn't inflated. Edit and the tenant theme re-render still increment on
   their re-completion (unchanged; arguably also double counting, left as is).
6. **Queue starvation.** The processor fetches 100 queued entries and attempts at
   most 20 uploads, so deferred entries can't fill the page. Only jobs actively in
   the pipeline are deferred; for a failed/cancelled admin re-render the entry is
   cancelled when the re-render re-publishes YouTube (resuming it re-queues),
   otherwise kept queued (it's the original run's owed upload). Job reads use one
   `JobManager` via `asyncio.to_thread`.
7. **Deferred upload only cancelled when YouTube is re-published.** Otherwise the
   queue entry and `youtube_upload_queued` are kept and a warning is emitted. A
   YouTube credential-check exception plans "don't touch YouTube" with a
   "couldn't verify YouTube credentials" warning.
8. **Legacy (KaraokeFinalise) path** gets `discord_webhook_url=None` on a quiet
   admin re-render. Brand code: both paths distribute via
   `_handle_native_distribution`, which already applies `rerender_brand_code` (tested).
9. **Admin delete-outputs** now uses the shared cleanup helpers (sanitised Dropbox
   folder name, like Edit / re-render); mirror cleanup unchanged.
10. **Frontend:** fixed the orphaned `regenerateScreens` JSDoc; `warnings` added to
    `AdminRerenderResponse` and shown in a toast after starting (33 locales).
11. **Claim uses the active marker** (a stale one can't leak brand code / history /
    retry flag), and a **CANCELLED** admin re-render can be resumed by the admin
    endpoint and `/retry`, like FAILED.

## Follow-ups

- Two other copies of published-output deletion remain and should move onto
  `published_outputs_cleanup.py` (admin delete-outputs was migrated in the second
  review pass): `backend/api/routes/jobs.py` (~L2440-2545), and
  `backend/services/visibility_change_service.py` `_delete_public_outputs` /
  `_delete_distributed_outputs` (~L280-410).
