import { useCallback, useEffect, useRef } from 'react'
import type { AudioEditEntry, AudioEditSessionSummary } from '@/lib/api'
import { api } from '@/lib/api'

interface UseAudioEditAutoSaveOptions {
  jobId: string
  editStack: AudioEditEntry[]
  originalDuration: number
  currentDuration: number
}

function computeSummary(
  editStack: AudioEditEntry[],
  originalDuration: number,
  currentDuration: number
): AudioEditSessionSummary {
  const breakdown: Record<string, number> = {}
  for (const entry of editStack) {
    breakdown[entry.operation] = (breakdown[entry.operation] || 0) + 1
  }
  return {
    total_operations: editStack.length,
    operations_breakdown: breakdown,
    duration_change_seconds: currentDuration - originalDuration,
    net_duration_seconds: currentDuration,
  }
}

const AUTO_SAVE_EVERY_N_EDITS = 3

export function useAudioEditAutoSave({
  jobId,
  editStack,
  originalDuration,
  currentDuration,
}: UseAudioEditAutoSaveOptions) {
  const lastBackupEditCount = useRef(0)
  const isSaving = useRef(false)
  // Store latest values in refs so visibilitychange handler always has current data
  const editStackRef = useRef(editStack)
  const originalDurationRef = useRef(originalDuration)
  const currentDurationRef = useRef(currentDuration)
  editStackRef.current = editStack
  originalDurationRef.current = originalDuration
  currentDurationRef.current = currentDuration

  const doSave = useCallback(
    async (trigger: 'auto' | 'submit' | 'manual') => {
      const currentEditCount = editStackRef.current.length
      // No edits yet
      if (currentEditCount === 0) return
      // No new edits since last backup
      if (currentEditCount === lastBackupEditCount.current) return
      // Already saving
      if (isSaving.current) return

      isSaving.current = true
      try {
        const summary = computeSummary(
          editStackRef.current,
          originalDurationRef.current,
          currentDurationRef.current
        )
        const result = await api.saveAudioEditSession(jobId, {
          edit_data: { entries: editStackRef.current },
          edit_count: currentEditCount,
          trigger,
          summary,
        })
        if (result.status === 'saved' || result.status === 'skipped') {
          lastBackupEditCount.current = currentEditCount
        }
      } catch (err) {
        console.warn('Audio edit session auto-save failed:', err)
      } finally {
        isSaving.current = false
      }
    },
    [jobId]
  )

  // Auto-save every N edits
  useEffect(() => {
    if (editStack.length === 0) return
    const editsSinceBackup = editStack.length - lastBackupEditCount.current
    if (editsSinceBackup >= AUTO_SAVE_EVERY_N_EDITS) {
      doSave('auto')
    }
  }, [editStack.length, doSave])

  // Save on page hide
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'hidden') {
        doSave('auto')
      }
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [doSave])

  const saveSession = useCallback(
    (trigger: 'auto' | 'submit' | 'manual') => doSave(trigger),
    [doSave]
  )

  return { saveSession }
}
