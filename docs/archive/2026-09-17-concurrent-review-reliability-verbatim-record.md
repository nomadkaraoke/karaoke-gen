# Concurrent-load reliability initiative — Andrew's verbatim request (2026-09-17)

This is the canonical record of Andrew's original prompt for this initiative. Treat it as the
spec; refer back to it over time. Do not paraphrase or edit the blockquote.

> i'd like to make sure karaoke-gen works reliably and quickly even when there are multiple users
> using it at the same time. unfortunately right now my confidence in that is pretty low, as i can
> reliably cause it to start showing errors such as the general purpose "We're having trouble
> reaching our servers
>
> This is usually temporary — please try again in a couple of minutes.
>
> Any karaoke videos currently being created are unaffected and will keep processing." or the more
> specific "Failed to load lyrics - The server is temporarily unavailable. Please try again in a
> moment." when loading a lyrics review page, just by opening 10 separate karaoke jobs' lyrics
> review URLs simultaneously in 10 separate browser tabs. usually 1 or 2 load then the other 8 fail
> with one of those errors.
> Please review our architecture and perhaps try reproducing this (you can open a playwright
> browser, make sure i'm logged into karaoke-gen as admin, then open the lyrics review pages for 10
> jobs currently in-review (there's plenty right now due to another initiative) in separate tabs -
> you should see some of them load fine, others show the reconnecting error, likely some
> timeout/fail to load completely.
> Also I see the "reconnecting" warning message a bit too frequently still, e.g. when the preview
> video is loading: [Image #2] so I think we need to tune that and probably log (server side,
> persistently, with useful metadata) any time that orange warning banner or the more serious
> "server is temporarily unavailable" version is shown to any user, so later on we can review those
> logs to understand how frequently users are experiencing that and make efforts to improve the
> underlying architecture and/or tune the warning banner notifications to prevent users from having
> a poor user experience.
> Also I have my default lyrics review mode set to waveforms and i usually find when i first load
> the lyrics review page the waveforms don't load yet: [Image #3], i have to wait a while (eg. 1+
> minutes) for them to eventually load: [Image #4]
> I'm not sure if all of these are same class of problem but i definitely want all of them to be
> addressed systematically and thoroughly after brainstorming and investigation, so please record
> this verbatim first as a record and refer back to it over time

**Image references** (attached to the original message):
- **Image #2** — the orange "We're having trouble reaching our servers" banner shown overlapping
  the review page while the Preview Video modal shows "Encoding preview video…" spinner.
- **Image #3** — Synced Lyrics panel in **Waveforms** mode right after page load: word bars render
  but the waveform strips under them are missing/empty.
- **Image #4** — the same panel after waiting (~1+ min): waveform strips now rendered under every
  segment row.

## Distilled problem statements (agent summary — the blockquote above is the spec)

1. **Concurrent review-page loads fail** — opening ~10 in-review lyrics review URLs in parallel
   tabs as admin: 1–2 load, the rest show the generic "trouble reaching our servers" banner or
   "Failed to load lyrics - The server is temporarily unavailable."
2. **Orange "reconnecting" banner shows too often** — e.g. during preview-video encoding; needs
   tuning so it reflects genuine outage, not slow/heavy in-flight requests.
3. **No persistent telemetry for user-visible degradation** — want server-side, persistent,
   metadata-rich logging every time the orange banner or the more serious "temporarily
   unavailable" state is shown to any user, so frequency can be reviewed later.
4. **Waveforms review mode loads without waveforms** — first paint has no waveform strips; they
   appear only after ~1+ minutes.

## Session trail

- 2026-09-17: initiative started; worktree `karaoke-gen-concurrent-review-reliability`
  (branch `feat/sess-20260917-2329-concurrent-review-reliability`). Investigation + repro attempt
  this session; findings and plan to follow in this doc's siblings.
