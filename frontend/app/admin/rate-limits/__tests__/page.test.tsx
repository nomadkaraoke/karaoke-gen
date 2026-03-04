/**
 * Tests for Admin Rate Limits Page
 *
 * Tests the rate limits admin page functionality including:
 * - Loading states and data fetching
 * - Tab navigation (Overview, Blocklists, User Overrides)
 * - Blocklist management (add, remove, search)
 * - User override management (add, remove)
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import AdminRateLimitsPage from "../page"

// Mock the API module
jest.mock("@/lib/api", () => ({
  adminApi: {
    getRateLimitStats: jest.fn(),
    getBlocklists: jest.fn(),
    getUserOverrides: jest.fn(),
    addDisposableDomain: jest.fn(),
    removeDisposableDomain: jest.fn(),
    addBlockedEmail: jest.fn(),
    removeBlockedEmail: jest.fn(),
    addBlockedIP: jest.fn(),
    removeBlockedIP: jest.fn(),
    setUserOverride: jest.fn(),
    removeUserOverride: jest.fn(),
    getYouTubeQueue: jest.fn(),
    retryYouTubeUpload: jest.fn(),
    processYouTubeQueue: jest.fn(),
  },
}))

// Mock the toast hook
const mockToast = jest.fn()
jest.mock("@/hooks/use-toast", () => ({
  useToast: () => ({
    toast: mockToast,
  }),
}))

// Import the mocked module
import { adminApi } from "@/lib/api"

const mockAdminApi = adminApi as jest.Mocked<typeof adminApi>

// Mock data
const mockStats = {
  jobs_per_day_limit: 5,
  rate_limiting_enabled: true,
  youtube_uploads_today: 3,
  youtube_quota_units_consumed: 900,
  youtube_quota_units_remaining: 8600,
  youtube_quota_daily_limit: 10000,
  youtube_quota_effective_limit: 9500,
  youtube_quota_upload_cost: 300,
  youtube_quota_estimated_uploads_remaining: 28,
  youtube_quota_seconds_until_reset: 43200,
  youtube_quota_gcp_usage: 600,
  youtube_quota_pending_units: 300,
  youtube_uploads_queued: 0,
  youtube_uploads_failed: 0,
  disposable_domains_count: 130,
  blocked_emails_count: 2,
  blocked_ips_count: 1,
  total_overrides: 1,
}

const mockBlocklists = {
  disposable_domains: ["tempmail.com", "mailinator.com", "guerrillamail.com"],
  blocked_emails: ["spammer@example.com", "abuse@test.com"],
  blocked_ips: ["192.168.1.100"],
  updated_at: "2025-01-09T10:00:00Z",
  updated_by: "admin@nomadkaraoke.com",
}

const mockOverrides = {
  overrides: [
    {
      email: "vip@example.com",
      bypass_job_limit: true,
      custom_daily_job_limit: undefined,
      reason: "VIP user",
      created_by: "admin@nomadkaraoke.com",
      created_at: "2025-01-08T15:00:00Z",
    },
  ],
  total: 1,
}

const mockYouTubeQueue = {
  entries: [],
  total: 0,
}

describe("AdminRateLimitsPage", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockAdminApi.getRateLimitStats.mockResolvedValue(mockStats)
    mockAdminApi.getBlocklists.mockResolvedValue(mockBlocklists)
    mockAdminApi.getUserOverrides.mockResolvedValue(mockOverrides)
    mockAdminApi.getYouTubeQueue.mockResolvedValue(mockYouTubeQueue)
  })

  describe("Loading State", () => {
    it("shows loading spinner initially", () => {
      // Don't resolve the promises immediately
      mockAdminApi.getRateLimitStats.mockImplementation(
        () => new Promise(() => {})
      )
      mockAdminApi.getBlocklists.mockImplementation(
        () => new Promise(() => {})
      )
      mockAdminApi.getUserOverrides.mockImplementation(
        () => new Promise(() => {})
      )

      render(<AdminRateLimitsPage />)

      // Should show loading spinner (check for the animate-spin class on the SVG)
      const spinner = document.querySelector(".animate-spin")
      expect(spinner).toBeInTheDocument()
    })

    it("loads and displays data", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      // Verify API calls were made
      expect(mockAdminApi.getRateLimitStats).toHaveBeenCalled()
      expect(mockAdminApi.getBlocklists).toHaveBeenCalled()
      expect(mockAdminApi.getUserOverrides).toHaveBeenCalled()
    })

    it("shows error toast on API failure", async () => {
      mockAdminApi.getRateLimitStats.mockRejectedValue(
        new Error("Network error")
      )

      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(mockToast).toHaveBeenCalledWith(
          expect.objectContaining({
            title: "Error",
            variant: "destructive",
          })
        )
      })
    })
  })

  describe("Overview Tab", () => {
    it("displays rate limit configuration stats", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limit Configuration")).toBeInTheDocument()
      })

      // Check that stats are displayed
      expect(screen.getByText("Jobs Per Day")).toBeInTheDocument()
      expect(screen.getByText("5")).toBeInTheDocument()
    })

    it("displays YouTube API quota section", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("YouTube API Quota")).toBeInTheDocument()
      })

      expect(screen.getByText("Uploads Today")).toBeInTheDocument()
      expect(screen.getByText("3")).toBeInTheDocument()
    })

    it("displays GCP + Pending quota breakdown", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("YouTube API Quota")).toBeInTheDocument()
      })

      // Should show the GCP + Pending breakdown line
      expect(screen.getByText(/GCP: 600/)).toBeInTheDocument()
      expect(screen.getByText(/Pending: 300/)).toBeInTheDocument()
    })

    it("displays rate limiting status badge", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Enabled")).toBeInTheDocument()
      })
    })
  })

  describe("Tab Navigation", () => {
    it("switches to Blocklists tab", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      // Click Blocklists tab
      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Should show blocklist content
      expect(screen.getByText("Disposable Email Domains")).toBeInTheDocument()
    })

    it("switches to User Overrides tab", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      // Click User Overrides tab
      await user.click(screen.getByRole("tab", { name: /user overrides/i }))

      // Should show overrides content
      expect(
        screen.getByText("Users with custom rate limits or bypass permissions")
      ).toBeInTheDocument()
    })
  })

  describe("Blocklists Tab", () => {
    it("displays disposable domains list", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Should show domains
      expect(screen.getByText("tempmail.com")).toBeInTheDocument()
      expect(screen.getByText("mailinator.com")).toBeInTheDocument()
    })

    it("filters domains by search", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Type in search box
      const searchInput = screen.getByPlaceholderText("Search domains...")
      await user.type(searchInput, "temp")

      // Should show filtered results
      expect(screen.getByText("tempmail.com")).toBeInTheDocument()
      // mailinator should not match "temp" filter
    })

    it("adds a disposable domain", async () => {
      mockAdminApi.addDisposableDomain.mockResolvedValue({ success: true, message: "Operation successful" })

      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Type new domain
      const input = screen.getByPlaceholderText(
        "Add domain (e.g., tempmail.com)"
      )
      await user.type(input, "newspam.com")

      // Click Add button
      const addButton = screen
        .getAllByRole("button", { name: /add/i })
        .find((btn) => btn.textContent?.includes("Add"))!
      await user.click(addButton)

      await waitFor(() => {
        expect(mockAdminApi.addDisposableDomain).toHaveBeenCalledWith(
          "newspam.com"
        )
      })
    })

    it("displays blocked emails", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      expect(screen.getByText("spammer@example.com")).toBeInTheDocument()
    })

    it("displays blocked IPs", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      expect(screen.getByText("192.168.1.100")).toBeInTheDocument()
    })
  })

  describe("User Overrides Tab", () => {
    it("displays existing overrides", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /user overrides/i }))

      // Should show override in table
      expect(screen.getByText("vip@example.com")).toBeInTheDocument()
      expect(screen.getByText("VIP user")).toBeInTheDocument()
      expect(screen.getByText("Bypass All")).toBeInTheDocument()
    })

    it("opens add override dialog", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /user overrides/i }))

      // Click Add Override button
      await user.click(screen.getByRole("button", { name: /add override/i }))

      // Should show dialog
      expect(screen.getByText("Add User Override")).toBeInTheDocument()
      expect(
        screen.getByText(
          "Grant a user custom rate limits or bypass permissions"
        )
      ).toBeInTheDocument()
    })

    it("creates a new override", async () => {
      mockAdminApi.setUserOverride.mockResolvedValue({ success: true, message: "Operation successful" })

      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /user overrides/i }))

      // Open dialog
      await user.click(screen.getByRole("button", { name: /add override/i }))

      // Fill form
      const emailInput = screen.getByPlaceholderText("user@example.com")
      await user.type(emailInput, "newuser@example.com")

      const reasonInput = screen.getByPlaceholderText(
        "Reason for this override..."
      )
      await user.type(reasonInput, "Test user needs extra access")

      // Enable bypass
      const bypassSwitch = screen.getByRole("switch")
      await user.click(bypassSwitch)

      // Submit
      const submitButton = screen
        .getAllByRole("button", { name: /add override/i })
        .find((btn) => btn.closest("[role='dialog']"))!
      await user.click(submitButton)

      await waitFor(() => {
        expect(mockAdminApi.setUserOverride).toHaveBeenCalledWith(
          "newuser@example.com",
          expect.objectContaining({
            bypass_job_limit: true,
            reason: "Test user needs extra access",
          })
        )
      })
    })

    it("shows empty state when no overrides", async () => {
      mockAdminApi.getUserOverrides.mockResolvedValue({ overrides: [], total: 0 })

      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /user overrides/i }))

      expect(screen.getByText("No user overrides configured")).toBeInTheDocument()
    })
  })

  describe("Refresh Button", () => {
    it("reloads data when clicked", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      // Clear previous calls
      jest.clearAllMocks()

      // Click refresh
      await user.click(screen.getByRole("button", { name: /refresh/i }))

      await waitFor(() => {
        expect(mockAdminApi.getRateLimitStats).toHaveBeenCalled()
        expect(mockAdminApi.getBlocklists).toHaveBeenCalled()
        expect(mockAdminApi.getUserOverrides).toHaveBeenCalled()
      })
    })
  })
})
