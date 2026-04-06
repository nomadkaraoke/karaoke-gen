/**
 * Tests for AuthStatus and AuthDialog components
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AuthStatus } from '../auth/AuthStatus'
import { AuthDialog } from '../auth/AuthDialog'
import { useAuth } from '@/lib/auth'

// Mock next/navigation
const mockPush = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}))

// Mock the auth module
jest.mock('@/lib/auth', () => ({
  useAuth: jest.fn(),
}))

const mockUseAuth = useAuth as unknown as jest.Mock

// Mock window.location.reload
const mockReload = jest.fn()
Object.defineProperty(window, 'location', {
  value: { reload: mockReload },
  writable: true,
})

describe('AuthStatus', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows Login button when not authenticated', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      logout: jest.fn(),
    })
    render(<AuthStatus />)

    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument()
  })

  it('shows user menu when authenticated', () => {
    mockUseAuth.mockReturnValue({
      user: { email: 'test@example.com', credits: 5, display_name: null },
      logout: jest.fn(),
    })
    render(<AuthStatus />)

    // Should show user email and credits
    expect(screen.getByText('test@example.com')).toBeInTheDocument()
    expect(screen.getByText('5 credits available')).toBeInTheDocument()
  })

  it('opens auth dialog when Login is clicked', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      logout: jest.fn(),
      sendMagicLink: jest.fn(),
      loginWithToken: jest.fn(),
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
    render(<AuthStatus />)

    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    // Dialog should appear with "Sign In" title
    expect(screen.getByText('Sign In')).toBeInTheDocument()
  })

  it('renders dropdown trigger button when authenticated', async () => {
    const mockLogout = jest.fn().mockResolvedValue(undefined)
    mockUseAuth.mockReturnValue({
      user: { email: 'test@example.com', credits: 5, display_name: null },
      logout: mockLogout,
    })
    render(<AuthStatus />)

    // Wait for component to mount
    const menuButton = await screen.findByRole('button')

    // Verify the dropdown trigger has correct attributes
    expect(menuButton).toHaveAttribute('aria-haspopup', 'menu')
    expect(menuButton).toHaveAttribute('data-slot', 'dropdown-menu-trigger')
  })

  it('passes onAuthChange prop correctly', async () => {
    const mockLogout = jest.fn().mockResolvedValue(undefined)
    const mockOnAuthChange = jest.fn()
    mockUseAuth.mockReturnValue({
      user: { email: 'test@example.com', credits: 5, display_name: null },
      logout: mockLogout,
    })

    // Component should render without errors when onAuthChange is provided
    render(<AuthStatus onAuthChange={mockOnAuthChange} />)

    // Wait for component to mount and verify it renders the user menu
    const menuButton = await screen.findByRole('button')
    expect(menuButton).toBeInTheDocument()
  })
})

describe('AuthDialog', () => {
  const mockOnClose = jest.fn()
  const mockOnSuccess = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseAuth.mockReturnValue({
      sendMagicLink: jest.fn(),
      loginWithToken: jest.fn(),
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
  })

  it('renders email step when open', () => {
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    expect(screen.getByText('Sign In')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
  })

  it('does not render when open is false', () => {
    render(<AuthDialog open={false} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    expect(screen.queryByText('Sign In')).not.toBeInTheDocument()
  })

  it('disables Send button when email is empty', () => {
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    const sendButton = screen.getByRole('button', { name: /send sign-in link/i })
    expect(sendButton).toBeDisabled()
  })

  it('enables Send button when email is entered', () => {
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    const input = screen.getByPlaceholderText('you@example.com')
    fireEvent.change(input, { target: { value: 'test@example.com' } })

    const sendButton = screen.getByRole('button', { name: /send sign-in link/i })
    expect(sendButton).not.toBeDisabled()
  })

  it('calls sendMagicLink when form is submitted', async () => {
    const mockSendMagicLink = jest.fn().mockResolvedValue(true)
    mockUseAuth.mockReturnValue({
      sendMagicLink: mockSendMagicLink,
      loginWithToken: jest.fn(),
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    const input = screen.getByPlaceholderText('you@example.com')
    fireEvent.change(input, { target: { value: 'test@example.com' } })

    const sendButton = screen.getByRole('button', { name: /send sign-in link/i })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(mockSendMagicLink).toHaveBeenCalledWith('test@example.com')
    })
  })

  it('shows token step when Use Access Token is clicked', () => {
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    fireEvent.click(screen.getByRole('button', { name: /use access token instead/i }))

    expect(screen.getByText('Access Token')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Enter access token')).toBeInTheDocument()
  })

  it('calls loginWithToken when token is submitted', async () => {
    const mockLoginWithToken = jest.fn().mockResolvedValue(true)
    mockUseAuth.mockReturnValue({
      sendMagicLink: jest.fn(),
      loginWithToken: mockLoginWithToken,
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    // Switch to token step
    fireEvent.click(screen.getByRole('button', { name: /use access token instead/i }))

    const input = screen.getByPlaceholderText('Enter access token')
    fireEvent.change(input, { target: { value: 'my-token' } })

    const authenticateBtn = screen.getByRole('button', { name: /authenticate/i })
    fireEvent.click(authenticateBtn)

    await waitFor(() => {
      expect(mockLoginWithToken).toHaveBeenCalledWith('my-token')
    })
  })

  it('calls onSuccess after successful token login', async () => {
    const mockLoginWithToken = jest.fn().mockResolvedValue(true)
    mockUseAuth.mockReturnValue({
      sendMagicLink: jest.fn(),
      loginWithToken: mockLoginWithToken,
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    // Switch to token step
    fireEvent.click(screen.getByRole('button', { name: /use access token instead/i }))

    const input = screen.getByPlaceholderText('Enter access token')
    fireEvent.change(input, { target: { value: 'my-token' } })

    const authenticateBtn = screen.getByRole('button', { name: /authenticate/i })
    fireEvent.click(authenticateBtn)

    await waitFor(() => {
      expect(mockOnSuccess).toHaveBeenCalled()
    })
  })

  it('shows friendly message when disposable email is rejected', async () => {
    const mockSendMagicLink = jest.fn().mockResolvedValue(false)
    mockUseAuth.mockReturnValue({
      sendMagicLink: mockSendMagicLink,
      loginWithToken: jest.fn(),
      isLoading: false,
      error: 'disposable_email_not_allowed',
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    // Should show the friendly disposable email message
    expect(screen.getByText(/don't support disposable email/i)).toBeInTheDocument()
    expect(screen.getByText(/no spam, no marketing emails/i)).toBeInTheDocument()
    expect(screen.getByText(/your regular email/i)).toBeInTheDocument()
  })

  it('shows generic error for non-disposable errors', () => {
    mockUseAuth.mockReturnValue({
      sendMagicLink: jest.fn(),
      loginWithToken: jest.fn(),
      isLoading: false,
      error: 'Something went wrong',
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    // Should show generic error text, not the disposable email message
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.queryByText(/don't support disposable email/i)).not.toBeInTheDocument()
  })

  it('shows email sent step after successful magic link send', async () => {
    const mockSendMagicLink = jest.fn().mockResolvedValue(true)
    mockUseAuth.mockReturnValue({
      sendMagicLink: mockSendMagicLink,
      loginWithToken: jest.fn(),
      isLoading: false,
      error: null,
      clearError: jest.fn(),
    })
    render(<AuthDialog open={true} onClose={mockOnClose} onSuccess={mockOnSuccess} />)

    const input = screen.getByPlaceholderText('you@example.com')
    fireEvent.change(input, { target: { value: 'test@example.com' } })

    const sendButton = screen.getByRole('button', { name: /send sign-in link/i })
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(screen.getByText('Check Your Email')).toBeInTheDocument()
    })
  })
})
