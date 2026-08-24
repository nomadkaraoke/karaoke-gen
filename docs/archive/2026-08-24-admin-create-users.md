# Admin Create Users + Auto-Create on Job Reassignment (2026-08-24)

## Task (Andrew's prompt, verbatim)

> I'd like to be able to create users from the gen admin users page, so I can submit jobs
> on their behalf (while logged in as admin, after impersonating the new user account) and
> assign free credits to users who have never logged in before. I've actually just edited
> an admin-created job to change the user email address to this:
> "[customer email redacted — public repo]" but that user didn't exist yet, so i'm not sure
> what happens in this case. Ideally in this scenario that user account would get created
> on the fly and the job would be assigned to the new user account

## What happened with the reassigned job (investigation)

The reassigned job had `user_email` set to the customer's email, but
`PATCH /api/admin/jobs/{id}` only wrote the string to
the job doc — no `gen_users` account was created. Consequences: notification emails still
send (notifier uses the raw email, falls back to `en` locale), and the job would appear in
the user's list if they ever logged in (jobs are associated purely by the `user_email`
string; magic-link login lazily creates the account). But until then: no account, no
credits, impersonation 404s.

## Changes (v0.199.0)

1. **`POST /api/users/admin/users`** (`backend/api/routes/users.py`) — admin creates a
   user account directly: `email` (required), `display_name`, `initial_credits` (0-1000),
   `credit_reason`. 409 if exists. If initial credits are granted,
   `welcome_credits_granted` is set so the user doesn't also get the automatic welcome
   credit on first login. Models in `backend/models/user.py`
   (`AdminCreateUserRequest/Response`).

2. **Auto-create on job reassignment** (`backend/api/routes/admin.py` `update_job`) —
   when an admin PATCHes `user_email`, the email is trimmed + lowercased before saving,
   and if no `gen_users` account exists one is created via `get_or_create_user`. The
   response message notes the creation, and the admin jobs page toast surfaces it.

3. **Admin users page** (`frontend/app/admin/users/page.tsx`) — "Create User" button +
   dialog (email, optional display name, optional initial credits). On success the list
   is filtered to the new user, so the existing Impersonate / Add Credits buttons work
   immediately (impersonation only requires the account doc to exist).

4. `adminApi.createUser` in `frontend/lib/api.ts`; docs in `docs/API.md`.

## Tests

- New `backend/tests/test_admin_create_user.py` (9 tests: minimal create, credits +
  display name + welcome-flag suppression, lowercase normalization, 409 duplicate,
  422 invalid email, 400 out-of-range credits, 500 add-credits failure, 403 non-admin).
- `backend/tests/test_admin_job_update.py`: new `TestUpdateJobUserEmailAutoCreate` class
  (creates when unknown, skips when existing, lowercases, non-email edits never touch
  user service).
- Full backend suite: 3917 passed. Frontend: 1151 Jest tests passed; eslint + tsc clean
  on touched files.

## Post-deploy TODO

- Backfill the account for the customer whose job was reassigned before this shipped
  (details in agent memory, kept out of this public repo): either re-save the
  `user_email` field on the job in the admin UI (now auto-creates), or call the new
  create endpoint — then grant credits/impersonate as desired.

## Design decisions

- Admin-granted initial credits suppress the automatic welcome credit (set
  `welcome_credits_granted=True`) to avoid double-granting.
- `email_verified` stays false on admin-created accounts — verification still happens on
  first magic-link login.
- Job reassignment now lowercases the email; job→user association is a string match on
  `user_email`, and all account doc IDs are lowercased emails, so mixed-case entry would
  otherwise silently orphan the job.
