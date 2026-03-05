"use client"

import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '@/lib/api'
import { useTenant } from '@/lib/tenant'

// LocalStorage key for tracking prompt dismissal
const PUSH_PROMPT_DISMISSED_KEY = 'karaoke_push_prompt_dismissed'
const PUSH_PROMPT_DISMISSED_UNTIL_KEY = 'karaoke_push_prompt_dismissed_until'

// How long to wait before showing prompt again after dismissal (7 days)
const DISMISS_DURATION_MS = 7 * 24 * 60 * 60 * 1000

export type PushPermission = 'default' | 'granted' | 'denied' | 'unsupported'

export interface UsePushNotificationsReturn {
  // Feature availability
  isSupported: boolean
  isPushEnabled: boolean // Server has push enabled
  isLoading: boolean

  // Permission state
  permission: PushPermission
  isSubscribed: boolean

  // iOS-specific
  isIOS: boolean
  isPWAInstalled: boolean
  needsIOSInstall: boolean // iOS but not installed as PWA

  // Prompt control
  shouldShowPrompt: boolean
  dismissPrompt: (permanent?: boolean) => void

  // Actions
  subscribe: () => Promise<boolean>
  unsubscribe: () => Promise<boolean>

  // Error handling
  error: string | null
}

/**
 * Detect if running on iOS
 */
function detectIOS(): boolean {
  if (typeof window === 'undefined') return false
  const userAgent = window.navigator.userAgent.toLowerCase()
  return /iphone|ipad|ipod/.test(userAgent)
}

/**
 * Detect if app is installed as PWA (standalone mode)
 */
function detectPWAInstalled(): boolean {
  if (typeof window === 'undefined') return false

  // Check display-mode media query
  if (window.matchMedia('(display-mode: standalone)').matches) {
    return true
  }

  // iOS Safari: check navigator.standalone
  if ('standalone' in window.navigator && (window.navigator as any).standalone) {
    return true
  }

  return false
}

/**
 * Get device name for the subscription
 */
function getDeviceName(): string {
  if (typeof window === 'undefined') return 'Unknown Device'

  const ua = window.navigator.userAgent
  const platform = window.navigator.platform || ''

  // Try to identify the device
  if (/iPhone/.test(ua)) return 'iPhone'
  if (/iPad/.test(ua)) return 'iPad'
  if (/Android/.test(ua)) {
    if (/Mobile/.test(ua)) return 'Android Phone'
    return 'Android Tablet'
  }
  if (/Mac/.test(platform)) return 'Mac'
  if (/Win/.test(platform)) return 'Windows PC'
  if (/Linux/.test(platform)) return 'Linux PC'

  // Fall back to browser name
  if (/Chrome/.test(ua)) return 'Chrome Browser'
  if (/Firefox/.test(ua)) return 'Firefox Browser'
  if (/Safari/.test(ua)) return 'Safari Browser'

  return 'Web Browser'
}

/**
 * Check if prompt was dismissed and should stay hidden
 */
function isPromptDismissed(): boolean {
  if (typeof window === 'undefined') return false

  // Check permanent dismissal
  const permanent = localStorage.getItem(PUSH_PROMPT_DISMISSED_KEY)
  if (permanent === 'true') return true

  // Check temporary dismissal
  const dismissedUntil = localStorage.getItem(PUSH_PROMPT_DISMISSED_UNTIL_KEY)
  if (dismissedUntil) {
    const until = parseInt(dismissedUntil, 10)
    if (Date.now() < until) return true
    // Expired - clean up
    localStorage.removeItem(PUSH_PROMPT_DISMISSED_UNTIL_KEY)
  }

  return false
}

/**
 * Convert Web Push subscription to the format expected by our API
 */
function subscriptionToApiFormat(subscription: PushSubscription): {
  endpoint: string
  keys: { p256dh: string; auth: string }
} {
  const key = subscription.getKey('p256dh')
  const auth = subscription.getKey('auth')

  if (!key || !auth) {
    throw new Error('Subscription missing required keys')
  }

  return {
    endpoint: subscription.endpoint,
    keys: {
      p256dh: btoa(String.fromCharCode(...new Uint8Array(key))),
      auth: btoa(String.fromCharCode(...new Uint8Array(auth))),
    },
  }
}

/**
 * Hook for managing Web Push notification subscriptions.
 *
 * Handles:
 * - Checking browser/server support
 * - Managing permission state
 * - Subscribing/unsubscribing
 * - iOS PWA detection
 * - Prompt dismissal state
 */
