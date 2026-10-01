#!/usr/bin/env bash
# Run the ASS render tests (CJK/RTL karaoke highlighting) in Linux, like prod.
#
# macOS ffmpeg's libass uses the CoreText font provider, which falls back to different
# fonts than prod and can't find CJK glyphs, so the font-sensitive render tests skip
# there. This runs them in an amd64 Debian container with fontconfig + Noto fonts,
# against BOTH renderers:
#   - Debian's apt ffmpeg (what CI's ubuntu runner uses)
#   - the johnvansickle static ffmpeg that prod uses (backend image + encoding worker)
#
# Usage: scripts/run-render-tests-linux.sh [extra pytest args]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="karaoke-gen-render-tests:1"
TESTS="tests/unit/lyrics_transcriber/output/test_ass_render_highlight.py tests/unit/lyrics_transcriber/output/test_translated_lyrics_render.py tests/unit/test_font_fallback_render.py"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE (one-off, a few minutes)..."
  docker build --platform linux/amd64 -t "$IMAGE" - <<'DOCKERFILE'
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fontconfig fonts-noto-core fonts-noto-cjk libfribidi0 curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Same source as backend/Dockerfile.base and infrastructure/packer/scripts/provision.sh
RUN curl -sL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/f.tar.xz \
    && tar -xf /tmp/f.tar.xz -C /tmp && mkdir -p /opt/ffmpeg-static \
    && cp /tmp/ffmpeg-*-amd64-static/ffmpeg /opt/ffmpeg-static/ && rm -rf /tmp/f.tar.xz /tmp/ffmpeg-*
# Minimal deps for importing karaoke_gen.lyrics_transcriber.output.subtitles + the video generator
RUN pip install --no-cache-dir pillow fonttools numpy pytest requests tenacity karaoke-lyrics-processor \
      lyricsgenius syrics shortuuid python-levenshtein metaphone toml ffmpeg-python attrs cattrs
DOCKERFILE
fi

run() {
  local label="$1" ffmpeg="$2"; shift 2
  echo "=== Render tests with $label ==="
  docker run --rm --platform linux/amd64 -e CI=true -e KARAOKE_RENDER_TEST_FFMPEG="$ffmpeg" \
    -v "$REPO_ROOT":/repo -w /repo "$IMAGE" \
    python -m pytest $TESTS -q -rs --noconftest -p no:cacheprovider -o addopts="" "$@"
}

run "Debian apt ffmpeg (CI-equivalent)" ffmpeg "$@"
run "johnvansickle static ffmpeg (prod)" /opt/ffmpeg-static/ffmpeg "$@"
