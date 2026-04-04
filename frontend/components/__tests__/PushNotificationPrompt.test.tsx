/**
 * Tests for PushNotificationPrompt component
 *
 * Tests the soft-ask banner for enabling push notifications,
 * including iOS detection and permission handling.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PushNotificationPrompt, PushNotificationToggle } from '../push-notification-prompt'
import { usePushNotifications } from '@/hooks/use-push-notifications'

// Mock the hook
jest.mock('@/hooks/use-push-notifications')
const mockUsePushNotifications = usePushNotifications as jest.MockedFunction<typeof usePushNotifications>

// Default mock return value
const defaultHookReturn = {
  isLoading: false,
  isPushEnabled: true,
  shouldShowPrompt: true,
  needsIOSInstall: false,
  isIOS: false,
  isPWAInstalled: false,
  isSubscribed: false,
  permission: 'default' as const,
  subscribe: jest.fn().mockResolvedValue(true),
  unsubscribe: jest.fn().mockResolvedValue(true),
  dismissPrompt: jest.fn(),
  error: null,
  isSupported: true,
}

describe('PushNotificationPrompt', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUsePushNotifications.mockReturnValue(defaultHookReturn)
  })

  describe('visibility conditions', () => {
    it('renders nothing when loading', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isLoading: true,
      })

      const { container } = render(<PushNotificationPrompt />)
      expect(container.firstChild).toBeNull()
    })

    it('renders nothing when push not enabled on server', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isPushEnabled: false,
      })

      const { container } = render(<PushNotificationPrompt />)
      expect(container.firstChild).toBeNull()
    })

    it('renders nothing when shouldShowPrompt is false', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        shouldShowPrompt: false,
      })

      const { container } = render(<PushNotificationPrompt />)
      expect(container.firstChild).toBeNull()
    })

    it('renders prompt when shouldShowPrompt is true', () => {
      render(<PushNotificationPrompt />)

      expect(screen.getByText(/Get notified when your video is ready/)).toBeInTheDocument()
      expect(screen.getByText('Enable Notifications')).toBeInTheDocument()
    })
  })

  describe('iOS Add to Home Screen instructions', () => {
    it('shows iOS instructions when needsIOSInstall is true', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        needsIOSInstall: true,
      })

      render(<PushNotificationPrompt />)

      expect(screen.getByText(/Get notifications on your iPhone/)).toBeInTheDocument()
      expect(screen.getByText(/Add to Home Screen/)).toBeInTheDocument()
      expect(screen.getByText(/Share button in Safari/)).toBeInTheDocument()
    })

    it('iOS instructions have dismiss button', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        needsIOSInstall: true,
      })

      render(<PushNotificationPrompt />)

      const dismissButton = screen.getByLabelText('Dismiss')
      expect(dismissButton).toBeInTheDocument()
    })
  })

  describe('user interactions', () => {
    it('calls subscribe when Enable Notifications clicked', async () => {
      const mockSubscribe = jest.fn().mockResolvedValue(true)
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        subscribe: mockSubscribe,
      })

      render(<PushNotificationPrompt />)

      fireEvent.click(screen.getByText('Enable Notifications'))

      await waitFor(() => {
        expect(mockSubscribe).toHaveBeenCalledTimes(1)
      })
    })

    it('shows loading state while subscribing', async () => {
      const mockSubscribe = jest.fn().mockImplementation(() => new Promise(resolve => setTimeout(() => resolve(true), 100)))
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        subscribe: mockSubscribe,
      })

      render(<PushNotificationPrompt />)

      fireEvent.click(screen.getByText('Enable Notifications'))

      expect(screen.getByText('Enabling...')).toBeInTheDocument()
    })

    it('calls dismissPrompt(false) when Not now clicked', () => {
      const mockDismiss = jest.fn()
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        dismissPrompt: mockDismiss,
      })

      render(<PushNotificationPrompt />)

      fireEvent.click(screen.getByText('Not now'))

      expect(mockDismiss).toHaveBeenCalledWith(false)
    })

    it('calls dismissPrompt(true) when Don\'t ask again clicked', () => {
      const mockDismiss = jest.fn()
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        dismissPrompt: mockDismiss,
      })

      render(<PushNotificationPrompt />)

      fireEvent.click(screen.getByText("Don't ask again"))

      expect(mockDismiss).toHaveBeenCalledWith(true)
    })
  })

  describe('error display', () => {
    it('displays error message when error is set', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        error: 'Permission denied',
      })

      render(<PushNotificationPrompt />)

      expect(screen.getByText('Permission denied')).toBeInTheDocument()
    })
  })
})

describe('PushNotificationToggle', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUsePushNotifications.mockReturnValue(defaultHookReturn)
  })

  describe('visibility conditions', () => {
    it('renders nothing when loading', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isLoading: true,
      })

      const { container } = render(<PushNotificationToggle />)
      expect(container.firstChild).toBeNull()
    })

    it('renders nothing when push not enabled', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isPushEnabled: false,
      })

      const { container } = render(<PushNotificationToggle />)
      expect(container.firstChild).toBeNull()
    })
  })

  describe('permission denied state', () => {
    it('shows "Blocked in browser" when permission denied', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        permission: 'denied',
      })

      render(<PushNotificationToggle />)

      expect(screen.getByText('Blocked in browser')).toBeInTheDocument()
    })
  })

  describe('toggle functionality', () => {
    it('shows Enable button when not subscribed', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isSubscribed: false,
      })

      render(<PushNotificationToggle />)

      expect(screen.getByText('Enable notifications')).toBeInTheDocument()
    })

    it('shows Disable button when subscribed', () => {
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isSubscribed: true,
      })

      render(<PushNotificationToggle />)

      expect(screen.getByText('Disable on this device')).toBeInTheDocument()
    })

    it('calls subscribe when Enable clicked', async () => {
      const mockSubscribe = jest.fn().mockResolvedValue(true)
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isSubscribed: false,
        subscribe: mockSubscribe,
      })

      render(<PushNotificationToggle />)

      fireEvent.click(screen.getByText('Enable notifications'))

      await waitFor(() => {
        expect(mockSubscribe).toHaveBeenCalledTimes(1)
      })
    })

    it('calls unsubscribe when Disable clicked', async () => {
      const mockUnsubscribe = jest.fn().mockResolvedValue(true)
      mockUsePushNotifications.mockReturnValue({
        ...defaultHookReturn,
        isSubscribed: true,
        unsubscribe: mockUnsubscribe,
      })

      render(<PushNotificationToggle />)

      fireEvent.click(screen.getByText('Disable on this device'))

      await waitFor(() => {
        expect(mockUnsubscribe).toHaveBeenCalledTimes(1)
      })
    })
  })
})
