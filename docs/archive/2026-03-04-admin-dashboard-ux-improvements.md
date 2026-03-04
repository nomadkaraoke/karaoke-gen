# Admin Dashboard UX Improvements

**Date:** 2026-03-04
**Branch:** feat/sess-20260304-1345-admin-dashboard-ux

## Issues Reported

1. **Job page - user email not clickable**: Plain text email in job detail info grid, should link to user detail page
2. **Job page - source field uninformative**: Shows generic "YouTube"/"Search"/"Upload" text without actionable detail (no URL, no artist-title, no filename)
3. **User detail - broken job links**: Job rows link to `/admin/jobs/detail?id=...` which doesn't exist (should be `/admin/jobs?id=...`)
4. **Missing cursor/tooltip hints**: Many clickable elements lack `cursor-pointer` and `title` attributes
5. **Users list - missing date columns**: No Created or Last Login columns in the users table
6. **Users list - non-interactive headers**: Table headers are static text, not sortable by click
7. **Payment history - email case mismatch**: Stored emails may have mixed case, but lookups use `.lower()`, causing Firestore `==` to miss

## Planned Fixes

### Phase 1: Quick Frontend Fixes
- 1a. Make user email clickable → link to `/admin/users/detail?email=...`
- 1b. Enhance source field: YouTube=clickable URL, Search=artist-title, Upload=filename
- 1c. Fix job links: `/admin/jobs/detail?id=` → `/admin/jobs?id=`
- 1d. Cursor and tooltip audit across all admin pages

### Phase 2: Users List Enhancement
- Add `created_at` and `last_login_at` to `UserPublic` model and API response
- Add Created and Last Login columns to users table
- Make table headers clickable for sorting

### Phase 3: Payment History Fix
- Normalize `customer_email` with `.lower().strip()` on storage
- Create backfill endpoint for existing records

### Phase 4: E2E Testing
- Extend production E2E tests for admin navigation flows
- Add regression tests for fixed issues

## Files Modified

See plan for complete file list.
