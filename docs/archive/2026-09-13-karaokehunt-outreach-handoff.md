# KaraokeHunt decommission + user outreach — session handoff (2026-09-13)

> **SESSION 3 UPDATE (2026-09-14)** — canonicalization v2 DONE (`phase1c_recanonicalize.py`
> + `outreach_out/canonical_v2.json`): resolved InputURL YouTube titles via oEmbed
> (`inputurl_titles.json`), judged all 60 non-confident songs, collapsed 10 duplicates,
> excluded 2 test requesters. Corrected names unlocked big torrents (Early Mornin' 189
> seeders, KAYTRANADA 10% 209, BOOMPALA, Luther Vandross). **FINAL BUCKETS
> (`review_summary_v3.md` / `actions_v3.json`): 87 requesters · 27 community (searched
> songs; +20 phase-1 links) · 65 torrent-submit · 29 spotify/youtube-make · 7 no-match ·
> 10 dropped.**
> **SESSION 3b (2026-09-15)**: the KN 429 bug was fixed by Andrew's dedicated session —
> see workspace `docs/KARAOKENERDS-DATA-ACCESS.md`; **HARD RULE: never scrape
> karaokenerds.com** — use the nightly exports (BigQuery / GCS / divebar-lookup CF /
> the now-fixed `/api/bulk/availability`). All outreach community verdicts were
> re-verified against the authoritative export + fuzzy divebar-lookup (3 more community
> found via title variants incl. Little Simz "Selfish" = our own NOMAD track; Youngblood
> community hit is an Acoustic variant so we generate the original).
> **DECIDED (Andrew)**: brand-only karaoke versions → GENERATE ANYWAY (annotations
> informational). **FINAL: 87 requesters · 29 community (searched; +20 phase-1) ·
> 64 torrent-submit · 28 spotify/youtube-make · 7 no-match · 10 dropped.**

