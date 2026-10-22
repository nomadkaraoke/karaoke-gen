# YouTube comment auto-reply → requests board (plan, 2026-10-22)

Backlog: FROGS #6 "YouTube channel → gen funnel". Spec (Andrew, verbatim):

> we could probably also solve that by building some automation to detect youtube comments asking for songs and auto-reply with a link to the requests board

## Andrew's decisions (2026-10-22)

- **Go-live:** "build confidence in what it would do by running it locally against past comments from the last 6 months, once every action the bot would've taken is what i would want ongoing, then go-live in prod".
- **Board:** pre-add the requested song to the board and link straight to it.
- **Reply pitch:** "Board only - let's focus on getting this process to convert anonymous youtubers into gen request users with a real linked email address, then we can rely on the requests board web UI, the post-delivery email, later follow-up emails etc. to try and convert a user like that into a paid gen user". So the reply has no gen or paid link.
- **Description:** move a one-line make-your-own call to action above YouTube's "...more" fold.

## Baseline

- The description already carries UTM-tagged links to the board and to gen (`utm_campaign=requests` / `make_cta`), but both sit below the fold.
- Since UTM attribution went live on 2026-10-15: **1 YouTube signup** out of 15 attributed signups (kjbox 8, chatgpt.com 4). The channel has 3.3K subs, 1,748 videos and 2.17M views.

## Design

### 1. Decision core: `backend/services/youtube_comment_bot.py` (pure, testable)

`decide(thread, context) -> Decision`, where Decision is one of `skip` (with a reason), `reply_board`, or `reply_existing_video`. It is shared by the local replay and the prod worker, so the replay is exactly what prod will do.

- **Skip without an LLM call:**
  - top-level comment written by the channel itself;
  - a thread the channel has already replied to (Andrew answered by hand);
  - a comment we have already processed (Firestore record);
  - a comment older than the cut-off.
- **Classifier:** Gemini Flash through `gemini_client.get_genai_client()` (never Vertex). It returns structured JSON: `is_song_request`, `songs: [{artist, title}]` (max 3), `language`, and `confidence`. The prompt is strict: only clear "please make or do X" requests count. Praise, lyric quibbles, questions about our process, and "can you do a version with backing vocals" do not.
  - Below the confidence threshold we skip with reason `low_confidence`. These are listed separately in the replay report so Andrew can tune the threshold.
- **Per song:**
  - **Already on our channel:** reply with a link to that video. A channel index of upload titles is built with `youtube_backfill.classify()`, keyed by the board's `_dedupe_key`.
  - **Already published from the board:** same, using the `youtube_url` on the request.
  - **Otherwise:** find or create the board request and reply with a deep link to it.
- **Reply text:** fixed English templates, short and friendly, with no sales pitch. For non-English comments, Gemini translates the template and the URLs stay intact; the replay shows those translations.
  - Board link: `https://requests.nomadkaraoke.com/?request=<id>&utm_source=youtube&utm_medium=comment_reply&utm_campaign=song_request`.

### 2. Requests board changes

- **New request source:** `RequestSource` gains `"youtube_comment"`.
  - `submitted_by` is set to a sentinel (`youtube-comment@nomadkaraoke.com`), and the request starts at 0 votes.
  - New fields: `youtube_comment_id`, `youtube_video_id`, `youtube_author` (display name only, for audit).
- **Claiming:**
  - The first real user to up-vote a sentinel-owned request becomes its `submitted_by`. This is a transactional, one-time switch.
  - The daily picker and the 24h hand-off skip sentinel-owned requests until they're claimed. That way no free credit ever goes to the sentinel, and the commenter has a reason to sign in.
- **Frontend deep link:** on `requests/client.tsx`, `?request=<id>` scrolls to that request and highlights it. A small banner says (in all 33 locales) "Requested on YouTube — sign in with your email to upvote it and we'll email you when it's made".
  - The `request` param survives the magic-link round trip.
  - UTM params are already captured by `frontend/lib/attribution.ts`.

### 3. Prod worker (built dark)

- **Route:** `POST /api/internal/youtube-comments/run` (admin token), mirroring `youtube-backfill/run`. Supports `?dry_run=true` and `&since=...`.
- **Fetching:** reads new threads with `commentThreads.list(allThreadsRelatedToChannelId, order=time)` at 1 unit per page of 100, stopping at the high-water mark.
- **State:**
  - Firestore `youtube_comment_bot/state` holds the high-water mark.
  - `youtube_comment_bot_actions/{commentId}` holds one record per comment: decision, reply id, request ids. This makes runs idempotent, and the comment-id doc is created with create-only semantics before posting, so a retry can't double-reply.
