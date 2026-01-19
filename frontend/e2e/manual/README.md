# Manual E2E Tests

Tests in this directory require **manual setup** before running. They are NOT included in CI or `npm run test:e2e`.

## Why Manual?

These tests target components with separate dev servers that aren't auto-started by Playwright's `webServer` config.

## Tests

### `lyrics-review-mobile.spec.ts`

Tests the **LyricsTranscriber frontend** (Vite, port 5173), not the main karaoke-gen frontend (Next.js, port 3000).

**To run:**

```bash
# Terminal 1: Start LyricsTranscriber frontend dev server
cd lyrics_transcriber_temp/lyrics_transcriber/frontend
yarn dev  # Starts on port 5173

# Terminal 2: Start LyricsTranscriber backend (for API)
# This serves the review API on port 8767
poetry run python -c "from karaoke_gen.lyrics_transcriber.review.server import ReviewServer; ..."

# Terminal 3: Run the test
cd frontend
npx playwright test e2e/manual/lyrics-review-mobile.spec.ts
```

## Future

These tests should be consolidated when LyricsTranscriber is fully integrated into the main karaoke-gen frontend. See CLAUDE.md for project context.