> **SESSION 2 UPDATE (2026-09-13, later the same day)** — the §4 Segment-A pipeline is
> BUILT and the full run is DONE (searched 2026-09-13 ~07:00 UTC — sessions expire in
> 7 days). **RESULTS: 89 requesters · 20 community (14 + 6 found after typo-fix, 51/139
> titles canonicalized) · 28 CONFIDENT auto-submit · 105 no-match (55 of them NEAR
> MISSES) · 0 errors.** NEXT STEP: **Andrew reviews `outreach_out/review_packet_v2.md`**,
> flips `"approved": true` rows (+ optional `"manual_approve": true` on near-miss songs)
> in `actions_v2.json`, then `phase2_execute.py --execute`; emails go out afterwards as
> Gmail drafts in ~10-batches (see EMAIL POLICY below).
> - `scripts/karaokehunt_outreach/phase1b_audio_search.py` — match-judge canonicalization
>   → community re-check → search-standalone per song → ported `pick_auto_selection`
>   tier-1 gate → buckets CONFIDENT / NEAR-MISS / no-match. Resumable via
>   `outreach_out/audio_search_cache.json`; outputs `actions_v2.json` + `review_packet_v2.md`.
>   NEAR MISS = lossless non-vinyl torrent, filename matches, <50 seeders — surfaced with
>   saved session id + best index so Andrew can hand-approve (seeders≥50 proved strict:
>   even Bohemian Rhapsody's best FLAC had 44).
> - `scripts/karaokehunt_outreach/phase2_execute.py` — Phase 2 executor (dry-run by
>   default, `--execute` to act; idempotent via `phase2_state.json`). KEY LEARNINGS baked
>   in: (1) admin-created search sessions 403 on create-from-search under an impersonated
>   user → phase 2 re-searches AS the user and exact-matches the reviewed pick
>   (provider+target_file), skips (never substitutes) if gone; (2)
>   `POST /api/users/admin/credits` EMAILS the user — existing gen users (3 of 89:
>   anahilopez8682@, liuhsua91@, mudrocksebastian6@gmail.com, all 0 credits — see
>   `outreach_out/existing_gen_users.txt`) are reported not silently granted;
>   (3) match-judge does NOT reliably fix typos ("Bohemian Rapsody" came back cosmetic
>   with typo intact) — the filename gate correctly blocks those; (4) create-from-search
>   deducts 1 credit, so job-submitted users end with 2/3 — OPEN QUESTION for Andrew.
> - **EMAIL POLICY (Andrew, verbatim intent)**: he reviews ALL emails before send; send in
>   batches of ~10 via his Gmail andrew@nomadkaraoke.com. Nothing in phase1b/phase2 sends
>   email; the send step should create Gmail DRAFTS for him.
> - **RELAXED TORRENT RULE (Andrew)**: any FLAC torrent ≥2 seeders is acceptable if the
>   torrent filename roughly matches — he eyeballs. Review surface = Google Sheet
>   "KaraokeHunt Outreach — Torrent Review v2 (2026-09-13)" (his Drive; created via
>   Drive MCP; `scripts/karaokehunt_outreach/torrent_review.py` regenerates the CSV).
>   Groups: CONFIDENT 28 / A filename-matches 53 / B filename-differs 4 / C no-torrent 45.
>   He fills the "Review notes" column; map notes back via the `key` column.
> - **LOCALIZATION**: no direct country/language data exists anywhere (Firestore/Auth/
>   Pushbullet empty; Kit geo 23/348 with 1 overlap). Inference from song language +
>   domains → `outreach_out/language_inference.{json,csv}` (28 non-en of 89; high-conf:
>   vi ar he ko hi id it fr es pt tl). Plan: bilingual emails for high-confidence rows;
>   for everyone, footer links to a HOSTED LOCALIZED LETTER page (33 locales via existing
>   translate.py) = true one-click "read this in your language" (Andrew approved) — build
>   AFTER email wording is final. NOTE: fj/mr/ne/lt/ka NOT in the 33 locales.

**Purpose:** hand a fresh Claude session everything needed to finish the KaraokeHunt
requester-outreach work. Most of the decommission is DONE; the remaining build is the
**Segment A generation pipeline** (find real audio for each requested song, only
auto-generate confident FLAC-from-torrent matches, email everyone appropriately).

Branch / worktree for this work:
- Worktree: `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-karaokehunt-outreach`
- Branch: `feat/sess-20260913-0106-karaokehunt-outreach`
- Resume the original session if useful: `cld --resume e53abc1d-28be-4ae0-9c97-965f2b533470`

Backlog item: FROG #2 "KaraokeHunt order-email recovery + app redirect" (WIP).
Related memory: `project_karaokehunt_gcp_decommission`, `reference_karaokehunt_old_project`.

---

## 1. Background (what KaraokeHunt was)

`KaraokeHunt` was Andrew's earlier mobile app (FlutterFlow + Firebase, GCP project
`projectbread-karaokay`). Users could search a karaoke-song catalog and **request** a
track to be made. Requesting did essentially nothing except fire a Pushbullet
notification to Andrew — **no payment, no datastore, no fulfilment.** It's being fully
retired in favour of **Nomad Karaoke / gen.nomadkaraoke.com**, which actually generates
karaoke videos. This session drew a line under the KaraokeHunt era and set up outreach to
convert its users into gen users.

---

## 2. DONE this session

### Cost / security (old GCP project `projectbread-karaokay`)
- **Billing unlinked** (`gcloud billing projects unlink projectbread-karaokay`) → all
  billing-gated services dead. Root cause of the spend: an unrestricted 2018 API key
  (Places/Maps) hit by a scraper + 5 `allUsers`-invoker Cloud Functions. **Deleted** the
  leaked key.
- ⚠️ After billing-unlink, GCS object reads + Cloud Functions describe return 403; only
  Firestore/Auth free-tier + bucket listing survive.

### Data recovered (all saved to `old-or-other/karaokehunt-users-export-2026-09-09/`)
- **User emails**: Firestore `users` (352) ∪ Firebase Auth (351) = **357 deduped**
  (`all_emails_deduped.csv`, `firestore_users.json`, `firebase_auth_users.json`).
  (firebase CLI broken on Node v26 → used Identity Toolkit REST `downloadAccount`.)
- **Order/request history** = Pushbullet push history (the ONLY record; no datastore).
  Pulled the full account history (11,599 pushes back to 2016). KaraokeHunt titles:
  **"KaraokeHunt Order"** (265 pushes 2024-07→2026-09; body = `KaraokeHunt App Request
  Success! Email: X | Artist: A | Title: T | InputURL: U | Backing Vocals: bool`) and
  **"KaraokeHunt Registration"** (350, 2023-03→2026-09). Parsed to
  `karaokehunt_track_requests.csv` (**241 requests / 96 distinct emails**),
  `karaokehunt_registrations.csv`, `karaokehunt_account_deletions.csv`, raw in
  `pushbullet_pushes_raw.json`. Pushbullet token is in repo `.envrc` as
  `PUSHBULLET_TOKEN` (account andrew.d.beveridge@gmail.com). RATE LIMIT is brutal
  (16384-unit bucket, ~4 units/push, `x-ratelimit-remaining`/`-reset` headers) — pull
  slowly, foreground; `/tmp/pb_pull4.py` was a cursor-persisting resumable version.
