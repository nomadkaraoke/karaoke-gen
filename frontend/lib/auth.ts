"use client"

import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { User, UserPublic } from "./types"
import { api, adminApi, setAccessToken, clearAccessToken, getAccessToken } from "./api"

interface AuthStore {
  user: User | null
  isLoading: boolean
  error: string | null

  // Hydration state - true once zustand has loaded persisted data
  hasHydrated: boolean

  // Impersonation state (not persisted)
  isImpersonating: boolean
  originalAdminToken: string | null
  impersonatedUserEmail: string | null

  // Actions
  sendMagicLink: (email: string) => Promise<boolean>
  verifyMagicLink: (token: string) => Promise<boolean>
  fetchUser: () => Promise<boolean>
  logout: () => Promise<void>
  updateCredits: (credits: number) => void
  clearError: () => void
  setHasHydrated: (state: boolean) => void

  // For legacy token-based auth (admin tokens)
  loginWithToken: (token: string) => Promise<boolean>

  // Impersonation actions
  startImpersonation: (email: string) => Promise<boolean>
  endImpersonation: () => void
}

export const useAuth = create<AuthStore>()(
  persist(
    (set, get) => ({
      user: null,
      isLoading: false,
      error: null,
      hasHydrated: false,

      // Impersonation state
      isImpersonating: false,
      originalAdminToken: null,
      impersonatedUserEmail: null,

      setHasHydrated: (state: boolean) => set({ hasHydrated: state }),

      sendMagicLink: async (email: string) => {
        set({ isLoading: true, error: null })
        try {
          await api.sendMagicLink(email)
          set({ isLoading: false })
          return true
        } catch (err) {
          set({
            isLoading: false,
            error: err instanceof Error ? err.message : 'Failed to send magic link'
          })
          return false
        }
      },

      verifyMagicLink: async (token: string) => {
        set({ isLoading: true, error: null })
        try {
          const response = await api.verifyMagicLink(token)

          // Store the session token
          setAccessToken(response.session_token)

          // Create user from response
          const user: User = {
            token: response.session_token,
            email: response.user.email,
            role: response.user.role,
            credits: response.user.credits,
            display_name: response.user.display_name,
            total_jobs_created: response.user.total_jobs_created,
            total_jobs_completed: response.user.total_jobs_completed,
          }

          set({ user, isLoading: false })
          return true
        } catch (err) {
          set({
            isLoading: false,
            error: err instanceof Error ? err.message : 'Invalid or expired link'
          })
          return false
        }
      },

      fetchUser: async () => {
        const token = getAccessToken()
        if (!token) {
          set({ user: null })
          return false
        }

        set({ isLoading: true })
        try {
          const response = await api.getCurrentUser()

          const user: User = {
            token,
            email: response.user.email,
            role: response.user.role,
            credits: response.user.credits,
            display_name: response.user.display_name,
            total_jobs_created: response.user.total_jobs_created,
            total_jobs_completed: response.user.total_jobs_completed,
          }

          set({ user, isLoading: false })
          return true
        } catch {
          // Session may have expired
          clearAccessToken()
          set({ user: null, isLoading: false })
          return false
        }
      },

      logout: async () => {
        try {
          await api.logout()
        } catch {
          // Ignore errors, just clear local state
        }
        clearAccessToken()
        set({ user: null, error: null })
      },

      updateCredits: (credits: number) =>
        set((state) => ({
          user: state.user ? { ...state.user, credits } : null,
        })),

      clearError: () => set({ error: null }),

      // Legacy token-based auth for admin tokens
      loginWithToken: async (token: string) => {
        set({ isLoading: true, error: null })
        try {
          // Store the token
          setAccessToken(token)

          // Try to fetch user profile
          const response = await api.getCurrentUser()

          const user: User = {
            token,
            email: response.user.email,
            role: response.user.role,
            credits: response.user.credits,
            display_name: response.user.display_name,
            total_jobs_created: response.user.total_jobs_created,
            total_jobs_completed: response.user.total_jobs_completed,
          }

          set({ user, isLoading: false })
          return true
        } catch {
          // Token validation failed - clear token and reject
          clearAccessToken()
          set({
            user: null,
            isLoading: false,
            error: 'Invalid token or server unavailable'
          })
          return false
        }
      },

      // Start impersonating a user (admin only)
      startImpersonation: async (email: string) => {
        // Prevent nested impersonation - must end current impersonation first
        const currentState = get()
        if (currentState.isImpersonating) {
          set({
            error: 'Already impersonating a user. End current impersonation first.'
          })
          return false
        }

        set({ isLoading: true, error: null })
        const originalToken = getAccessToken()

        try {
          // Store original admin token before switching
          if (!originalToken) {
            throw new Error('No active session to preserve')
          }

          // Call API to get impersonation token
          const response = await adminApi.impersonateUser(email)

          // Switch to impersonation token
          setAccessToken(response.session_token)

          // Fetch the impersonated user's profile
          // If this fails, we need to restore the original token
          let userResponse
          try {
            userResponse = await api.getCurrentUser()
          } catch (profileErr) {
            // Restore original admin token if profile fetch fails
            setAccessToken(originalToken)
            throw profileErr
          }

          const user: User = {
            token: response.session_token,
            email: userResponse.user.email,
            role: userResponse.user.role,
            credits: userResponse.user.credits,
            display_name: userResponse.user.display_name,
            total_jobs_created: userResponse.user.total_jobs_created,
            total_jobs_completed: userResponse.user.total_jobs_completed,
          }

          set({
            user,
            isLoading: false,
            isImpersonating: true,
            originalAdminToken: originalToken,
            impersonatedUserEmail: email,
          })

          return true
        } catch (err) {
          set({
            isLoading: false,
            error: err instanceof Error ? err.message : 'Failed to impersonate user'
          })
          return false
        }
      },

      // End impersonation and return to admin session
      endImpersonation: () => {
        const { originalAdminToken } = get()

        if (originalAdminToken) {
          // Restore original admin token
          setAccessToken(originalAdminToken)

          // Clear impersonation state and refetch admin user
          set({
            isImpersonating: false,
            originalAdminToken: null,
            impersonatedUserEmail: null,
          })

          // Fetch the admin user profile
          get().fetchUser()
        }
      },
    }),
    {
      name: "nomad-karaoke-auth",
      // Only persist user, not impersonation state (impersonation should not survive page refresh)
      partialize: (state) => ({ user: state.user }),
      onRehydrateStorage: () => (state) => {
        // Called when hydration is complete
        state?.setHasHydrated(true)
      },
    },
  ),
)

// Initialize auth state on page load
if (typeof window !== 'undefined') {
  const token = getAccessToken()
  if (token) {
    // Fetch fresh user data on page load
    useAuth.getState().fetchUser()
  }
}
