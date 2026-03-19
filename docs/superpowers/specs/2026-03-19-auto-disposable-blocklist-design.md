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

**Document size**: At ~4,800 domains averaging 15 chars each, `external_domains` is ~80 KB — well within Firestore's 1 MiB document limit. The list has grown slowly (~100 domains/year). If it ever exceeds 20,000 domains, migrate to a subcollection. Cache misses will transfer ~80 KB instead of ~3 KB, but the 5-minute TTL means this happens rarely.

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
- Safety limits: 30-second request timeout, reject if response exceeds 2 MB or 50,000 domains
- During sync, any `manual_domains` entries that now appear in the fetched external list are automatically removed from `manual_domains` (they're redundant)

**Cache behavior**: The sync invalidates the cache on the instance handling the request. Other Cloud Run instances may serve stale data for up to 5 minutes (their TTL). This is acceptable for a daily sync.

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

## Backward Compatibility

`get_blocklist_config()` continues to return `{"disposable_domains": <set>, "blocked_emails": <set>, "blocked_ips": <set>}`. Internally it computes the effective set from the three new fields. `is_disposable_domain()` and all other callers remain unchanged.

The GET `/api/admin/rate-limits/blocklists` response is updated to include:
- `external_domains`: list (the synced domains)
- `manual_domains`: list (admin-added domains)
- `allowlisted_domains`: list (override domains)
- `last_sync_at`: ISO timestamp or null
- `last_sync_count`: int or null
- Plus existing `blocked_emails` and `blocked_ips`

The old `disposable_domains` response field is removed (breaking change for the admin UI only, updated in the same PR).

## Migration Strategy

The sync endpoint handles migration on first run:

1. Detect migration needed: `external_domains` field doesn't exist yet
2. Build the current effective set: Firestore `disposable_domains` UNION Python `DEFAULT_DISPOSABLE_DOMAINS`
3. Compare against the fetched external list
4. Domains in both → covered by `external_domains` (no action)
5. Domains only in the current effective set → moved to `manual_domains` (preserves admin additions and hardcoded extras)
6. Remove old `disposable_domains` field
7. Remove hardcoded `DEFAULT_DISPOSABLE_DOMAINS` from Python — fallback becomes empty set if Firestore unreachable (cached data covers outages)

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

- **Cloud Scheduler job**: Created via Pulumi in `infrastructure/`. Daily at 3:00 AM UTC, hits the sync endpoint with admin token from Secret Manager.
- **No new services**: Runs on existing Cloud Run backend.

## Out of Scope

- Wildcard/subdomain matching (the repo also publishes `disposable_email_blocklist_with_wildcards.conf` — deferred)