- **Posting:** `comments.insert` with the parent thread id, 50 units each. Quota is coordinated with `YouTubeQuotaService`, and there's a per-run cap (default 20).
- **Kill switch:** `YOUTUBE_COMMENT_AUTOREPLY_ENABLED`, default **false**. While off, the worker can still run in dry-run mode.
- **Discord:** each posted reply goes to **#noise** as a one-liner. Only real failures (auth broken, quota errors) go to **#alerts**, aggregated to one per run.
- **Scheduler:** Cloud Scheduler `youtube-comment-autoreply-hourly` in Pulumi. Andrew runs `pulumi up` before merge.
- **OAuth:** posting comments needs the `youtube.force-ssl` scope, and the current token only has `youtube`.
  - Add `youtube.force-ssl` to `CredentialManager.YOUTUBE_SCOPES` and the upload/backfill scope lists. The new scope also covers uploads.
  - **Andrew re-authorizes once** through the existing YouTube device flow (`POST /api/auth/youtube/device`). Uploads keep working on the old token until then.
  - The worker checks the token's scopes and skips posting, with one #alerts message, if `force-ssl` is missing.

### 4. Local replay (the go-live gate)

- **Script:** `scripts/youtube-comments/replay.py --since 2026-04-22`. It reads up to 6 months of real channel comment threads and runs the **same** `decide()` with Gemini.
- **No side effects:** board creation is simulated in memory, with dedupe across the run, and nothing is posted.
- **Output:** an HTML report for Andrew, one row per comment. Each row shows:
  - the video;
  - the comment;
  - the decision and reason;
  - the parsed songs;
  - the exact reply text and link;
  - **what Andrew actually replied by hand, if he did.** This is the strongest check that the bot behaves like him.
- **Summary counts:** requests found, replies by type, skips by reason, and low-confidence cases.
- **Iteration:** we refine the prompt, templates and threshold until Andrew approves every action. Then we enable it in prod: re-auth, `pulumi up`, set the env var.
- **Pending comments:** comments already answered by hand count as matches for validation, and the bot never replies to them. Only unanswered requests from the 6 months are candidates for a one-time backfill reply, and that is Andrew's call after the replay; it's off by default (`since` = go-live time).

### 5. Description above the fold

Rewrite the opening of `default_youtube_description`:

```
🎤 Karaoke version of "{title}" by {artist}: original audio, lead vocals removed, hand-synced lyrics.
▶️ Make a karaoke video of ANY song: https://nomadkaraoke.com/r/youtube?utm_source=youtube&utm_medium=description&utm_campaign=make_cta_top
```

The rest of the template stays as it is. The template fingerprint changes, so the daily backfill re-drains about 1.6k videos (~11 days at 150/day). Tempo and translation notices still go first on the few videos that have them. The new `utm_campaign=make_cta_top` tells us whether the above-the-fold placement converts.

## Testing (per docs/TESTING.md)

- **Unit:**
  - `decide()` skip rules, song routing (existing video / published / board), the template + translation fallback, and classifier JSON parsing including malformed output (Gemini mocked);
  - channel-index matching;
  - sentinel claim on first up-vote (transactional; second voter doesn't steal it);
  - picker and hand-off skip unclaimed `youtube_comment` requests;
  - worker idempotency (create-only action doc, retry doesn't double-post), kill switch, dry run, quota cap, missing-scope path;
  - description render (above-the-fold line present, notices still go first).
- **Integration:** worker against the Firestore emulator with a fake YouTube client: a page of threads goes in, and the right actions, board requests and replies come out.
- **Frontend:** Jest for the deep-link highlight + banner. A Playwright regression test (mocked API) for `?request=<id>` scrolling to the request and keeping the param through sign-in. A production E2E test that opens the board with a real request id after deploy.
- **Replay** is the behavioural acceptance test, and Andrew signs it off.

## Rollout

1. PR 1 (this branch): core, board changes, deep link, dark worker, Pulumi scheduler, description change, replay script, tests, docs (`docs/REQUESTS-BOARD.md` § YouTube comments). Merge with the flag off.
2. Replay → Andrew reviews the report → iterate (small follow-up PRs if prompt or templates change).
3. Andrew: YouTube device re-auth with `force-ssl` + `pulumi up` → set `YOUTUBE_COMMENT_AUTOREPLY_ENABLED=true` → watch #noise for the first replies.
4. Measure: signups with `utm_medium=comment_reply` and `make_cta_top` after about 2 weeks.

## Open questions (default chosen, flag if wrong)

- **Replies to replies** (someone asking in a sub-thread): ignored for v1, top-level comments only.
- **Requests for songs that already have a non-Nomad community version:** still add them to the board. The picker's existing KaraokeNerds check handles them later.
