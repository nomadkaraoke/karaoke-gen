# KaraokeHunt outreach — batch generation through Phase C — 2026-09-19

**Project:** karaoke-gen (worktree `karaoke-gen-karaokehunt-outreach`)
**Branch:** `feat/sess-20260913-0106-karaokehunt-outreach` (PRs #1004 #1005 #1015 #1016 merged from it)
**Status:** in-progress — handoff to fresh session

## Summary
One long multi-day session (2026-09-13 → 09-19) that took the KaraokeHunt requester
outreach from analysis to near-completion: canonicalized all 139 requested songs
(InputURL-oEmbed + search evidence + web search), batch-generated 90 public karaoke
jobs under holding account karaokehunt@nomadkaraoke.com, ran the full-auto-review
experiment (13/89 ≈ 15% full-auto), triaged the rest for Andrew's review (15 easy +
26 medium done or in progress; 34 skip → users), shipped one-click authed review
links, reassigned 62 jobs to requester accounts (48 created silently with 3 credits),
and drafted the first Gmail outreach batch (10, NOT sent).

## What changed
- **Prod jobs**: 90 jobs created (review_mode=auto, public, MFY=true); 39 complete/live
  on YouTube as of wrap; 62 reassigned to requesters; 49 accounts exist.
- **karaoke-gen PRs shipped**: #1004 made_for_you admin-editable (stale-review
  exemption); #1005 `POST /api/internal/jobs/{id}/auto-approval-eval`; #1015 admin
  login-link mint endpoint with `job_review:<id>` redirect purpose; #1016 verify-page
  hard-navigation fix for hash redirect paths. All deployed + prod-verified.
- **Gmail**: 10 outreach drafts created in Andrew's account (batch 1, English,
  completed-video variant). Nothing sent to anyone.
- **Ops incidents handled**: RED IP-ban of flacup (27 download failures, 100%
  recovered via retry loop); encoding-worker disconnect (Buck Owens, retried);
  Cloudflare-524 duplicate job (cancelled); tangled reset job (cancelled+resubmitted).

## Decisions & rationale
- Holding-account pattern (karaokehunt@ + catchall) to contain ALL job emails until
  coordinated outreach — worked perfectly.
- Relaxed torrent rule (Andrew): any FLAC ≥2 seeders with roughly-matching filename.
- Brand-only karaoke versions on KN → generate anyway (Andrew).
- Non-English / no-reference songs → skip Andrew's review; requesters self-review via
  one-click links.
- Emails: Gmail drafts only, Andrew sends in ~10 batches (standing policy).

## Learnings / gotchas
See §3 of `docs/archive/2026-09-19-karaokehunt-outreach-phase-c-handoff.md` — the
authoritative list (MFY dual-role, /admin/credits emails users, RED burst bans,
CF-524 double-creates, retry vs reset semantics, KN never-scrape).

## Open threads & next steps
**Pick up from `docs/archive/2026-09-19-karaokehunt-outreach-phase-c-handoff.md`** —
it is the self-contained continuation doc: open the 16 remaining medium review tabs,
reassign newly-completed jobs, run the email batches (batch 1 awaiting Andrew's
review/send), bilingual + skip-pile batches with minted links, hosted localized
letter, Segments B/C (reconcile C with Frog #1), then final KaraokeHunt-era cleanup
(GCP Phase E delete, /backlog-finish Frog #2).

## Related docs
- `docs/archive/2026-09-19-karaokehunt-outreach-phase-c-handoff.md` (CONTINUATION)
- `docs/archive/2026-09-13-karaokehunt-outreach-handoff.md` (full history, sessions 1–5)
- Memory: `project_karaokehunt_batch_generation`, `feedback_outreach_email_sending_policy`
- Local PII data: `old-or-other/karaokehunt-users-export-2026-09-09/outreach_out/`