export function usePushNotifications(): UsePushNotificationsReturn {
  const [isSupported, setIsSupported] = useState(false)
  const [isPushEnabled, setIsPushEnabled] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [permission, setPermission] = useState<PushPermission>('default')
  const [isSubscribed, setIsSubscribed] = useState(false)
  const [isIOS, setIsIOS] = useState(false)
  const [isPWAInstalled, setIsPWAInstalled] = useState(false)
  const [promptDismissed, setPromptDismissed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const vapidPublicKeyRef = useRef<string | null>(null)
  const registrationRef = useRef<ServiceWorkerRegistration | null>(null)

  // Get tenant context for scoping push subscriptions
  const { tenantId } = useTenant()

  // Check browser support and initial state
  useEffect(() => {
    const checkSupport = async () => {
      setIsLoading(true)
      setError(null)

      // Check client-side support
      const browserSupported =
        typeof window !== 'undefined' &&
        'serviceWorker' in navigator &&
        'PushManager' in window &&
        'Notification' in window

      setIsSupported(browserSupported)
      setIsIOS(detectIOS())
      setIsPWAInstalled(detectPWAInstalled())
      setPromptDismissed(isPromptDismissed())

      if (!browserSupported) {
        setPermission('unsupported')
        setIsLoading(false)
        return
      }

      // Check notification permission
      setPermission(Notification.permission as PushPermission)

      // Check server support
      try {
        const response = await api.getVapidPublicKey()
        setIsPushEnabled(response.enabled)
        if (response.enabled && response.vapid_public_key) {
          vapidPublicKeyRef.current = response.vapid_public_key
        }
      } catch (err) {
        console.error('Failed to get VAPID key:', err)
        setIsPushEnabled(false)
      }

      // Check if already subscribed (if permission granted)
      if (Notification.permission === 'granted') {
        try {
          const registration = await navigator.serviceWorker.ready
          registrationRef.current = registration
          const subscription = await registration.pushManager.getSubscription()
          setIsSubscribed(!!subscription)
        } catch (err) {
          console.error('Failed to check subscription:', err)
        }
      }

      setIsLoading(false)
    }

    checkSupport()
  }, [])

  /**
   * Subscribe to push notifications
   */
  const subscribe = useCallback(async (): Promise<boolean> => {
    setError(null)

    if (!isSupported || !isPushEnabled) {
      setError('Push notifications are not supported or enabled')
      return false
    }

    if (!vapidPublicKeyRef.current) {
      setError('VAPID key not available')
      return false
    }

    try {
      // Request notification permission
      const permissionResult = await Notification.requestPermission()
      setPermission(permissionResult as PushPermission)

      if (permissionResult !== 'granted') {
        setError('Notification permission denied')
        return false
      }

      // Get service worker registration
      const registration = await navigator.serviceWorker.ready
      registrationRef.current = registration

      // Check for existing subscription
      let subscription = await registration.pushManager.getSubscription()

      // Create new subscription if needed
      if (!subscription) {
        // Convert base64 VAPID key to Uint8Array
        const vapidKey = vapidPublicKeyRef.current
        const padding = '='.repeat((4 - (vapidKey.length % 4)) % 4)
        const base64 = (vapidKey + padding).replace(/-/g, '+').replace(/_/g, '/')
        const rawData = window.atob(base64)
        const outputArray = new Uint8Array(rawData.length)
        for (let i = 0; i < rawData.length; ++i) {
          outputArray[i] = rawData.charCodeAt(i)
        }

        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: outputArray,
        })
      }

      // Send subscription to backend with tenant scope
      const { endpoint, keys } = subscriptionToApiFormat(subscription)
      await api.subscribePush(endpoint, keys, getDeviceName(), tenantId)

      setIsSubscribed(true)
      return true
    } catch (err) {
      console.error('Failed to subscribe:', err)
      setError(err instanceof Error ? err.message : 'Failed to subscribe')
      return false
    }
  }, [isSupported, isPushEnabled, tenantId])

  /**
   * Unsubscribe from push notifications
   */
  const unsubscribe = useCallback(async (): Promise<boolean> => {
    setError(null)

    try {
      const registration = registrationRef.current || await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.getSubscription()

      if (subscription) {
        // Notify backend
        try {
          await api.unsubscribePush(subscription.endpoint)
        } catch (err) {
          console.warn('Failed to notify backend of unsubscribe:', err)
          // Continue anyway - local unsubscribe is more important
        }

        // Unsubscribe locally
        await subscription.unsubscribe()
      }

      setIsSubscribed(false)
      return true
    } catch (err) {
      console.error('Failed to unsubscribe:', err)
      setError(err instanceof Error ? err.message : 'Failed to unsubscribe')
      return false
    }
  }, [])

  /**
   * Dismiss the prompt (temporarily or permanently)
   */
  const dismissPrompt = useCallback((permanent: boolean = false) => {
    if (typeof window === 'undefined') return

    if (permanent) {
      localStorage.setItem(PUSH_PROMPT_DISMISSED_KEY, 'true')
    } else {
      const until = Date.now() + DISMISS_DURATION_MS
      localStorage.setItem(PUSH_PROMPT_DISMISSED_UNTIL_KEY, until.toString())
    }

    setPromptDismissed(true)
  }, [])

  // Calculate derived state
  const needsIOSInstall = isIOS && !isPWAInstalled
  const shouldShowPrompt =
    !isLoading &&
    isSupported &&
    isPushEnabled &&
    permission === 'default' &&
    !isSubscribed &&
    !promptDismissed &&
    !needsIOSInstall // Don't show regular prompt on iOS - show install instructions instead

  return {
    isSupported,
    isPushEnabled,
    isLoading,
    permission,
    isSubscribed,
    isIOS,
    isPWAInstalled,
    needsIOSInstall,
    shouldShowPrompt,
    dismissPrompt,
    subscribe,
    unsubscribe,
    error,
  }
}
