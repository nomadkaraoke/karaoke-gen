# Plan: Admin Feedback Visibility

**Created:** 2026-03-23
**Branch:** feat/sess-20260323-1454-feedback-email-debug
**Status:** Draft

## Overview

User feedback submissions are stored in Firestore (`user_feedback` collection) but are completely invisible to the admin. The admin panel only shows a boolean "Feedback Submitted: Yes/No" badge per user. This plan adds:

1. **Admin UI page** to browse and review all submitted feedback
2. **Email notification** to admin@nomadkaraoke.com on each new submission

## Requirements

- [ ] Admin page at `/admin/feedback` listing all feedback with ratings, text, timestamps, and user info
- [ ] Sortable by date (newest first by default) and filterable by search (email)
- [ ] Star ratings rendered visually (not just numbers)
- [ ] Email sent to admin@nomadkaraoke.com immediately when feedback is submitted
- [ ] Email includes all feedback content (ratings, text fields, user email)
- [ ] Test data filtering works (exclude_test toggle)
- [ ] Sidebar navigation includes new "Feedback" item

## Technical Approach

### Admin API Endpoint
Add `GET /api/admin/feedback` to `backend/api/routes/admin.py`. Follows the standard admin endpoint pattern: `require_admin` dependency, pagination with `limit`/`offset`/`search`/`exclude_test`, returns Pydantic response model. Queries `user_feedback` Firestore collection.

### Admin UI Page
New page at `frontend/app/admin/feedback/page.tsx`. Follows existing admin page patterns (Card, Table, Badge, pagination, search input, loading states). Uses `adminApi.listFeedback()` to fetch data.

### Email Notification
Add a new method to `EmailService` (or call `send_email` directly) from the `submit_user_feedback` endpoint in `users.py`. Uses the existing SendGrid infrastructure — no new template service templates needed since this is an internal admin notification (simple HTML table with the feedback data).

## Implementation Steps

### Step 1: Backend — Admin feedback list endpoint

1. [ ] Add `AdminFeedbackItem` and `AdminFeedbackListResponse` Pydantic models to `admin.py`
2. [ ] Add `GET /api/admin/feedback` endpoint with:
   - Pagination: `limit=50`, `offset=0`
   - Search by user email
   - `exclude_test` filter (reuse `is_test_email`)
   - Sort by `created_at` descending
   - Return: items, total, offset, limit, has_more
3. [ ] Add aggregate stats in response: average ratings, total count

### Step 2: Backend — Email notification on submission

4. [ ] Add `send_feedback_notification` method to `EmailService` that sends an HTML email to `admin@nomadkaraoke.com` containing:
   - User email
   - All 4 star ratings
   - All text fields
   - Would recommend / would use again
   - Submitted timestamp
   - Link to admin feedback page
5. [ ] Call `send_feedback_notification` from `submit_user_feedback` in `users.py` (fire-and-forget, don't block the user response on email success)

### Step 3: Frontend — Admin API client

6. [ ] Add `listFeedback` method to `adminApi` in `frontend/lib/api.ts`
7. [ ] Add TypeScript types for `AdminFeedbackItem` and `AdminFeedbackListResponse`

### Step 4: Frontend — Admin feedback page

8. [ ] Create `frontend/app/admin/feedback/page.tsx` with:
   - Table: user email, date, overall rating (stars), ease of use, lyrics accuracy, correction experience, would recommend, would use again
   - Expandable rows or click-to-detail for text fields (what went well, what could improve, additional comments)
   - Search by email
   - Pagination (50 per page)
   - Refresh button
   - Test data toggle (via `useAdminSettings`)
9. [ ] Add "Feedback" item to admin sidebar in `frontend/components/admin/admin-sidebar.tsx` with `MessageSquare` icon

### Step 5: Testing

10. [ ] Backend unit test for `GET /api/admin/feedback` endpoint (pagination, search, exclude_test)
11. [ ] Backend unit test for `send_feedback_notification` email method
12. [ ] Frontend component test for feedback page (render, empty state)

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/api/routes/admin.py` | Modify | Add feedback list endpoint + response models |
| `backend/api/routes/users.py` | Modify | Add email notification call in `submit_user_feedback` |
| `backend/services/email_service.py` | Modify | Add `send_feedback_notification` method |
| `frontend/lib/api.ts` | Modify | Add `adminApi.listFeedback()` method + types |
| `frontend/app/admin/feedback/page.tsx` | Create | Admin feedback review page |
| `frontend/components/admin/admin-sidebar.tsx` | Modify | Add "Feedback" nav item |
| `backend/tests/test_admin_feedback.py` | Create | Tests for admin feedback endpoint |
| `backend/tests/test_feedback_email.py` | Create | Tests for feedback email notification |

## Testing Strategy

- **Unit tests**: Admin endpoint (pagination, filtering, sorting), email method (content formatting, send call)
- **Integration**: Submit feedback → verify email service called
- **Manual verification**: Check email arrives in admin@nomadkaraoke.com inbox after submission

## Open Questions

- [ ] Should the admin page show feedback from the old `beta_feedback` collection too, or just `user_feedback`? (Recommendation: just `user_feedback` — the beta collection is legacy)
- [ ] Should there be any spam/quality detection flagging? (Some submissions appear to game the system for credits)

## Rollback Plan

- Backend: Remove endpoint from admin.py, revert email call in users.py
- Frontend: Delete feedback page, remove sidebar item
- No data migration needed — Firestore collection unchanged
