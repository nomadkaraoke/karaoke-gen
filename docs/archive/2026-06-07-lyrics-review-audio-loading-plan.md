# Lyrics Review — Graceful Audio Loading + Silence Benign AbortError Noise

**Date:** 2026-06-07
**Branch:** `feat/sess-20260607-1834-lyrics-audio-loading`
**Origin:** Prod error alert — `AbortError: The fetching process for the media resource was aborted by the user agent at the user's request.` (Firefox 151, `/en/app/jobs/`, build `e8066164`). User reproduced it: clicked the play icon beside a lyric line, nothing happened (audio still loading), clicked around, reloaded, then it worked.

## Two problems, one root cause

The lyrics-review audio element (`new Audio(audioUrl)` in `AudioPlayer.tsx`) begins fetching a large file from the backend on mount. Until it has buffered enough, calling `.play()` does nothing visible — and the resulting rejected `play()` promise is reported to the error monitor as noise.

1. **Noise:** three bare `play()` calls in `AudioPlayer.tsx` (lines 82, 105, 115) have no `.catch()`. Every other audio player in the app already uses `audio.play().catch(() => {})`. The rejection becomes an `unhandledrejection` → reported via `lib/crash-reporter.ts`.
2. **UX:** there is no loading state. Playback controls are live before the media can play, so early clicks/spacebar silently do nothing.

## Architecture (current)

- `AudioPlayer.tsx` (mounted in `Header.tsx:341`) owns the single `<audio>` element and exposes imperative globals: `window.seekAndPlayAudio(time)`, `window.toggleAudioPlayback()`, `window.getAudioDuration()`, `window.isAudioPlaying`.
- **Every** playback trigger funnels through two globals:
  - `window.seekAndPlayAudio` ← `LyricsAnalyzer.handlePlaySegment` (`LyricsAnalyzer.tsx:982`), which backs the segment play buttons and `EditModal`'s `onPlaySegment`.
  - `window.toggleAudioPlayback` ← spacebar (`lib/lyrics-review/utils/keyboardHandlers.ts:113-117`), the player button, `EditModal`, `LyricsSynchronizer`.
- `EditModal` already polls `window.isAudioPlaying` every 100ms — there's precedent for a window-state bridge.

**Consequence:** gating the two globals at their source makes *all* playback non-janky automatically. A reactive readiness signal handles the *visual* requirements (spinner, disabled buttons).

## Design

### Part A — Silence the noise
1. `AudioPlayer.tsx`: add `.catch(() => {})` to the three `play()` calls (matches existing convention everywhere else).
2. `lib/crash-reporter.ts`: in `reportClientError`, early-return for benign `AbortError` (`args.error instanceof DOMException && args.error.name === 'AbortError'`) before dedup/POST. Defense-in-depth so any future un-caught media abort never pages.

### Part B — Readiness as single source of truth (`AudioPlayer.tsx`)
Track media-element readiness and expose it both imperatively (for guards) and via event (for reactive UI):

- New state/refs: `isReady`, `loadProgress` (0–1, or `null` = indeterminate), `hasError`. Keep an `isReadyRef` mirror so the global closures aren't stale.
- Listeners on the `audio` element:
  - `loadstart` → `isReady=false`, `progress=0`, `hasError=false`
  - `loadedmetadata`/`durationchange` → record duration (enables determinate progress)
  - `progress` → `progress = buffered.length ? buffered.end(buffered.length-1) / duration : 0`
  - **`canplaythrough` → `isReady=true`** (chosen readiness threshold — see Decision). Fallback: also treat fully-buffered (`buffered.end(last) >= duration - 0.25`) as ready, since `canplaythrough` is unreliable in some browsers.
  - `error` → `hasError=true`, `isReady=false`
  - reset all on `audioUrl` change
- On any readiness change: set `window.isAudioReady = isReady` (+ `window.audioLoadProgress`) and dispatch `new CustomEvent('karaoke:audio-ready', { detail: { ready, progress } })`.
- **Guard the globals:** `seekAndPlay()` and `togglePlayback()` early-return when `!isReadyRef.current`. This neutralises the spacebar, segment buttons, and modal triggers at the source.
- **Player UI:** while `!isReady && !hasError`, replace the play button with a disabled spinner and the slider/time with a progress bar (determinate once duration known, else indeterminate) + a "Loading audio…" label. On `hasError`, show an inline error.

