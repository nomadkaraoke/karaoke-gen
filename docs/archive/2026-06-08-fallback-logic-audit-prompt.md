# Prompt: Audit karaoke-gen for legacy "fallback" logic

**Purpose:** A lot of this codebase was written by an earlier LLM that, when
unsure what should happen in some situation, tended to invent a silent
*fallback* path rather than (a) asking the human what the desired behaviour was,
or (b) failing loudly with a clear error. These fallbacks hide bugs: the system
appears to "work" while doing the wrong thing, and the real failure only surfaces
much later (or never).

**Motivating example (2026-06-08):** A job submitted from a Facebook share URL
failed with a generic `Failed to download audio file`. The non-YouTube URL path
silently fell back to running yt-dlp *locally inside the Cloud Run container*,
which then died in a buggy `convert_to_wav` (ffmpeg given both `-y` and `-n`).
The download had actually succeeded — the fallback path was both unintended
(flacfetch was supposed to be the sole downloader) and broken. See
`docs/archive/2026-06-08-flacfetch-sole-downloader-plan.md` (workspace root) and
the memory note `project_flacfetch_sole_downloader`. The fix deleted the
fallback rather than patching it.

## Your task

Systematically find every "fallback" in karaoke-gen, and for each one bring it to
the user (Andrew) with a crisp question about intended behaviour. Do **not**
change any behaviour without his answer — fallbacks sometimes ARE the right call
(e.g. graceful degradation of an optional feature). The goal is to make each one
an explicit, intentional decision.

### 1. Discover candidates (cast a wide net)

Search code, comments, docstrings, and docs. Suggested sweeps (run several):

- Literal: `rg -in "fallback|fall back|fall-back" backend/ karaoke_gen/ frontend/`
- Intent words near logic: `rg -in "best effort|best-effort|silently|swallow|ignore (the )?error|just in case|legacy|deprecated path|for now|TODO|HACK|workaround"`
- Defensive swallowing: `except Exception` / bare `except:` that `pass`, `return None`, `return`, or log-and-continue without re-raising.
- Silent defaults: `\.get\(.*,\s*['\"]?(unknown|default|none)` style, `or "Unknown"`, `or {}`, env-var defaults that mask misconfiguration.
- "If not configured, do X instead" branches (the most dangerous class — like the yt-dlp one).
- Try-import / optional-dependency fallbacks that change behaviour rather than failing.

Record each candidate with `file:line`, a one-line description, and which class
it falls into.

### 2. Triage each candidate

For each, classify and note evidence:
- **A — Legitimate graceful degradation** (optional feature, clearly intended, fails safe and visible). Likely keep; confirm it's logged/observable.
- **B — Silent fallback masking a bug / misconfiguration** (like the yt-dlp case). Candidate for: delete, or convert to fail-fast with a clear error.
- **C — Ambiguous** — needs the user's intent.

Don't trust comments that *say* a path is a safe fallback — verify what actually
happens when it triggers (what does the user see? does the job silently produce
wrong output?).

### 3. Interview the user

Present findings grouped by class, B and C first. For each B/C item ask a
focused question, e.g.:
> `audio_worker.py:NN` falls back to <X> when <Y>. Today that means <observable
> consequence>. Should this (a) fail loudly with a clear error, (b) keep the
> fallback but log/alert, or (c) something else?

Batch related items. Capture his answers as direct quotes in a plan doc
(`docs/archive/YYYY-MM-DD-fallback-audit-plan.md`) — he values verbatim
decisions.

### 4. Execute

Implement the agreed changes in small, reviewable PRs (one logical area each),
with tests. Prefer fail-fast + clear error messages over silent fallbacks.
Update `docs/LESSONS-LEARNED.md` with the principle and notable examples.

## Guardrails

- This is an audit, not a rewrite — minimise behaviour change until the user
  decides per item.
- Some "fallbacks" are load-bearing; deleting blindly will cause incidents.
- Pay special attention to anything touching: audio/lyrics download, payment,
  job state transitions, and worker triggering — silent wrong behaviour there is
  most costly.