- **Play Console reports**: full GCS mirror `gs://pubsite_prod_rev_04078640156148783055/`
  copied to `play-console-reports/` (4,621 CSVs incl. 528 KaraokeHunt; acquisition,
  reviews, stats — spans all Andrew's Play apps 2012→2026). Reviews/financials were
  essentially empty (0 ratings, free app).
- **Kit subscribers**: `NomadKaraoke-Kit-Subscribers-All-2026-09-13-6647674.csv`
  (348 active subs; email/name/created_at/geo/referrer/utm).

### Stores — both apps REMOVED (Andrew authorized; reversible, NOT deleted)
- **iOS** (App Store Connect app `6445938099`, was v1.10.0): Pricing & Availability →
  **Remove App From Sale** (gone within 24h).
- **Android** (Play `com.karaokehunt.karaokehunt`, app id 4974846911887103500, was
  Production): Test and release → Advanced settings → App availability → **Unpublished**
  (gone within ~1h). Did NOT click "Delete app" (that's a 7-day permanent delete that
  loses console data — leave it so analytics stay accessible).
- `karaokehunt.com` already 301-redirects (Andrew) and is also the iOS Support/Marketing
  URL, so it covers both listings' website links.
- **Learnings from Play analytics** (tiny scale, directional): US #1 + **Philippines #2**
  (big karaoke market); steady ~1k organic Play-search impressions/mo at ~22% install
  conversion but ~5 MAU → *discovery was fine, retention/product was the problem*; the
  #1 engaged action was **requesting a specific song** — validates gen's value prop.

### Backlog / memory
- Added PRODUCT item **"Native Nomad Karaoke mobile app (iOS + Android)"** (successor to
  the retired apps; `nomad-mobile` Expo repo is scaffolding only).
- **Phase E of the GCP decommission is now UNGATED** — apps are down, so
  `gcloud projects delete projectbread-karaokay` + close billing acct
  `0123B3-AAC0D8-4344D3` can happen whenever Andrew wants.

### Outreach tool built (Phase 1 = analyze/draft only, ZERO spend)
Location: `scripts/karaokehunt_outreach/` in this worktree.
- `phase1_analyze.py` — **Segment A** (requesters): cleans + dedupes requests, drops
  Andrew's own email + junk, checks each unique song for an existing community karaoke
  version via `POST /api/bulk/availability`, buckets (community-link vs generate), and
  drafts a personalized per-requester email. Output → `outreach_out/review_packet.md` +
  `outreach_out/actions.json`.
- `segment_bc.py` — **Segments B & C** (account + 3 credits + "we've moved" email, NO
  generation). Output → `outreach_out/review_packet_bc.md` + `outreach_out/actions_bc.json`.

---

## 3. The three audience segments (397 people, none double-messaged; priority A>B>C)

| Seg | Who | Count | Message | Account + 3 credits | Generate a track? |
|-----|-----|------:|---------|:---:|:---:|
| **A** | KaraokeHunt **requesters** | 94 | Personalized per requested song | ✅ | ✅ (only confident matches — see §4) |
| **B** | KaraokeHunt app users, never requested | 266 | "Your KaraokeHunt app is retired → Nomad Karaoke" | ✅ | ❌ |
| **C** | Kit-only (brochure/sticker/GitHub/YouTube) | 37 | "You found us online → it's now Nomad Karaoke" | ✅ | ❌ |

**Decisions locked by Andrew:** 3 free credits to everyone (apology for the delay);
Segment-A jobs are **public** (YouTube + community catalog) and **user-owned** (so the
requester gets the lyrics-review email); draft everything, **zero spend until Andrew
approves each row**; Andrew reviews **every** email before send.

⚠️ **Segment C overlaps the approved Frog #1 "Launch email to old mailing list"**
(discount-code campaign). Reconcile to ONE coordinated offer before sending — don't hit
these 37 with two competing emails.

---

## 4. WHAT REMAINS — Segment A generation pipeline (the main build)

Andrew's refined spec (verbatim intent): **dedupe → skip obvious junk → search for
existing community versions first → for unique requests that get through, run a
flacfetch-remote search (same as karaoke-gen audio search) per song and review results →
only auto-submit a job for requests with a CONFIDENT FLAC match from a TORRENT source →
for the rest, send an email variant: "we saw you requested '<title>' by '<artist>' but
couldn't find a clear match" (still gave you credits, try it yourself here).**

Current Phase-1 state: dedupe + junk-skip + community check are DONE. Of 153 unique
requester songs, **14 have a community version** (link them), **139 don't** (candidates
for generation). Many of the 139 are niche or typo'd.