### Part C — Reactive bridge for consumers
New hook `lib/lyrics-review/hooks/useAudioReady.ts`:
```ts
export function useAudioReady() {
  const [s, setS] = useState({ ready: false, progress: 0 })
  useEffect(() => {
    const sync = () => setS({ ready: !!window.isAudioReady, progress: window.audioLoadProgress ?? 0 })
    sync()
    const onChange = (e: Event) => setS((e as CustomEvent).detail)
    window.addEventListener('karaoke:audio-ready', onChange)
    return () => window.removeEventListener('karaoke:audio-ready', onChange)
  }, [])
  return s
}
```
Wire it into the play-button surfaces so they visually disable + show a "still loading" tooltip while `!ready`:
- `LyricsAnalyzer.handlePlaySegment` — early-return if `!window.isAudioReady` (redundant with the global guard, but lets us also disable the buttons reactively).
- Segment play buttons: `TranscriptionView.tsx`, `TimelineEditor.tsx`, `EditTimelineSection.tsx` — `disabled` + tooltip via the hook.
- `EditModal.tsx` — disable its play/sync-play buttons while `!ready`.
- Spacebar: no change needed — `window.toggleAudioPlayback` is guarded at source.

### Part D — i18n
Add new strings to `frontend/messages/en.json` (e.g. `loadingAudio`, `audioStillLoading`, `audioLoadError`) under the lyrics-review namespace, then `python frontend/scripts/translate.py --messages-dir frontend/messages --target all` (33 locales — CI enforces completeness).

## Decision (confirmed by user)
- **Readiness threshold = `canplaythrough`**: playback is only allowed once the full file is ready, so seeking anywhere is smooth and no mid-playback buffering UI is needed. The progress bar shows buffer progress until `canplaythrough` fires. Because `canplaythrough` can be unreliable across browsers, also flip to ready when the buffered range covers the full duration (fallback). Trade-off accepted: on long files / slow connections the controls stay disabled (with spinner + progress) longer.

## Files to modify / add
- `frontend/components/lyrics-review/AudioPlayer.tsx` — readiness, spinner/progress UI, guard globals, `.catch()`.
- `frontend/lib/crash-reporter.ts` — filter benign `AbortError`.
- `frontend/lib/lyrics-review/hooks/useAudioReady.ts` — **new** bridge hook.
- `frontend/components/lyrics-review/LyricsAnalyzer.tsx` — guard `handlePlaySegment`, thread readiness.
- `frontend/components/lyrics-review/TranscriptionView.tsx`, `TimelineEditor.tsx`, `EditTimelineSection.tsx` — disable segment play buttons while loading.
- `frontend/components/lyrics-review/modals/EditModal.tsx` — disable play buttons while loading.
- `frontend/messages/en.json` (+ 32 translated locales).

## Testing strategy
- **Unit (Jest + RTL):**
  - `crash-reporter` — `reportClientError` no-ops on a `DOMException('…','AbortError')`; still reports a normal `Error`.
  - `AudioPlayer` — simulate `canplay`/`error` events (mock `HTMLMediaElement`), assert spinner→controls transition, `window.isAudioReady`, and that `seekAndPlay`/`togglePlayback` no-op before `canplay` and work after.
  - `useAudioReady` — updates on `karaoke:audio-ready` dispatch.
  - `handlePlaySegment` — does not call `window.seekAndPlayAudio` when `window.isAudioReady` is false.
- **Component:** segment buttons + EditModal play buttons render `disabled` while `!ready`.
- **E2E (Playwright, prod):** load a job review page, assert the loading spinner appears then controls enable; optionally throttle network to make the loading state observable.
- `make test` must pass before PR.

## Out of scope
- Changing how the audio URL is served / streamed (no backend changes).
- The instrumental-review and audio-editor players (already `.catch()` their `play()` calls).
