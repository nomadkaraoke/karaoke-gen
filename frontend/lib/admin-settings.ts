/**
 * Admin settings store for persisting admin dashboard preferences.
 *
 * Uses zustand with localStorage persistence for settings like
 * whether to show test data in admin views.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AdminSettings {
  /**
   * When false (default), test data (e.g., @inbox.testmail.app users) is hidden.
   * When true, all data including test data is shown.
   */
  showTestData: boolean
  setShowTestData: (show: boolean) => void
  /**
   * When true (default), the /app dashboard shows other accounts' jobs parked at
   * awaiting_audio_selection (e.g. fiverrbot orders needing a manual audio pick).
   */
  showAwaitingAudioJobs: boolean
  setShowAwaitingAudioJobs: (show: boolean) => void
}

export const useAdminSettings = create<AdminSettings>()(
  persist(
    (set) => ({
      showTestData: false,
      setShowTestData: (show) => set({ showTestData: show }),
      showAwaitingAudioJobs: true,
      setShowAwaitingAudioJobs: (show) => set({ showAwaitingAudioJobs: show }),
    }),
    {
      name: 'admin-settings',
    }
  )
)
