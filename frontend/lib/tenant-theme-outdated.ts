/**
 * Which of the signed-in user's finished portal tracks were made with an older
 * version of the tenant theme (so their job cards can suggest a re-render).
 *
 * One shared set per page: fetched once when the first job card on a tenant
 * portal asks for it, replaced after a theme save, and trimmed as re-renders
 * start (a re-rendered track picks up the current theme).
 */
import { useEffect, useSyncExternalStore } from 'react'
import { tenantThemeApi } from './api'

const EMPTY: ReadonlySet<string> = new Set()
let outdated: ReadonlySet<string> = EMPTY
let fetchStarted = false
let version = 0 // bumped on every update, so a slow initial fetch can't clobber a newer list
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function setOutdatedThemeJobs(jobIds: string[]) {
  outdated = new Set(jobIds)
  fetchStarted = true
  version++
  emit()
}

export function clearOutdatedThemeJobs(jobIds: string[]) {
  if (!jobIds.some((id) => outdated.has(id))) return
  const next = new Set(outdated)
  jobIds.forEach((id) => next.delete(id))
  outdated = next
  version++
  emit()
}

/** Test hook: forget everything (module state survives between tests). */
export function resetOutdatedThemeJobs() {
  outdated = EMPTY
  fetchStarted = false
  emit()
}

export function useOutdatedThemeJobs(enabled: boolean): ReadonlySet<string> {
  useEffect(() => {
    if (!enabled || fetchStarted) return
    fetchStarted = true
    const startedAt = version
    tenantThemeApi
      .outdatedJobs()
      .then((r) => {
        if (version === startedAt) setOutdatedThemeJobs(r.job_ids)
      })
      // Not on a member's portal / transient error: just show no hints.
      .catch(() => {})
  }, [enabled])
  return useSyncExternalStore(subscribe, () => outdated, () => EMPTY)
}
