# Requests Voting Board — Phase 2 Handoff (the automation)

**Cold-start doc for a fresh Claude session** picking up the requests voting board after Phase 1
shipped. Read `docs/REQUESTS-BOARD.md` first for the product overview, then this for what to build.

- **Phase 1 (SHIPPED, karaoke-gen v0.219.0, PR #975, 2026-09-02):** the public board itself — email
  magic-link sign-in, submit + auto-correct + dedupe, one-vote-per-day, ranked list, convert-to-gen
  CTA. Live at `requests.nomadkaraoke.com` / `gen.nomadkaraoke.com/en/requests`.
- **Phase 2 (THIS handoff, NOT built):** the daily automation that turns the top-voted request into a
  free, auto-generated, published karaoke video — and the trending-agent integration.

Backlog item: `BACKLOG.md` → "Daily free community track via public voting board" (engine).
Original spec (Andrew's verbatim prompt) is the source of truth; it's in that backlog item.

---

## Phase 1 recap — what exists to build on

**Backend**
- `backend/models/song_request.py` — `SongRequest` (fields incl. Phase-2 placeholders: `status`
  `open|queued|in_progress|published|rejected`, `source` `human|trending_agent`, `job_id`,
  `youtube_url`, `picked_at`), `Vote`, and API models.
- `backend/services/song_request_service.py` — `SongRequestService`: `submit_request`, `list_active`
  (ranked by `vote_count` desc), `list_published`, `cast_vote` (transactional), `get_daily_vote`,
  `get_request`. Collections `song_requests` / `song_request_votes`.
- `backend/api/routes/requests_board.py` — the public/authed board endpoints.
- Board sign-in plumbing in `backend/api/routes/users.py` + `backend/services/user_service.py`
  (`purpose="requests_board"`, `BOARD_MAX_SIGNUPS_PER_IP`, `POST /users/claim-welcome-credit`).

**Frontend:** `frontend/app/[locale]/requests/{page,client}.tsx`, `lib/api.ts` board methods,
`messages/en.json` `requests` namespace (+ 33 locales).

**Tests:** `backend/tests/test_requests_board_routes.py`, `test_song_request_logic.py`,
`test_requests_board_signin.py`, `test_board_signup_limit.py`, and
`backend/tests/emulator/test_song_request_service.py` (real Firestore transactions — run with the
emulator: `scripts/start-emulators.sh`).

## Phase 2 scope (Andrew's design decisions — DO honor these)

### 1. Daily picker — SOURCE-AGNOSTIC (this merges the "Autogen trending-karaoke agent" backlog item)
Andrew explicitly merged the standalone *Autogen trending-karaoke agent* into this system
(2026-09-02, verbatim):
> "the trending agent could just submit a request to the voting board whenever the voting board is
> empty, allowing us to essentially use the same system for the 'pick up the next highest-priority
> (either most-voted, or submitted by the trending-karaoke agent) track to make and make it, fully
> auto' part"

So build **one** daily job that:
- Picks the highest-priority **open** request (top net votes; tiebreak oldest-first — `list_active`
  already returns this order). Human votes and trending-agent submissions feed the **same queue**.
- **One free track per day, total** — the trending agent is only a *fallback source*: if the board
  is **empty** when the daily trigger fires, the trending agent submits a request (source=
  `trending_agent`) so there's always something to make; if the board already has requests, the
  trending agent does nothing that day. If the board is empty **and** no trending pick is available,
  nothing happens (per the original spec).
- Respect the standing rule **[don't make songs that already have good community versions]**
  (`feedback_job_creation_candidates_only` / KaraokeNerds) — the picker should skip / not surface
  songs that already exist. The trending agent must generate *candidates* the same careful way (see
  `scripts/karaoke-candidates/` in the workspace and the karaoke-decide `candidates` CLI).

### 2. Submit the pick as the requester, grant a free credit
When a request is picked: grant the requester one free credit (`user_service.add_credits(email,
1, reason="community_daily_pick")`), then create the job **owned by that user** (non-admin, so the
credit is actually consumed — admins bypass credit charging). Programmatic submit path:
`POST /api/audio-search/search` with `auto_download=true` (owner = caller), or build a `JobCreate`
directly (needs `theme_id` = `get_theme_service().get_default_theme_id()`). Set the request
`status=queued`/`in_progress` and store `job_id`. Auto-completion is handled by the existing
full-auto review pipeline (`project_gen_full_auto_review` — lyrics/instrumental auto-approve).

### 3. 24-hour ownership hand-off
If the owner doesn't complete the (lyrics) review within 24h, reassign the job to another voter and
email them. Repeat until someone completes it. Reuse `PATCH /api/admin/jobs/{id}` with `user_email`
(it auto-creates the account and logs the reassignment — but note it does **not** move/refund
credits, so handle credit transfer explicitly). "Another voter" = iterate the `song_request_votes`
docs pointing at this request (value +1), skipping those already tried. Track attempts on the
request doc.

### 4. Notify voters on publish
When the job is published to YouTube, email everyone who voted for it. Reuse
`backend/services/job_notification_service.py::send_youtube_upload_complete_email` /
`email_service` + `template_service`. Set request `status=published`, `youtube_url`. Trigger hook:
`backend/workers/youtube_queue_processor.py` (deferred upload) already sends the owner's completion
email — add the voter fan-out near there (gather voter emails from `song_request_votes`).

### 5. Add the board link to the YouTube description template
Once live, add `requests.nomadkaraoke.com` to the published-video description so every video drives
traffic to the board. Single source: `backend/config.py` `default_youtube_description` (flows into
both publish paths via `job.youtube_description_template`). Coordinate with the YouTube-descriptions
work Andrew is doing in a parallel session.

## Concurrency & idempotency (CRITICAL — the daily cron WILL double-fire otherwise)

The daily flow has multiple separate side effects (pick → grant credit → create job → advance
status → publish → email). A retried Scheduler delivery, an overlapping run, or a crash between
steps can double-grant credits, create duplicate jobs, or make **two** free tracks in a day. Design
for this from the start:

- **Claim the day atomically before doing anything.** The "board empty?" check is NOT a claim. Take a
  durable per-UTC-day lock first — e.g. a Firestore doc `daily_pick/{YYYY-MM-DD}` created with a
  transaction / `if_generation_match=0` (create-only); if it already exists, this run exits. Only the
  run that wins the claim proceeds to pick + trending-fallback. This is what actually enforces "one
  free track per day, total".
- **Transition the chosen request atomically.** Move the selected request `open → queued` inside a
  transaction (guard on current status) so two runners can't both grab the same request.
- **Make grant + submit idempotent.** Carry a durable idempotency key (e.g. `daily-pick-{date}` or
  `{request_id}`): record `credit_granted_for` on the request before/with the grant and check it so a
  retry can't re-grant; pass a deterministic key when creating the job and store `job_id` on the
  request so a retry finds the existing job instead of creating another. Define the recovery rule for
  each partial-failure point (claimed-but-no-credit, credit-but-no-job, job-but-no-status).
- Same idea for the **hand-off** (don't double-reassign / double-email on retry) and **publish
  emails** (mark voters-notified so a re-run doesn't email everyone twice).

`cast_vote`'s transactional pattern in `song_request_service.py` is the model to copy. (These points
were raised by CodeRabbit on this handoff — they're real; bake them in.)

## Reuse map (for Phase 2)
| Need | Reuse |
|---|---|
| Grant free credit | `user_service.add_credits(email, amount, reason=...)` |
| Submit job as a user | `POST /api/audio-search/search` (auto_download) / `JobManager.create_job` |
| Auto-complete review | full-auto pipeline (`auto_approval/*`, `project_gen_full_auto_review`) |
| Reassign owner | `PATCH /api/admin/jobs/{id}` `user_email` (does NOT move credits) |
| Voter / completion emails | `job_notification_service`, `email_service`, `template_service` |
| Daily trigger | Cloud Scheduler → an internal admin endpoint or Cloud Run Job (see how other crons are wired; NOTE `oauth_token` not `oidc` gotcha — `project_divebar_sync_vm_scheduler_fix`) |
| Candidate generation (trending) | `scripts/karaoke-candidates/`, karaoke-decide `candidates` CLI, KaraokeNerds check |

## Data model — already in place for Phase 2
`SongRequest.status` (advance open→queued→in_progress→published/rejected), `job_id`, `youtube_url`,
`picked_at`, `source`. `list_published` already surfaces `status=published` with `youtube_url` on the
board's "Recently made" section. No schema migration needed to start.

## Gotchas (learned in Phase 1 — will bite Phase 2 too)
- **`lib/api.ts` `apiFetch` does NOT auto-attach the bearer token** — callers must pass
  `getAuthHeaders()`.
- **Backend route tests:** don't capture `app`/`require_auth` at module import — several test modules
  reload `backend.main`/`backend.api.dependencies`, so the conftest auth override lands on a different
  object → 401s only in full-suite order. Import `app` in a fixture and override the router's own
  `require_auth` (see `test_requests_board_routes.py`).
- **Admin jobs bypass credit charging** — to actually consume the granted community credit, submit
  the job as the (non-admin) requester, not as admin.
- **`theme_id` is mandatory** in `create_job`.
- **i18n:** any new `en.json` string must be translated to all 33 locales
  (`python frontend/scripts/translate.py --messages-dir frontend/messages --target all`) or CI fails.
- **Firestore composite indexes:** Phase 1 deliberately avoided them (single-field queries + Python
  filter/sort). If Phase 2 adds `where(status)+order_by(vote_count)` etc., add the index to
  `infrastructure/modules/database.py` (Pulumi) — and `pulumi up` needs Andrew's write creds (ADC is
  read-only).

## Suggested build order
1. Daily picker (source-agnostic) + free-credit grant + submit-as-user, gated behind a kill-switch
   env + a `--dry-run` mode; **shadow-run first** (log what it *would* pick) before it actually
   creates jobs. Watch the candidates rule.
2. Voter publish-emails (self-contained, low-risk).
3. 24h ownership hand-off (most fiddly — needs attempt tracking + credit transfer).
4. Trending-agent fallback source (only fires when board empty).
5. Add board link to YouTube description template (coordinate with Andrew's descriptions session).

## Open questions for Andrew (ask before building)
- Exact daily trigger time / timezone (venue is US Eastern).
- Should downvoted-to-negative requests be auto-hidden/rejected, or always eligible?
- Trending-agent candidate source + how aggressive (max/day is 1 via the shared "one free/day" rule,
  but how many candidates to keep queued?).
- Hand-off: cap the number of re-assignment attempts before giving up on a track?

## Memory
`project_gen_requests_voting_board` (this initiative), `project_gen_full_auto_review` (auto-approve
the picker relies on), `feedback_job_creation_candidates_only`, `project_referral_system`
(magic-link/subdomain pattern), `reference_infra_access_capabilities`.