Steps to build next:
1. **(Recommended) match-judge canonicalization pass** on the 139 first — fixes typos
   (e.g. "bohemian rapsody"→"Bohemian Rhapsody", "Olivia Newton"→"Olivia Newton-John")
   and will re-classify some as "community version exists," cutting the generate count
   and cost. Endpoint `POST /catalog/match-judge` / service `backend/services/match_judge/`.
2. **flacfetch-remote audio search per song** (the new step). For each of the remaining
   unique songs, run the same search karaoke-gen uses and capture the verdict:
   is there a **confident lossless/FLAC match from a torrent source**? Collect results
   into the review packet so Andrew can eyeball. → SEE §6 for the concrete API/functions.
   - NOTE: the captured `InputURL` in most requests is NOT a usable YouTube link (users
     pasted the song title), so direct-URL jobs mostly won't work — audio SEARCH is the
     right path, which is exactly why Andrew wants this step.
3. **Bucket the 139 into**: (a) confident FLAC-from-torrent → auto-submit job; (b) no
   confident match → "couldn't find a clear match" email variant (no job).
4. **Phase 2 — execute (spend happens here), gated on Andrew's per-row approval:**
   - Create account + grant 3 credits: `POST /api/users/admin/users {email,
     display_name?, initial_credits:3}` (idempotent; 409 if exists; sends no email).
   - Submit user-owned job for bucket-(a) songs: `POST /api/admin/users/{email}/impersonate`
     (admin token) → returns the user's session token → `POST /api/jobs/create-from-url`
     (or the search-based path) **with that session token** so the job is owned by the
     requester and THEY get the lyrics-review email. `is_private:false` (public),
     `backing_preference` = `auto` if captured "Backing Vocals: true" else `clean`.
     ⚠️ With an ADMIN token, `create-from-url` ignores `body.user_email` and owns the job
     as admin — you MUST impersonate for user-owned jobs.
   - Send the approved outreach emails (Andrew reviews each first). For Segment A there
     are now THREE email variants: community-link / job-submitted / couldn't-find-match.
5. **Reconcile Segment C** with Frog #1 before sending C.

---

## 5. Concrete API reference (verified this session)

- **Admin token**: `gcloud secrets versions access latest --secret=admin-tokens
  --project=nomadkaraoke | cut -d',' -f1`. Send as `Authorization: Bearer <t>` or
  `X-Admin-Token`.
- **Prod API base**: `https://api.nomadkaraoke.com` (all routes under `/api`).
  ⚠️ **Cloudflare WAF** bans non-browser User-Agents (error 1010) — send a browser UA
  header on every request (see `phase1_analyze.py` `BROWSER_UA`).
- **Community check**: `POST /api/bulk/availability` `{tracks:[{artist,title}]}` (max 100)
  → `{results:[{artist,title,available,brands,brand_count,versions:[{brand,url}]}]}`.
  Service: `backend/services/karaokenerds_service.py::check_community_versions_batch`.
- **Account + credits**: `POST /api/users/admin/users` `{email, initial_credits:3}`
  (`backend/api/routes/users.py:2060`). Users are in Firestore `gen_users`. Pure credit
  grant: `POST /api/users/admin/credits`.
- **Magic-link login** (users self-serve after we make accounts): public
  `POST /api/users/auth/magic-link {email}`. Just tell people to sign in at
  gen.nomadkaraoke.com with their email.
- **Impersonate**: `POST /api/admin/users/{email}/impersonate` (`admin.py:2176`) → session
  token for that user.
