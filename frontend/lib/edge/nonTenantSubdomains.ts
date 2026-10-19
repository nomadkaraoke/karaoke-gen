/**
 * First-level subdomains of nomadkaraoke.com that are Nomad Karaoke itself, never a
 * white-label tenant. Shared by the browser (lib/tenant.ts) and the Pages Function
 * (functions/[[path]].ts) so the two lists can't drift. Mirror any change in
 * backend/middleware/tenant.py NON_TENANT_SUBDOMAINS.
 *
 * "requests" is the voting-board vanity host (requests.nomadkaraoke.com).
 */
export const NON_TENANT_SUBDOMAINS: readonly string[] = [
  "gen",
  "api",
  "www",
  "buy",
  "admin",
  "app",
  "beta",
  "requests",
]
