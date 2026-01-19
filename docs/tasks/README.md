# Frontend Consolidation Tasks

This directory contains task documents for the frontend consolidation project.

## Overview

We're consolidating 3 frontends into 1:
- Main karaoke-gen frontend (keep, extend)
- Lyrics Review frontend (migrate from MUI → Tailwind)
- Instrumental Review frontend (convert from vanilla HTML → React)

## Task Status

| Task | Description | Status | Assignee |
|------|-------------|--------|----------|
| TASK-001 | Lyrics Review Migration | Ready | - |
| TASK-002 | Instrumental Review Migration | Ready | - |

## Execution Order

Tasks can be executed **in parallel** - they're independent:

```
┌─────────────────────────────────────────────────────────────┐
│                    Phase 1 (Complete)                        │
│   Route structure + JobCard navigation + placeholders        │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│      TASK-001           │     │      TASK-002           │
│  Lyrics Review (~14k)   │     │  Instrumental (~1.7k)   │
│  Multiple sessions      │     │  Single session         │
│  recommended            │     │                         │
└─────────────────────────┘     └─────────────────────────┘
              │                               │
              └───────────────┬───────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Final Integration                         │
│   - Update E2E tests                                         │
│   - Remove old frontends                                     │
│   - Update documentation                                     │
└─────────────────────────────────────────────────────────────┘
```

## For Agent Sessions

When starting a new agent session:

1. Read the relevant task document
2. Work in the same worktree: `/Users/andrew/Projects/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends`
3. Follow the migration order specified in the task
4. Verify build passes after each tier/milestone
5. Update task status when complete

## Files Reference

- **Plan**: `docs/archive/2026-01-09-frontend-consolidation-plan.md`
- **Main worktree**: `karaoke-gen-consolidate-frontends/`
- **Branch**: `feat/sess-20260109-1931-consolidate-frontends`

## Completion Checklist

After all tasks are done:

- [ ] TASK-001 complete (Lyrics Review migrated)
- [ ] TASK-002 complete (Instrumental Review migrated)
- [ ] Build passes: `cd frontend && npm run build`
- [ ] E2E tests updated and passing
- [ ] Old frontend code removed:
  - [ ] `lyrics_transcriber_temp/lyrics_transcriber/frontend/`
  - [ ] `karaoke_gen/instrumental_review/static/`
- [ ] Documentation updated
- [ ] PR created with `/pr` command
