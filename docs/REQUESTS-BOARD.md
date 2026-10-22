# Requests Voting Board (`requests.nomadkaraoke.com`)

**Status:** Phase 1 + Phase 2 **LIVE in production** (Phase 1 v0.219.0 #975; Phase 2 automation
v0.220.0 #977 — **enabled** `COMMUNITY_DAILY_PICK_ENABLED=true`, daily picker fires noon US Eastern).
Existing-community-version checks (submission-time soft warning + pick-time review) shipped v0.222.0.
Trending-agent fallback source is the one deferred piece. Design:
`docs/archive/2026-09-03-requests-board-phase2-plan.md`.

This doc is the single self-contained explanation of what the requests system is, what users
experience, and how it works. Point marketing / YouTube-description work here.

---

## The idea (the vision)

**Give away one free, fully-made karaoke video every day — to whichever song the public most
wants — and use that to turn curious viewers into Nomad Karaoke users.**

A public, Hacker-News-style board where anyone can:
- **request** a song (just artist + title), and
- **vote** for the songs they want made.

Every day, the most-voted song is generated for free and published to YouTube. It's a low-friction,
zero-cost thing for a viewer to do (voting on a song you want costs nothing), which makes it a great
top-of-funnel call-to-action: far more people will click "vote for a free karaoke track" than "buy
credits". Once someone has an account and an email on file, we can gently convert a share of them
into paying [gen.nomadkaraoke.com](https://gen.nomadkaraoke.com) customers over time.

## Where it lives

- **Public URL to share / link:** **https://requests.nomadkaraoke.com**
  (serves the board on that host, so the short URL stays in the address bar and can be copied
  on mobile; put it in YouTube descriptions/replies, socials, etc.)
- Also directly reachable at `https://gen.nomadkaraoke.com/en/requests` (any of the 33 locales:
  `/es/requests`, `/de/requests`, …).

## What a user experiences (Phase 1 — live today)

1. **Open the board.** Anyone can view it without signing in — they see the ranked list of
   requested songs and how many votes each has.
2. **Sign in with just an email.** No password. They enter their email, get a magic-link, click it,
   and they're in. (Signing in here is *identity only* — it does **not** spend a karaoke credit.)
3. **Request a song.** They type an artist and title. We auto-correct/canonicalize it with the same
   AI the main generator uses (e.g. "beatles" → "The Beatles", "Maduk" → "Maduk feat. Kye Sones"),
   show them "we tidied that to …", and add it to the board. If someone already requested that same
   song, their submission just up-votes the existing entry instead of creating a duplicate.
4. **Vote — once per day.** Each person gets **one vote per calendar day** (up or down, on a single
   song). They can move it to a different song later that day, or come back tomorrow for a fresh
   vote. This keeps the signal strong and hard to game. Submitting a song counts as that day's vote
   for it.
5. **See the ranking.** The board is sorted by net votes — the top song is what's next in line.
6. **"Don't want to wait? Make it yourself now →"** A prominent call-to-action lets an impatient
   user jump straight into the full generator and make their video immediately — and *that* path
   grants them the standard free welcome credit (once per account, the normal way).

### Phase 2 — the automation (built, deployed dark)
The daily pick-and-generate, the free-credit grant to the winning requester, the 24-hour
"if you don't finish it, it passes to the next voter" hand-off, and the "your song is live on
YouTube!" fan-out to every voter are all **built and merged**, gated behind the
`COMMUNITY_DAILY_PICK_ENABLED` kill-switch (default **off**). While off, the daily Scheduler still
runs but only **shadow-logs** the pick it *would* make (also reachable manually with
`?dry_run=true`) — so behavior can be verified before going live. Flip the env var on to start
donating one free track per day. The only outstanding piece is the **trending-agent fallback
source** (auto-submit a candidate when the board is empty), deliberately deferred to a follow-up.

**How the automation works:**
- **Daily picker** (`community_daily_pick.py`, Scheduler noon US Eastern) claims the UTC day with a
  create-only lock (`daily_community_pick/{YYYY-MM-DD}` → **one free track/day, total**), picks the
  top open request with net votes ≥ 0 (source-agnostic; skips community-rejected), grants the
  requester a free credit, and submits the job **as that user** (so it's owned by them and the credit
  is consumed) via the same search → auto-select → download path the web flow uses.
- **24h hand-off** (`community_handoff.py`, hourly) reassigns a track to the next up-voter if the
  owner hasn't completed their review within 24h — up to 5 voters, then it parks the track
  (`stalled`). Community jobs are excluded from the normal stale-review auto-cancel.
- **Publish fan-out** hooks the YouTube upload queue: when a community pick goes live it's marked
  `published` and every up-voter (except the owner, already emailed) gets a "track you voted for is
  live" email.
- Every step is idempotent (durable per-day lock phase + per-request guard flags) so a retried
  Scheduler delivery or a mid-run crash can't double-grant credits or make two tracks in a day.

**Avoiding duplicates of existing community versions (v0.222.0):** we don't want to spend a free
track (or waste voters' time) on a song that already has a good community karaoke version online, so
the same KaraokeNerds check the normal job-submission flow uses runs at two points:
- **At submission time (soft):** as someone types an artist/title on the board, we surface any
  existing community versions in a dismissible banner (with YouTube links) — non-blocking, they can
  still request it, but many will just watch the existing one.
- **At pick time (review):** when the daily picker reaches a request, it KaraokeNerds-checks it first.
  If a community version now exists (it may not have when the request was submitted), the pick is
  **held for review** instead of auto-made, Andrew is emailed, and the picker **skips to the next
  clean request** so a fresh free track still ships that day. Andrew resolves held picks at
  **`/admin/community-reviews`**: *Make ours anyway* (creates the job), *Reject* (declines and emails
  every up-voter a link to the existing version), or *Keep on board* (leaves it votable, snoozing
  re-review for 30 days). Held requests stay visible/votable on the board; they just aren't auto-made.

## How it works (technical, brief)

- **Frontend:** a route in the gen Next.js app (`frontend/app/[locale]/requests/`), fully translated
  to 33 locales. `requests.nomadkaraoke.com` serves it via the `requests-host` Worker (see
  `docs/runbooks/requests-subdomain-cloudflare.md`).
- **Auth:** reuses gen's passwordless magic-link system. Board sign-in sends
  `purpose="requests_board"`, which creates an identity **without** granting a welcome credit and
  with a higher per-IP signup cap (so a whole venue on shared WiFi can sign in).
- **Auto-correct:** reuses `match_judge` (Gemini) — the same artist/title canonicalizer the
  job-submission flow uses.
- **Storage:** two Firestore collections — `song_requests` (one doc per song, with a denormalized
  `vote_count`) and `song_request_votes` (one doc per person per day, id `{email}__{YYYY-MM-DD}`,
  which is what structurally enforces one-vote-per-day).
- **API:** `GET /api/requests-board/requests` (public), `POST /api/requests-board/requests` (submit),
  `POST /api/requests-board/requests/{id}/vote`, `GET /api/requests-board/me`, and
  `POST /api/users/claim-welcome-credit` (the convert-to-gen credit). Full reference in `docs/API.md`.

## Guidance for marketing / YouTube descriptions

- **Link to use:** `https://requests.nomadkaraoke.com`
- **The hook:** it's *free* and *low-effort* — "Vote for the next free karaoke track" / "Request the
  song you want made — we make the most-voted one free every day." Emphasise there's nothing to buy
  and no password.
- **Honest framing while Phase 2 is pending:** the board is live and collecting requests/votes now;
  the fully-automatic "one free track every day" cadence is rolling out. It's safe to say "request &
  vote for the songs you want us to make" today; be a little careful about promising a *guaranteed
  daily* free track until Phase 2 ships (until then, picks are made as we go). If in doubt, frame it
  as "vote for what we make next."
- **Conversion:** the board itself nudges impatient users into the paid generator ("Make it yourself
  now"), so linking the board also feeds gen signups.

## YouTube comment auto-reply (song requests → board)

**Status:** built 2026-10-22 (backlog Frog #6). Andrew signed off the behaviour on a
6-month local replay before go-live.

Viewers often comment "could you make <song>?" on our videos. Every hour a bot reads new
top-level comments on the channel and, for a clear song request:

- **Song already on the channel:** replies with that video's link ("Already made "X" by Y 🥳 <link>").
- **Otherwise:** adds the song to the board, or finds the existing request, and replies with a
  link straight to it: `https://requests.nomadkaraoke.com/?request=<id>&utm_source=youtube&utm_medium=comment_reply&utm_campaign=song_request`.
- **Vague asks** ("more Johnnie please") get a generic board link.
- **Non-English comments** get a translated reply. If a URL doesn't survive the translation,
  the reply falls back to English.

The reply promotes only the board, not gen (Andrew's call). The goal is getting an
anonymous YouTuber to sign in with an email; follow-up emails do the gen conversion.

**Skipped:**
- the channel's own comments;
- threads the channel already replied to (Andrew answering by hand);
- comments from before go-live;
- anything the Gemini Flash classifier isn't confident about (< 0.8).

**Bot-added requests:**
- `source="youtube_comment"` and `submitted_by="youtube-comment@nomadkaraoke.com"`, with no votes.
- The **first real up-voter claims the request**, which sets `submitted_by` to them. Until
  then the daily picker skips it, so a free credit never goes to the placeholder address.
- The deep link highlights the request. Signing in from it uses purpose
  `requests_board:<id>`, so the magic link returns to `/requests?request=<id>&upvote=1`
  and casts the upvote on arrival. That's normally how the commenter claims it.

**Pieces:**
- Decision core: `backend/services/youtube_comment_bot.py`, shared by the replay and the worker.
- Worker: `backend/workers/youtube_comment_bot_worker.py`, called through
  `POST /api/internal/youtube-comments/run` (`?dry_run=true` logs what it would post and writes nothing).
- Scheduler: Cloud Scheduler `youtube-comment-autoreply-hourly` (:17 past each hour).
- Idempotency: one Firestore doc per comment in `youtube_comment_bot_actions/{comment_id}`,
  created before posting, so nothing can get a double reply. State is in
  `youtube_comment_bot/state` (`go_live_at`, `high_water`) and the channel index is cached in
  `youtube_comment_bot/channel_index`.
- Config:
  - `YOUTUBE_COMMENT_AUTOREPLY_ENABLED`: kill switch.
  - `YOUTUBE_COMMENT_AUTOREPLY_START`: ISO date, used on the first run only. It enabled a
    one-time backfill of unanswered requests.
  - `..._MAX_REPLIES` (20 per run) and `..._MIN_CONFIDENCE` (0.8).
- Notifications: each reply posts a one-liner to Discord **#noise**. Missing credentials,
  missing scope, YouTube errors and failed replies go to **#alerts**, throttled to 6h.
- Long runs: all comment pages are fetched up front and reads retry (`num_retries`).
  Each post uses a freshly built client and is never retried. Without this, the slow
  Gemini work between calls (background tasks are CPU-throttled) idled the shared
  connection until Google dropped it (`BrokenPipeError`, first prod run 2026-10-22).
- Quota: `commentThreads.list` costs 1 unit per page and each reply costs 50, recorded through
  `YouTubeQuotaService` as `comment-reply:<comment_id>`.

**OAuth (separate from the upload token):**
- Listing a channel's comments and posting replies both need the `youtube.force-ssl` scope.
  Google refuses that scope in the device flow that the upload token uses
  (`invalid_scope: Invalid device flow scope`).
- So the bot has its own **Desktop** OAuth client, `youtube-comment-bot` (Google Auth
  Platform → Clients, project nomadkaraoke).
- Its token is in Secret Manager `youtube-comment-bot-credentials`, as JSON with
  `token, refresh_token, token_uri, client_id, client_secret, scopes`.
- To re-mint it:
  1. Run an `InstalledAppFlow` (`google_auth_oauthlib`) with that client's JSON and the scopes
     `youtube` + `youtube.force-ssl`.
  2. Approve as the **Nomad Karaoke Youtube** brand account. Our app shows the expected
     "unverified app" warning: Advanced → Go to Nomad Karaoke.
  3. Add a new secret version.

**Replay (go-live gate / tuning):**
```bash
python scripts/youtube-comments/replay.py --since 2026-04-22 --credentials-file creds.json --out replay.html
```
Read-only. The report has one row per comment: the decision, parsed songs, exact reply, and
the channel's own reply if there was one. On 2026-10-22 it covered 265 threads with 33
requests. The bot caught all 29 that Andrew had answered by hand, with no false positives.

## Related docs
- Product/build plan: `docs/archive/2026-09-02-requests-voting-board-plan.md`
- **Phase 2 handoff (start here to build the automation):**
  `docs/archive/2026-09-03-requests-voting-board-phase2-handoff.md`
- Subdomain wiring runbook: `docs/runbooks/requests-subdomain-cloudflare.md`
- API reference: `docs/API.md` (§ Requests Voting Board)
