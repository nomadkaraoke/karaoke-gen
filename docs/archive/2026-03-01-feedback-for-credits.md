# Feedback-for-Credits Mechanism — 2026-03-01

## Summary

Replaced the beta-only feedback flow with a general feedback-for-credits mechanism available to all users. Users who complete 2+ karaoke videos can submit product feedback to earn 2 free credits, bringing total possible free credits to 4 (2 welcome + 2 feedback).

This was PR 2 of 2 in the beta→credits transition:
- PR #430: Removed beta program from homepage, promoted 2 free welcome credits
- PR #431: Added feedback-for-credits for all users

## Key Changes

### Backend
- **New models** in `backend/models/user.py`: `UserFeedback`, `UserFeedbackRequest`, `UserFeedbackResponse`, `FeedbackEligibilityResponse`
- **New field** on `User` model: `has_submitted_feedback: bool` (for duplicate prevention)
- **New field** on `UserPublic` model: `feedback_eligible: bool` (computed in `/me` endpoint)
- **New endpoints** in `backend/api/routes/users.py`:
  - `GET /api/users/feedback/eligibility` — returns eligibility details
  - `POST /api/users/feedback` — submits feedback, grants 2 credits
- **Updated `/me` endpoint** — computes `feedback_eligible` inline (no extra API call needed)
- **New Firestore collection**: `user_feedback` (separate from `beta_feedback`)
- **Updated welcome email** — credit-focused styling with feedback teaser

### Frontend
- **FeedbackDialog component** (`frontend/components/feedback/FeedbackDialog.tsx`) — 4 star ratings, 3 text fields, 2 checkboxes, loading/success states
- **Dashboard banner** — dismissible green banner when `user.feedback_eligible === true`
- **User dropdown** — "Earn 2 Free Credits" menu item with Gift icon
- **Types/API** — `feedback_eligible` on User type, `submitFeedback()` API method

## Decisions Made

- **Eligibility threshold**: 2 completed jobs. Low enough to be achievable, high enough that users have meaningful experience to share.
- **Credit reward**: 2 credits (same as welcome credits). Enough to be compelling, not so much it undermines paid packages.
- **One-time only**: `has_submitted_feedback` flag prevents duplicate submissions.
- **Inline eligibility**: `feedback_eligible` computed in `/me` response avoids an extra API roundtrip.
- **Separate collection**: `user_feedback` kept separate from `beta_feedback` to preserve historical beta data.
- **Text validation**: At least one text field must have >50 characters to ensure substantive feedback.

## Future Considerations

- Analytics: Track feedback submission rates and banner click-through rates
- Admin view: Consider adding an admin page to view user feedback submissions
- Follow-up: Could send email prompts to eligible users who haven't submitted yet
