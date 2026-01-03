# Plan: Migrate from MailSlurp to testmail.app

**Created:** 2026-01-02
**Branch:** feat/sess-20260102-2109-migrate-mailslurp-testmail
**Status:** Complete

## Overview

Replace MailSlurp email testing service with testmail.app for E2E testing of magic link authentication. testmail.app uses a simpler API with namespace-based email addresses that require no inbox creation/deletion - emails just arrive at `{namespace}.{tag}@inbox.testmail.app`.

## Requirements

- [x] Remove all code referencing MailSlurp
- [x] Remove `mailslurp-client` npm dependency
- [x] Implement testmail.app API integration
- [x] Update environment variable names (`TESTMAIL_API_KEY`, `TESTMAIL_NAMESPACE`)
- [x] Maintain same `EmailHelper` interface for minimal test changes
- [x] Update archive docs to note migration (optional - historical accuracy)

## Technical Approach

### API Differences

| Feature | MailSlurp | testmail.app |
|---------|-----------|--------------|
| Email format | Random `xxx@mailslurp.com` | `{namespace}.{tag}@inbox.testmail.app` |
| Inbox creation | Required (`createInbox()`) | Not needed - use any tag |
| Inbox deletion | Required (`deleteInbox()`) | Not needed |
| Get emails | `waitForLatestEmail(inboxId)` | GET `/api/json?apikey=X&namespace=Y&tag=Z&livequery=true` |
| Auth | API key in constructor | API key as query param |
| Email body | `email.body` | `email.html` or `email.text` |

### Implementation Strategy

1. **Keep same `EmailHelper` interface** - `createInbox()`, `waitForEmail()`, `extractMagicLink()`, `deleteInbox()`
2. **Use testmail.app's live query feature** - `livequery=true` waits for email to arrive (up to timeout)
3. **Generate unique tags** - Use timestamp + random suffix for each test run to avoid collisions
4. **Inbox becomes a simple object** - `{ id: tag, emailAddress: '{namespace}.{tag}@inbox.testmail.app' }`
5. **Delete becomes no-op** - testmail.app emails auto-expire, no cleanup needed

### Environment Variables

| Old | New |
|-----|-----|
| `MAILSLURP_API_KEY` | `TESTMAIL_API_KEY` |
| (none) | `TESTMAIL_NAMESPACE` |

Values: Set via environment variables (see `frontend/.env.local.example`)

## Implementation Steps

1. [x] **Update `email-testing.ts`** - Replace MailSlurp implementation with testmail.app
   - Remove `mailslurp-client` import
   - Add testmail.app API fetch implementation
   - Use native `fetch` (no new dependencies)
   - Generate unique tag for each "inbox"
   - Implement `livequery=true` for waiting
   - Adapt email body extraction (`html`/`text` instead of `body`)

2. [x] **Update `full-user-journey.spec.ts`** - Change references
   - Update doc comments (env var name)
   - Update error messages
   - Update console logs

3. [x] **Update `playwright.record.config.ts`** - Change env var comment

4. [x] **Update `package.json`** - Remove `mailslurp-client` dependency

5. [x] **Run `npm install`** - Update package-lock.json

6. [x] **Create `.env.local.example`** - Document required env vars for local testing

7. [x] **Update archive docs** - Skipped intentionally; historical docs preserved for accuracy

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `frontend/e2e/helpers/email-testing.ts` | Modify | Replace MailSlurp with testmail.app |
| `frontend/e2e/production/full-user-journey.spec.ts` | Modify | Update env var refs and messages |
| `frontend/playwright.record.config.ts` | Modify | Update env var comment |
| `frontend/package.json` | Modify | Remove `mailslurp-client` |
| `frontend/package-lock.json` | Modify | Auto-updated by npm install |
| `frontend/.env.local.example` | Create | Document env vars for local testing |

## Testing Strategy

- **Manual testing**: Run production E2E test with testmail.app credentials
  ```bash
  cd frontend
  TESTMAIL_API_KEY=your-api-key \
  TESTMAIL_NAMESPACE=your-namespace \
  npm run test:e2e:prod:headed
  ```
- **Verify**: Email received, magic link extracted, auth flow completes
- **CI testing**: Env vars already configured in GitHub Actions org secrets

## Open Questions

None - requirements are clear.

## Rollback Plan

1. Revert the commit
2. Run `npm install` to restore mailslurp-client
3. Re-add MAILSLURP_API_KEY to secrets if removed
