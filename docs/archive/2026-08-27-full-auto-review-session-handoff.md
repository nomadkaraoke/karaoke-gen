# Full-Auto Review — Session Handoff (2026-08-27)

Comprehensive handoff for a fresh Claude session picking up the "fully-automated review" initiative.
Read this + the two design docs + the private corpus, then continue from **§8 Next steps**.

- **Initiative goal (Andrew's spec):** make karaoke-gen able to SKIP the manual lyrics-review +
  instrumental-selection steps for tracks where we're confident it's safe — without sloppy releases.
  Start with easy cases, record real review decisions, then build heuristics + gating.
- **Worktree:** `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-full-auto-review`
  **Branch:** `feat/sess-20260825-0000-full-auto-review` (all work UNCOMMITTED).
- **Design docs:** `docs/archive/2026-08-25-full-auto-review-design.md` (original plan + session-2 update),
  this handoff, `docs/REPLAY.md` (how to run the replay tool).
- **Corpus (PRIVATE, workspace root — NOT in the public repo):**
  `/Users/andrew/Projects/nomadkaraoke/docs/automation-corpus/` →
  `SYNTHESIS.md` (★ read first), `lyrics-manual-edit-patterns.md`, `backing-vocals-decision-logic.md`,
  `REVIEW-QUEUE.md`, `jobs/*.md` (20 per-job records), `snapshots/`.

---

## 1. TL;DR of what happened across 3 sessions

- **Session 1:** built the offline `AutoApprovabilityScorer` + review-diff + a capture script + corpus
  scaffold; validated against 30 real jobs.
- **Session 2:** built the **replay tool** — reopen the real lyrics/instrumental review UIs read-only for
  COMPLETED jobs, with audio, running LOCALLY against prod data (read-only).
- **Session 3 (this one):** walked all **20** most-recent `admin@nomadkaraoke.com` jobs in the replay UI
  with Andrew, recording his reasoning → produced the full **pattern catalogue + SYNTHESIS.md**. Also
  hardened the replay tool (nav bar, Post-AI toggle, audio seeking) and fixed several reconstruction bugs.

## 2. ★ The key conceptual reframe (read this or you'll misread everything)

**AI lyric suggestions AUTO-APPLY on load** (`frontend/hooks/useAutoCorrect.ts` `autoApplyOnLoad` →
`acceptAll(true)`). Andrew never manually reviews them. Therefore in a job's `edit_log`:
- `ai_suggestion_accept` / `ai_suggestion_reject` = the **auto-apply's own conflict resolution**, NOT
  human decisions. **Ignore AI✗ counts as a human signal.** (Jobs I first flagged as "15-rejection
  goldmines" are actually near-pure-AI wins.)
- **Only the non-`ai_suggestion` ops are Andrew's** (`word_change/word_delete/word_add/segment_*/
  timing_change/find_replace`). Those are the real residual work.

So the goal is NOT "skip a manual step" — it's **"per track, is the auto-applied result safe to ship, or
does it need a human glance?"** 8/20 jobs had zero human edits.

## 3. The findings (see SYNTHESIS.md for full detail)

**Lyrics — 3-tier strategy:**
- **★ MASTER GATE = post-AI confidence score** (anchor coverage + gap fraction/count + #corrections +
  phantom/absurd-duration flags). Low → human; high → auto-ship. This is *exactly* what
  `backend/services/auto_approval/scorer.py` already computes — Andrew reinvented it independently.
- **Auto-fix classes:** **P4** leading connective/interjection over-insertion (`And`/`Oh`/`A`, seen 6× —
  biggest win); **P5** reference-majority (≥2/3) resolution; **P1** dedupe AI self-conflicting suggestions.
- **Gate→human (never auto):** **P3** vocalization sections (da-da-dun/woo-woo — dominates heavy-manual
  jobs); **P6** missed line; **P8** phantom/hallucinated lines (absurd durations).
- **Defer:** **P7** trailing parenthetical backing bits → delete + extend last word (= the real timing
  axis; detect via lead-vocal audio energy after the transcribed end-time).

**Backing:** no-pink → clean (always right in data); pink → **KEEP** (human UNDER-keeps — 2/8 clean picks
were wrong). Bias to keep. "Pink but don't keep" needs **3-stem analysis** (mixed/lead/backing): lead
bleed (ae0cd7e8), backing-stem-IS-lead when lead quiet/reverby (1d45b286, catastrophic if kept),
flat-noise pink (95d8e844). Maps to the up-front toggle `retain-where-possible(default)/clean/review`.

## 4. Code built (all in the worktree, UNCOMMITTED)

New:
- `backend/services/auto_approval/{__init__,models,scorer,lyrics_diff}.py` — the shadow scorer +
  review-diff. Pure, tested. Will power the real gate at `screens_worker.py:214` later.
- `backend/tests/services/auto_approval/{test_scorer,test_lyrics_diff}.py` (21 tests).
- `scripts/review_capture.py` — offline per-job corpus record generator (superseded in practice by the
  replay UI, but still works).
- `scripts/run-replay-local.sh` — one-command local replay backend.
- `docs/REPLAY.md`, `docs/archive/2026-08-25-full-auto-review-design.md`.

Modified (the replay tool):
- `backend/api/routes/review.py` — `GET /correction-data?replay=true` (admin/owner only): serves review
  data for ANY status (skips the status gate + the AWAITING→IN_REVIEW transition), attaches the parsed
  `edit_log` + a reconstructed `post_ai_segments` ("post-AI, pre-human" state). Plus a **dev-only audio
  byte-proxy** (`GET /{job}/dev-audio`, gated by env `REVIEW_AUDIO_PROXY`, supports HTTP Range) so review
  audio plays locally without GCS URL-signing. Helpers: `_reconstruct_post_ai_segments`, `_load_edit_log`,
  `_dev_audio_url`, `_ranged_response`, `_dev_audio_proxy_enabled`. Prod behaviour unchanged (flag off).
- `backend/tests/test_routes_review.py` — +5 tests (replay gate/auth, dev-audio inert). 61 pass.
- Frontend: `frontend/app/[locale]/app/jobs/[[...slug]]/client.tsx` (replay mode via `?replay=1`:
  bypass state gate for admin, `isReadOnly`, `ReplayNavBar` Prev/Next+Lyrics/Instrumental via `queue`
  URL param, `ReplayActionLog` panel, Post-AI/Final toggle), `components/instrumental-review/
  InstrumentalSelector.tsx` (+`isReadOnly` prop), `lib/api.ts` (+`replay` opt), `lib/lyrics-review/
  types.ts` (+`replay` fields). Changed files typecheck clean.

**Tests:** `python -m pytest backend/tests/services/auto_approval/ backend/tests/test_routes_review.py`
→ 82 pass. Full `make test` NOT yet run. No version bump yet. Not PR'd.

## 5. How to resume the local replay environment

Backend + frontend may still be running from this session (check `curl localhost:8000/api/health` and
`localhost:3000`). To (re)start:
```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-full-auto-review
./scripts/run-replay-local.sh          # backend :8000, REAL prod data (read-only), REVIEW_AUDIO_PROXY on
# in another terminal:
cd frontend && npm run dev             # :3000
```
Then in the browser: `localStorage.setItem('karaoke_access_token','replay-local-token')`, reload, and open:
```
http://localhost:3000/en/app/jobs?baseApiUrl=http://127.0.0.1:8000&replay=1&queue=<comma-ids>#/<jobId>/review
```
The 20-job `queue=` string is in `REVIEW-QUEUE.md` / the session transcript. Requires `gcloud auth
application-default login` with READ access to Firestore `jobs` + GCS `karaoke-gen-storage-nomadkaraoke`.
**Driving it in Playwright:** the Playwright browser is shared with Andrew (he can interact with the same
window). You can extract his live in-browser edits via a React fiber full-tree walk (see transcript for the
snippet) — needed because read-only blocks *save*, not in-UI editing.

## 6. Gotchas / caveats (IMPORTANT)

- **Don't trust the Post-AI reconstruction blindly.** It had 3 bugs this session (multi-word-replace
  "cram", dropped words, split-word identical timing) that briefly misled Andrew into thinking the AI had
  bugs it didn't. All fixed, but: **treat `corrections_updated.json` (final) as ground truth**; use the
  reconstruction/edit_log for *what changed*, not exact values.
- **edit_log is not always complete** — it can miss segment-level deletions (e.g. 33453fa0's phantom-line
  deletes aren't in its log though the final has them removed). Use raw→final diff for completeness.
- **Same-job reload doesn't re-fetch** after a backend change (SPA); do a hard reload (Cmd+Shift+R).
- **Prod bucket = `karaoke-gen-storage-nomadkaraoke`** (config default `karaoke-gen-storage` is overridden
  by env in prod). Prod Firestore collection = `jobs`.
- **GCP is read-only for this workspace** (claude-readonly ADC / here it was admin@ ADC but no
  Token-Creator). Can't sign GCS URLs locally (hence the dev-audio byte-proxy) and can't impersonate the
  backend SA. Don't attempt IAM/Pulumi writes.
- **Public repo:** never commit customer emails / the private corpus into `karaoke-gen`. Corpus lives at
  workspace root on purpose.

## 7. What's PR-ready vs not

The scorer/diff/tests and the replay tool are self-contained and green. Before PR: run full `make test`,
bump `pyproject.toml` version, `/docs-review`, `/coderabbit`. The dev-audio proxy + replay param are
prod-inert (env/flag gated) so shipping them is low-risk, but confirm the `?replay=true` admin-gating and
that `REVIEW_AUDIO_PROXY` is unset in prod. (Frontend `?replay=1` is admin-only via the existing gate.)

## 8. ★ Next steps (build order, from SYNTHESIS.md §4)

1. **Confidence gate (shadow → enforce).** Wire `auto_approval/scorer.py` into `screens_worker.py:214` in
   SHADOW mode (record verdict in `processing_metadata`, no behaviour change); validate its verdict
   against the 20-job corpus; then enforce (auto-ship high-confidence, route rest to human). Add the new
   signals: phantom/absurd-duration (P8), vocalization detector (P3).
2. **Pattern 4 auto-fixer** (leading connective/interjection) — validate against the 6 examples in the
   corpus. Then **P1 dedupe** + **P5 reference-majority** in the AI-correct step.
3. **Vocalization detector (P3)** → force human review (also feeds the gate).
4. **Backing decider + 3-stem analysis** (bias-to-keep; reject bleed / lead-misclassification /
   flat-noise) → ship the up-front `retain backing vocals?` toggle (default retain-where-possible).
5. **Timing: trailing-word extension via lead-vocal audio** (P7 + end-time-too-early) — later.

No new transcription model needed for 1–4; hard problems are *gated*, not solved. Start with #1 (validates
the whole thesis cheaply) unless Andrew redirects.
</content>
