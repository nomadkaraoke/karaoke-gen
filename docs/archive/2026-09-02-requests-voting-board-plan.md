# Requests Voting Board — Implementation Plan (Phase 1)

**Backlog item:** "Daily free community track via public voting board (requests.nomadkaraoke.com)"
`[engine score:7 heavy]` (WIP, session 35dc230a).
**Date:** 2026-09-02
**Worktree:** `karaoke-gen-requests-voting-board` / branch `feat/sess-20260902-2041-requests-voting-board`

## Why now / phasing decision (Andrew, 2026-09-02)

Andrew is rewriting every YouTube video description in a parallel session to point at a
free "vote for the next track" page instead of the old Fiverr gig + pricing. He needs a **live,
linkable public URL** ASAP. So we ship in two phases:

- **Phase 1 (this session): the live public voting board.** A real, linkable
  `requests.nomadkaraoke.com` where anyone can sign in with an email magic link, submit a
  song request (auto-corrected artist/title), and cast one vote per day. HN-style ranked list.
  This is what unblocks the YouTube-description work.
- **Phase 2 (follow-up PR): the automation.** Daily auto-picker (source-agnostic), free-credit
  grant to the picked requester, 24h ownership-handoff to the next voter, publish-notification
  emails to all voters, and integration of the "Autogen trending-karaoke agent" as a fallback
  request source when the board is empty (max 1 free track/day total). Also: add the board link
  to the YouTube description template.

## Design decisions (Andrew-confirmed)

1. **Host:** new routes inside the existing gen Next.js app (`app/[locale]/requests/`), fronted
   at `requests.nomadkaraoke.com` via Cloudflare — same reuse pattern as the referral interstitial.
2. **Voting rule:** *one vote total per person per calendar day* (up or down, on a single
   request; changeable tomorrow). Strong anti-gaming, concentrates the daily signal.
3. **Board sign-in = identity only:** no welcome credit, and it **bypasses the per-IP new-signup
   limit** so a whole venue on shared WiFi can sign in. (The existing `MAX_SIGNUPS_PER_IP=2/24h`
   guard is tied to welcome-credit abuse; skipping the credit grant sidesteps it.)
4. **Convert-to-gen CTA:** prominent "Don't want to wait? Make it yourself now →" on the board
   and in the post-submission state, linking into gen's normal create flow, where the standard
   welcome credit is granted the usual way (once per account). This is the funnel.
5. **Trending-agent integration (Phase 2):** the daily picker is source-agnostic — human votes
   and trending-agent submissions feed the SAME queue; trending agent submits only when the board
   is empty; one free track/day total.

## Reuse map (all confirmed in code)

- **Magic-link auth:** `POST /api/users/auth/magic-link` → `GET /api/users/auth/verify` → opaque
  session token; `require_auth` dep → `auth_result.user_email`; users in `gen_users` (keyed by
  lowercased email). Frontend `useAuth().sendMagicLink` + `/auth/verify` page + localStorage
  `karaoke_access_token`.
- **Artist/title auto-correct:** `judge_match(artist, title, stage="full")`
  (`backend/services/match_judge/service.py:50`) / `POST /api/catalog/match-judge` — Vertex Gemini,
  returns `canonical_artist/title`, `kind`, `confident`, `alternatives`.
- **Credits (Phase 2):** `user_service.add_credits(email, amount, reason, ...)`.
- **Auto-submit (Phase 2):** `POST /api/audio-search/search` (auto_download); owner = caller email.
- **Ownership handoff (Phase 2):** `PATCH /api/admin/jobs/{id}` `user_email` (auto-creates account).
- **YouTube description (Phase 2 link):** `config.py:273` `default_youtube_description`.
- **Voter emails (Phase 2):** `job_notification_service.send_youtube_upload_complete_email`.

## Phase 1 — build list

