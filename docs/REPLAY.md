# Review Replay (read-only re-open of completed jobs)

Re-open the **real** lyrics-review and instrumental-selection UIs for a job that's
already **completed**, with full audio playback + backing-vocals previews (streamed
from GCS), plus a side panel showing your ordered actions (AI-accept / AI-reject /
manual / timing) from the stored edit log.

Built for the fully-automated-review recording program — see
`docs/archive/2026-08-25-full-auto-review-design.md`.

## What it is

- Backend `GET /api/review/{job_id}/correction-data?replay=true` (admin/owner only):
  serves the same payload for a job in **any** status — skips the
  AWAITING_REVIEW/IN_REVIEW gate and the status-transition side-effect, and attaches
  the reviewer's `edit_log`. Purely read-only.
- Frontend `?replay=1` on the review/instrumental route: bypasses the state gate
  (admins), renders the existing UI with `isReadOnly` (no submit / no mutations),
  and shows the **Replay action log** panel.
- `REVIEW_AUDIO_PROXY=1` (local dev only): the backend streams review audio **bytes**
  instead of GCS signed URLs, because local user ADC can't sign. Inert in prod.

## Run it locally (against real prod data, read-only)

```bash
# Terminal 1 — backend (needs `gcloud auth application-default login` with read
# access to Firestore `jobs` + the GCS bucket; makes NO writes):
./scripts/run-replay-local.sh

# Terminal 2 — frontend:
cd frontend && npm run dev        # http://localhost:3000
```

Then in the browser:

1. Open http://localhost:3000, open DevTools console, and sign in as admin:
   ```js
   localStorage.setItem('karaoke_access_token', 'replay-local-token')  // matches ADMIN_TOKENS
   ```
   Reload — you're now `admin@nomadkaraoke.com`.
2. Open a job in replay:
   - Lyrics review:
     `http://localhost:3000/en/app/jobs?baseApiUrl=http://127.0.0.1:8000&replay=1#/<JOB_ID>/review`
   - Instrumental screen:
     `http://localhost:3000/en/app/jobs?baseApiUrl=http://127.0.0.1:8000&replay=1#/<JOB_ID>/instrumental`

Audio, vocals waveform, and backing-vocals previews all play (proxied through the
local backend). The submit/mutation controls are hidden; nothing can be changed.

## Using it for the recording sessions

Work through the job list in the private corpus
(`~/Projects/nomadkaraoke/docs/automation-corpus/`). For each job, with the replay
UI open, narrate the two things we're capturing (the agent writes them into the
corpus record):

1. **Lyrics — what I corrected and why** (per manual edit / AI rejection: why the AI
   was wrong, how you knew the right answer, whether a deterministic check could catch it).
   **Timings too**: which timing adjustments you make, and what signal (e.g. the
   separated vocal audio) could detect them automatically.
2. **Backing vocals — what I heard and how I decided** (clean vs. with-backing, why;
   what would make it a clear yes/no vs. a judgement call).

The action-log panel groups your edits as **AI ✓ / AI ✗ / manual / timing** so it's
easy to talk through what the AI got right, what you overrode, and what's left.

## Notes / limitations

- Fidelity is "final state + ordered action log", not a per-keystroke visual replay
  (no intermediate full-state snapshots are stored). The final lyrics are shown with
  AI-corrected words tagged (`ai_corrected`, `original_text`, `timing_estimated`).
- If a job had its outputs deleted (admin "delete outputs"), stems may be gone and
  audio won't load — the lyrics/action-log still work.
</content>
