/**
 * IndexedDB persistence for tenant bulk-upload batches, enabling
 * "re-pick to resume": if the page reloads or the browser crashes mid-batch,
 * we still hold each row's job id + GCS resumable session URIs. The user
 * re-selects the same folder, we match files by name+size+mtime, and every
 * upload resumes from its server-persisted offset — no re-uploaded bytes.
 *
 * The browser cannot re-read local files after a reload without the user
 * re-granting access, which is why the re-pick step exists at all.
 *
 * Records are pruned after MAX_AGE_MS (GCS sessions expire at ~1 week).
 */

const DB_NAME = "nomad-bulk-uploads"
const DB_VERSION = 1
const STORE = "pending-rows"
const MAX_AGE_MS = 6 * 24 * 60 * 60 * 1000 // 6 days, inside GCS's ~1-week session validity

export interface PersistedFile {
  fileType: string // "audio" | "existing_instrumental"
  /** File identity as used in the review table (webkitRelativePath or name). */
  identity: string
  /** Basename, size and mtime used to re-match the file after a re-pick. */
  name: string
  size: number
  lastModified: number
  sessionUri: string
}

export interface PersistedRow {
  /** `${batchId}:${rowId}` */
  key: string
  batchId: string
  rowId: string
  jobId: string
  artist: string
  title: string
  createdAt: number
  files: PersistedFile[]
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB unavailable"))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "key" })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function tx<T>(
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(STORE, mode)
        const req = fn(t.objectStore(STORE))
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
        t.oncomplete = () => db.close()
        t.onabort = () => db.close()
      }),
  )
}

/** Persist a row's job + session URIs right after job creation. Best-effort. */
export async function saveRowSessions(row: Omit<PersistedRow, "key">): Promise<void> {
  try {
    await tx("readwrite", (s) => s.put({ ...row, key: `${row.batchId}:${row.rowId}` }))
  } catch {
    // Persistence is an enhancement — never let it break the upload itself.
  }
}

/** Remove a row once its uploads-complete call has landed. Best-effort. */
export async function markRowDone(batchId: string, rowId: string): Promise<void> {
  try {
    await tx("readwrite", (s) => s.delete(`${batchId}:${rowId}`))
  } catch {
    /* best-effort */
  }
}

/** Drop every persisted row of a batch (user discarded the recovery offer). */
export async function clearBatch(batchId: string): Promise<void> {
  try {
    const rows = await loadAllRows()
    await Promise.all(
      rows.filter((r) => r.batchId === batchId).map((r) => tx("readwrite", (s) => s.delete(r.key))),
    )
  } catch {
    /* best-effort */
  }
}

async function loadAllRows(): Promise<PersistedRow[]> {
  return tx<PersistedRow[]>("readonly", (s) => s.getAll() as IDBRequest<PersistedRow[]>)
}

/**
 * Load the most recent unfinished batch (if any), pruning expired rows.
 * Returns null when there is nothing worth resuming.
 */
export async function loadPendingBatch(): Promise<{ batchId: string; rows: PersistedRow[] } | null> {
  try {
    const now = Date.now()
    const all = await loadAllRows()
    const expired = all.filter((r) => now - r.createdAt > MAX_AGE_MS)
    await Promise.all(expired.map((r) => tx("readwrite", (s) => s.delete(r.key))))
    const live = all.filter((r) => now - r.createdAt <= MAX_AGE_MS)
    if (live.length === 0) return null

    // Most recent batch wins.
    const byBatch = new Map<string, PersistedRow[]>()
    for (const r of live) {
      const list = byBatch.get(r.batchId) ?? []
      list.push(r)
      byBatch.set(r.batchId, list)
    }
    let best: { batchId: string; rows: PersistedRow[]; at: number } | null = null
    for (const [batchId, rows] of byBatch) {
      const at = Math.max(...rows.map((r) => r.createdAt))
      if (!best || at > best.at) best = { batchId, rows, at }
    }
    return best ? { batchId: best.batchId, rows: best.rows } : null
  } catch {
    return null
  }
}

/**
 * Match a persisted file against a re-picked selection.
 * Prefers exact identity (relative path), falls back to basename+size, and
 * requires size to match exactly — a same-named file of different size is a
 * different file and must not be resumed into the old session.
 */
export function matchRepickedFile(persisted: PersistedFile, files: File[]): File | null {
  const identityOf = (f: File): string =>
    (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name

  // Identity (relative path) outranks bare name; size must always match.
  const byIdentity = files.filter(
    (f) => identityOf(f) === persisted.identity && f.size === persisted.size,
  )
  if (byIdentity.length === 1) return byIdentity[0]
  const pool = byIdentity.length > 1
    ? byIdentity
    : files.filter((f) => f.name === persisted.name && f.size === persisted.size)
  if (pool.length === 1) return pool[0]
  // Still ambiguous (or missing) — mtime must disambiguate, else no match.
  const byMtime = pool.filter((f) => f.lastModified === persisted.lastModified)
  return byMtime.length === 1 ? byMtime[0] : null
}