### Backend
1. **Models** `backend/models/song_request.py`:
   - `SongRequest`: `id`, `artist`, `title` (canonical), `artist_raw`, `title_raw`, `dedupe_key`
     (normalized), `submitted_by` (email), `source` ("human" | "trending_agent"), `status`
     ("open" | "queued" | "in_progress" | "published" | "rejected"), `vote_count` (denormalized net),
     `created_at`, `updated_at`, plus Phase-2 placeholders (`job_id`, `youtube_url`, `picked_at`).
   - `Vote`: doc id `{request_id}__{email}`-independent; fields `request_id`, `voter_email`,
     `value` (+1/-1), `voted_date` (UTC `YYYY-MM-DD`), `created_at`.
   - API models: `SubmitRequestBody{artist,title}`, `VoteBody{direction}`, public response shapes
     (never leak voter emails; expose `vote_count`, `status`, canonical artist/title, and
     per-viewer `your_vote` / `voted_today`).
2. **Service** `backend/services/song_request_service.py`:
   - `submit_request(user_email, artist, title)` → `judge_match` canonicalize → normalized
     `dedupe_key` → if an open dup exists, treat as an upvote/return existing; else create doc,
     submitter auto-upvotes (counts as their daily vote). Per-person submit rate-limit (e.g. 5/day).
   - `list_requests(viewer_email=None, include_done=False)` → ranked (vote_count desc, created_at
     asc tiebreak); annotate viewer's vote state + whether they've used today's vote.
   - `cast_vote(user_email, request_id, direction)` → enforce **one vote/day/person total**
     (query votes by `voter_email`+`voted_date`); transactional upsert of the vote + `vote_count`.
   - `daily_vote_status(user_email)` helper.
3. **Router** `backend/api/routes/requests_board.py` (prefix `/requests-board`, mounted in `main.py`):
   - `GET /requests` (public/optional-auth) — list.
   - `POST /requests` (require_auth) — submit.
   - `POST /requests/{id}/vote` (require_auth) — vote.
   - `GET /me` (require_auth) — today's vote status.
4. **Board sign-in context:** add `purpose: Optional[str]` to `SendMagicLinkRequest`; when
   `purpose == "requests_board"`, `send_magic_link`/`create_magic_link` skip the welcome-credit
   grant + skip the per-IP signup limit, and the verify link carries `&next=/<locale>/requests`
   so the user lands back on the board.
5. **Firestore indexes:** `song_requests` (status + vote_count desc), votes (voter_email +
   voted_date), votes (request_id + voter_email).

### Frontend
6. `app/[locale]/requests/page.tsx` (Suspense) + `client.tsx` (`'use client'`), modeled on
   `app/[locale]/r/`. Publicly viewable; voting/submit require sign-in (reuse `AuthDialog` /
   `useAuth().sendMagicLink` with `purpose:"requests_board"`). Submission form previews the
   canonical artist/title via `matchJudge()` before submit. Ranked list w/ vote buttons + counts +
   status badges (open / being made / published-with-YouTube-link). Prominent convert-to-gen CTA.
7. API client methods in `frontend/lib/api.ts` (list/submit/vote/me).
8. i18n: new `requests` namespace in `frontend/messages/en.json` → `scripts/translate.py --target all`
   (33 locales; CI-enforced).
9. Verify-page return-to-board: honor a `next` param / stored redirect so board sign-in returns to
   `/requests` (default flow returns to `/app`).

### Infra
10. Cloudflare (via API — access per workspace CLAUDE.md): proxied DNS record for
    `requests.nomadkaraoke.com` + a Dynamic Redirect Rule → `https://gen.nomadkaraoke.com/<locale>/requests`;
    add `requests` to `nonTenantSubdomains` in `frontend/functions/[[path]].ts`. Record the change
    in the infra doc. (Fallback: attach as a Pages custom domain if we later want the vanity host to
    persist in the address bar.)

### Tests
- Backend unit tests: submit (canonicalize + dedupe), vote (daily-limit, transaction, count),
  ranking, public response never leaks emails, board sign-in skips credit/IP-limit.
- Frontend: typecheck + a component test for the board; i18n completeness (CI).

## Out of scope for Phase 1 (→ Phase 2)
Daily auto-picker, free-credit grant on pick, 24h ownership-handoff, voter publish emails,
trending-agent-as-source, YouTube-description link, "already has a community version" badge
(KaraokeNerds lookup — ties into the standing "don't make songs with existing versions" rule).