- **Job submit (URL)**: `POST /api/jobs/create-from-url` (`file_upload.py:1781`), model
  `CreateJobFromUrlRequest`. Fields: `url` (required), `artist`, `title`, `is_private`,
  `backing_preference` (`auto`|`clean`|`review`), `review_mode`, `enable_youtube_upload`,
  `theme_id`, …
- **Job submit (search)**: `POST /api/jobs/create-from-search` (`jobs.py:2443`); bulk
  `POST /api/bulk/submit` (max 100, `bulk.py:277`, has `auto_select_if_lossless`).

## 6. flacfetch audio search — the "confident FLAC-from-torrent" step (verified)

This is the new middle step for Segment A. karaoke-gen proxies flacfetch (torrent
trackers RED/OPS + YouTube + Spotify).

**Read-only preview per song (no job, no credit charge):**
`POST /api/audio-search/search-standalone` `{artist, title}`
(`backend/api/routes/audio_search.py:1015`). Returns `{search_session_id, results[],
results_count}`; each result has `is_lossless`, `provider` (RED/OPS/YouTube/Spotify),
`seeders`, `quality_data{format,bit_depth,…}`, `target_file`, `match_score`, `release_type`.
It only writes a small 7-day-TTL Firestore search-session doc (no job, no charge; use an
admin token to bypass the non-admin credit *check*). This is the endpoint to loop over the
~139 songs to collect verdicts. ⚠️ Same Cloudflare browser-UA requirement.

**Confident-FLAC-from-torrent verdict = reuse `pick_auto_selection(results, title)`**
(`backend/services/audio_search_service.py:172`). It returns an index to auto-select or
`None` to defer. A non-`None` result means the best candidate is category `BEST CHOICE`:
`is_lossless == True` **AND** `seeders >= 50` **AND** media != vinyl **AND** filename
matches the title (`_check_filename_mismatch`). Because flacfetch computes `is_lossless`
as *true* lossless (Spotify-FLAC and YouTube are `False`), `is_lossless==True` already
implies a real FLAC torrent source (RED/OPS). Import that function directly, or port the
tier-1 rule (it's ~20 lines). **Use `pick_auto_selection`, NOT `select_best`** — the
single-job `auto_download` path uses the looser `select_best` quality ranking, which does
NOT enforce the confident-torrent-FLAC gate Andrew wants.

**Then submit (only for confident ones):** `POST /api/jobs/create-from-search` with the
`search_session_id` from the preview + the chosen `selection_index` — the preview session
is reusable within its 7-day TTL, so preview-now/submit-after-approval works. Do this
under the requester's impersonated session token (§4/§5) so the job is user-owned.

**Direct flacfetch alternative:** `POST {FLACFETCH_API_URL}/search` header `X-API-Key:
{FLACFETCH_API_KEY}`, body `{artist, title, exhaustive?}` → `SearchResponse`
(`flacfetch/flacfetch/api/routes/search.py:20`, models `flacfetch/flacfetch/api/models.py`).
Same fields; but the karaoke-gen `search-standalone` path is less code (gives you the
ready `search_session_id`). flacfetch env: `FLACFETCH_API_URL`, `FLACFETCH_API_KEY`
(`backend/config.py:239`); flacfetch key is retrievable via `ssh flacup` per memory.

**Bucketing outcome for the 139:** `pick_auto_selection` returns a pick → bucket (a)
auto-submit; returns `None` → bucket (b) "couldn't find a clear match" email variant
(no job, but the user still got 3 credits and can try it themselves).

---

## 7. File inventory (`old-or-other/karaokehunt-users-export-2026-09-09/`)

- `all_emails_deduped.csv` (357), `firestore_users.json`, `firebase_auth_users.json`
- `karaokehunt_track_requests.csv` (241), `karaokehunt_registrations.csv` (350),
  `karaokehunt_account_deletions.csv` (20), `pushbullet_pushes_raw.json`
- `NomadKaraoke-Kit-Subscribers-All-2026-09-13-6647674.csv` (348)
- `play-console-reports/` (4,621 Play report CSVs)
- `outreach_out/` — `review_packet.md` + `actions.json` (Segment A);
  `review_packet_bc.md` + `actions_bc.json` (Segments B & C)

Tool code: `scripts/karaokehunt_outreach/{phase1_analyze.py, segment_bc.py}` in this worktree.

> NOTE: the export dir holds real user PII and is under `old-or-other/` (local only —
> not committed). Keep it out of git.
