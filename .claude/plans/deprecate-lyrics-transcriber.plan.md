# Plan: Deprecate python-lyrics-transcriber and Consolidate into karaoke-gen

**Created:** 2026-01-19
**Branch:** feat/sess-20260117-1643-deprecate-lyrics-transcriber
**Status:** Draft

## Overview

The python-lyrics-transcriber project was originally a standalone PyPI package. It was vendored into karaoke-gen as `lyrics_transcriber_temp/` to enable faster iteration. Now that the frontend has been consolidated into karaoke-gen (lyrics review UI is part of the main frontend), maintaining lyrics-transcriber as a separate project no longer makes sense.

This plan covers:
1. Updating the python-lyrics-transcriber README to redirect to karaoke-gen
2. Archiving the GitHub repo
3. Deprecating the PyPI package (via README update published as final version)
4. Reorganizing vendored code to a permanent location in karaoke-gen
5. Cleaning up unnecessary files (pyproject.toml, docs/, tests/, etc. that only made sense for a standalone package)

## Requirements

- [ ] python-lyrics-transcriber GitHub repo README updated with deprecation notice
- [ ] python-lyrics-transcriber GitHub repo archived
- [ ] Final PyPI release with deprecation notice in README
- [ ] Vendored code moved from `lyrics_transcriber_temp/` to `libs/lyrics_transcriber/`
- [ ] Unnecessary package files removed (pyproject.toml, poetry.lock, .github/, docs/, tests/, scripts/, specs/, .cursor/, .specify/)
- [ ] All import paths continue to work (Python allows `libs/lyrics_transcriber/lyrics_transcriber/` path)
- [ ] Tests pass after reorganization
- [ ] karaoke-gen pyproject.toml updated for new path

## Technical Approach

### New Path Structure

Move the vendored code to `libs/lyrics_transcriber/`:

```
karaoke-gen/
├── libs/
│   └── lyrics_transcriber/        # Just the Python package (was lyrics_transcriber_temp/lyrics_transcriber/)
│       ├── __init__.py
│       ├── cli/
│       ├── core/
│       ├── correction/
│       ├── lyrics/
│       ├── output/
│       ├── review/
│       ├── storage/
│       ├── transcribers/
│       ├── types.py
│       └── utils/
├── karaoke_gen/
├── backend/
├── frontend/
└── ...
```

### Why `libs/lyrics_transcriber/` instead of alternatives?

1. **`libs/`** - Clear convention for vendored/embedded libraries, separate from main codebase
2. **Single level** - `lyrics_transcriber` directly under `libs/`, not nested (no `libs/lyrics_transcriber/lyrics_transcriber/`)
3. **Clean imports** - `from karaoke_gen.lyrics_transcriber.core import ...` continues to work with proper Poetry config

### pyproject.toml Changes

```toml
packages = [
    { include = "karaoke_gen" },
    { include = "lyrics_transcriber", from = "libs" },  # Changed from lyrics_transcriber_temp
    { include = "backend" }
]
include = [
    "karaoke_gen/nextjs_frontend/out",
    "karaoke_gen/nextjs_frontend/out/**/*",
    "libs/lyrics_transcriber/output/fonts/*",  # Updated path
    "libs/lyrics_transcriber/output/cdgmaker/images/*",
    "libs/lyrics_transcriber/output/cdgmaker/transitions/*"
]
```

### Files to Remove from Vendored Code

From current `lyrics_transcriber_temp/`:
- `pyproject.toml` - Package config for standalone release
- `poetry.lock` - Lock file for standalone package
- `README.md` - Will be replaced by deprecation notice
- `LICENSE` - Keep MIT license in karaoke-gen root (already there)
- `Dockerfile` - Standalone container build
- `.github/` - CI/CD for standalone package
- `.cursor/` - Cursor AI config
- `.specify/` - Specify config
- `.gitignore` - Not needed (karaoke-gen has its own)
- `docs/` - Old documentation (karaoke-gen has consolidated docs)
- `scripts/` - Development scripts for standalone package
- `specs/` - Specification files
- `tests/` - Tests will need to be migrated or removed (see Testing Strategy)

**Keep only:** The actual `lyrics_transcriber/` Python package directory.

## Implementation Steps

### Phase 1: Update and Archive External Project (Manual Steps)

