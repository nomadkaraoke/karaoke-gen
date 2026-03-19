# Auto-Updating Disposable Email Blocklist

## Problem

The disposable email blocklist is maintained manually — ~160 hardcoded domains in Python plus admin UI additions. New disposable email services appear frequently, and the list falls behind. The community-curated [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains) repo tracks ~4,800 domains and updates regularly.

## Solution

Automatically sync the external blocklist daily, while preserving admin control to add custom domains and allowlist false positives.

## Data Model

The Firestore `blocklists/config` document is restructured:

| Field | Type | Description |
|-------|------|-------------|
| `external_domains` | list[str] | Domains synced from the GitHub repo (~4,800). Auto-managed. |
| `manual_domains` | list[str] | Domains added by admins via UI. Persist across syncs. |
| `allowlisted_domains` | list[str] | Domains explicitly permitted, overriding the external list. |
| `blocked_emails` | list[str] | Unchanged. |
| `blocked_ips` | list[str] | Unchanged. |
| `last_sync_at` | timestamp | Last successful external sync. |
| `last_sync_count` | int | Number of domains in the external list at last sync. |
| `updated_at` | timestamp | Last manual change. |
| `updated_by` | str | Admin who made the last manual change. |

**Effective blocklist** = `(external_domains + manual_domains) - allowlisted_domains`

The hardcoded `DEFAULT_DISPOSABLE_DOMAINS` set in Python is removed.

## Sync Endpoint

**`POST /api/internal/sync-disposable-domains`**

- Authenticated via `X-Admin-Token` (same as other internal endpoints)
- Fetches `https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/refs/heads/main/disposable_email_blocklist.conf`
- Parses as newline-delimited text (one domain per line), strips whitespace/blanks
- Replaces `external_domains` in Firestore (full replacement, not incremental)
- Updates `last_sync_at` and `last_sync_count`
- Invalidates the in-memory cache
- Returns summary: domains added/removed since last sync, total count
- On fetch failure: returns error, existing list stays intact

**Trigger:** Cloud Scheduler, daily at 3:00 AM UTC. Also callable manually from the admin UI via a "Sync Now" button.

## Admin UI Changes

The Blocklists tab at `/admin/rate-limits` is updated:

### Disposable Domains Section (three sub-sections)

1. **External Domains** (~4,800) — Read-only list with search/filter. Badge: "external". Shows `last_sync_at` and count. Removing an external domain moves it to the Allowlist.

2. **Manual Domains** — Existing add/remove behavior. Badge: "manual". For domains admins discover that aren't in the external list.

3. **Allowlisted Domains** — Domains explicitly permitted despite being on the external list. Badge: "allowed". Removing from the allowlist re-enables blocking.

### Sync Status Bar

At the top of the Disposable Domains section: last sync time, domain count, and "Sync Now" button.

### Unchanged

Blocked emails and blocked IPs sections remain as-is.

## Migration Strategy

The sync endpoint handles migration on first run:

1. Detect migration needed: `external_domains` field doesn't exist yet
2. Compare current `disposable_domains` against the fetched external list
3. Domains in both → covered by `external_domains` (no action)
4. Domains only in current list → moved to `manual_domains` (preserves admin additions)
5. Remove old `disposable_domains` field
6. Remove hardcoded `DEFAULT_DISPOSABLE_DOMAINS` from Python — fallback becomes empty set if Firestore unreachable (cached data covers outages)

Subsequent sync runs just replace `external_domains`.

## Domain Source Resolution

When displaying or operating on domains:

- A domain in `external_domains` → source: "external"
- A domain in `manual_domains` → source: "manual"
- A domain in `allowlisted_domains` → source: "allowed" (not blocked)
- A domain in both `external_domains` and `manual_domains` → source: "external" (manual entry is redundant)

## Testing

- **Unit tests**: Sync parsing, migration logic, allowlist override, effective blocklist computation, source tagging
- **Integration tests**: Full sync endpoint with Firestore emulator, cache invalidation after sync
- **E2E tests**: Admin UI — source badges, sync button, allowlist flow (remove external domain → appears in allowlist → remove from allowlist → blocked again)

## Infrastructure

- **Cloud Scheduler job**: Created via Pulumi in `infrastructure/`. Daily at 3:00 AM UTC, hits the sync endpoint with admin token.
- **No new services**: Runs on existing Cloud Run backend.