1. [ ] Clone python-lyrics-transcriber repo locally
2. [ ] Create deprecation notice README:
   ```markdown
   # python-lyrics-transcriber (DEPRECATED)

   **This project has been deprecated and archived.**

   The lyrics transcription functionality has been consolidated into [karaoke-gen](https://github.com/nomadkaraoke/karaoke-gen).

   ## Migration

   For karaoke video generation with synchronized lyrics:
   - Use [karaoke-gen](https://github.com/nomadkaraoke/karaoke-gen) - the complete solution
   - Web app: https://gen.nomadkaraoke.com

   ## Historical Context

   This library was originally developed as a standalone tool for creating synchronized
   lyrics files. It has now been integrated into the karaoke-gen platform which provides
   a complete end-to-end solution for karaoke video generation.

   The last standalone version was 0.81.0. No further releases will be made to PyPI.
   ```
3. [ ] Publish final PyPI version (0.82.0) with deprecation notice
4. [ ] Archive the GitHub repository via `gh repo archive nomadkaraoke/python-lyrics-transcriber`

### Phase 2: Reorganize Vendored Code in karaoke-gen

5. [ ] Create `libs/` directory
6. [ ] Move `lyrics_transcriber_temp/lyrics_transcriber/` to `libs/lyrics_transcriber/`
7. [ ] Remove `lyrics_transcriber_temp/` directory (with all its extra files)
8. [ ] Update `pyproject.toml` packages and include paths
9. [ ] Update the one script that references `lyrics_transcriber_temp` directly:
   - `scripts/benchmark_encoding.py` line 232: change `from lyrics_transcriber_temp.lyrics_transcriber.output.video` to `from karaoke_gen.lyrics_transcriber.output.video`

### Phase 3: Verify and Test

10. [ ] Run `poetry install` to verify package configuration
11. [ ] Run `make test` to ensure all tests pass
12. [ ] Verify imports work:
    ```python
    from karaoke_gen.lyrics_transcriber.core.controller import LyricsControllerResult
    from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
    from karaoke_gen.lyrics_transcriber.types import CorrectionResult
    ```

### Phase 4: Handle Tests from lyrics_transcriber

13. [ ] Evaluate lyrics_transcriber tests:
    - **74 test files** in `lyrics_transcriber_temp/tests/`
    - These test the internal lyrics_transcriber library functionality
    - Decision: Move critical tests to `tests/unit/lyrics_transcriber/` in karaoke-gen
    - Skip integration/performance/manual tests that were for standalone development

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `libs/lyrics_transcriber/` | Create (move) | New location for lyrics_transcriber package |
| `lyrics_transcriber_temp/` | Delete | Remove old vendored location |
| `pyproject.toml` | Modify | Update package paths |
| `scripts/benchmark_encoding.py` | Modify | Update import path |
| `tests/unit/lyrics_transcriber/` | Create | Migrate critical tests (optional) |

## Testing Strategy

### Immediate Testing
- Run `make test` after reorganization - all existing tests must pass
- Verify Poetry can build/install the package correctly

### Test Migration Decision
The vendored `lyrics_transcriber_temp/tests/` contains 74 test files covering:
- `unit/correction/` - Correction logic tests (important)
- `unit/lyrics/` - Lyrics provider tests
- `unit/review/` - Review server tests (less relevant - frontend consolidated)
- `integration/` - Integration tests
- `performance/` - Performance benchmarks

**Recommendation:** Migrate a subset of critical unit tests, particularly:
- `test_corrector.py` - Core correction logic
- `test_anchor_sequence*.py` - Anchor sequence matching
- Handler tests in `handlers/`

This keeps karaoke-gen's test suite focused while preserving coverage of critical functionality.

## Open Questions

- [x] Should lyrics_transcriber tests be migrated? **Recommendation: Migrate critical unit tests only**
- [x] What's the best path for vendored code? **Answer: `libs/lyrics_transcriber/`**
- [ ] Should we publish a final deprecation release to PyPI before archiving? **Recommended: Yes, with deprecation notice**

## Rollback Plan

If issues arise:
1. Git revert the consolidation commits
2. `lyrics_transcriber_temp/` can be restored from git history
3. The external python-lyrics-transcriber repo can be unarchived if needed

## Notes

- The import paths like `from karaoke_gen.lyrics_transcriber.core import ...` will continue to work unchanged
- Only the internal project structure changes, not the API
- This is primarily a cleanup/organization task with some external repo admin work
