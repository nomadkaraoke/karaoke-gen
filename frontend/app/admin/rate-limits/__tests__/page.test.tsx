/**
 * Tests for Admin Rate Limits Page
 *
 * Tests the rate limits admin page functionality including:
 * - Loading states and data fetching
 * - Tab navigation (YouTube Queue, Blocklists)
 * - Blocklist management (add, remove, search)
 * - YouTube queue display
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import AdminRateLimitsPage from "../page"

// Mock the API module
jest.mock("@/lib/api", () => ({
  adminApi: {
    getBlocklists: jest.fn(),
    addDisposableDomain: jest.fn(),
    removeDisposableDomain: jest.fn(),
    addBlockedEmail: jest.fn(),
    removeBlockedEmail: jest.fn(),
    addBlockedIP: jest.fn(),
    removeBlockedIP: jest.fn(),
    syncDisposableDomains: jest.fn(),
    addAllowlistedDomain: jest.fn(),
    removeAllowlistedDomain: jest.fn(),
    getYouTubeQueue: jest.fn(),
    retryYouTubeUpload: jest.fn(),
    processYouTubeQueue: jest.fn(),
    getIpInfo: jest.fn().mockResolvedValue({ status: "success", ip: "192.168.1.100", country_code: "US", isp: "Test ISP" }),
    getIpInfoBatch: jest.fn().mockResolvedValue({}),
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

// Mock data — new data model with external/manual/allowlisted
const mockBlocklists = {
  external_domains: ["tempmail.com", "mailinator.com", "guerrillamail.com"],
  manual_domains: ["custom-spam.com"],
  allowlisted_domains: ["false-positive.com"],
  blocked_emails: ["spammer@example.com", "abuse@test.com"],
  blocked_ips: ["192.168.1.100"],
  last_sync_at: "2025-01-09T10:00:00Z",
  last_sync_count: 4800,
  updated_at: "2025-01-09T10:00:00Z",
  updated_by: "admin@nomadkaraoke.com",
}

const mockYouTubeQueue = {
  entries: [],
  stats: { queued: 0, processing: 0, failed: 0, completed: 0, total: 0 },
}

describe("AdminRateLimitsPage", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockAdminApi.getBlocklists.mockResolvedValue(mockBlocklists)
    mockAdminApi.getYouTubeQueue.mockResolvedValue(mockYouTubeQueue)
  })

  describe("Loading State", () => {
    it("shows loading spinner initially", () => {
      // Don't resolve the promises immediately
      mockAdminApi.getBlocklists.mockImplementation(
        () => new Promise(() => {})
      )
      mockAdminApi.getYouTubeQueue.mockImplementation(
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
      expect(mockAdminApi.getBlocklists).toHaveBeenCalled()
      expect(mockAdminApi.getYouTubeQueue).toHaveBeenCalled()
    })

    it("shows error toast on API failure", async () => {
      mockAdminApi.getBlocklists.mockRejectedValue(
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

    it("YouTube Queue tab is default", async () => {
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      // YouTube Queue should be the default tab
      expect(screen.getByText("YouTube Upload Queue")).toBeInTheDocument()
    })
  })

  describe("Blocklists Tab", () => {
    it("displays external domains", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Should show external domains
      expect(screen.getByText("tempmail.com")).toBeInTheDocument()
      expect(screen.getByText("mailinator.com")).toBeInTheDocument()
    })

    it("displays manual domains", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      expect(screen.getByText("custom-spam.com")).toBeInTheDocument()
    })

    it("filters external domains by search", async () => {
      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Type in external domains search box
      const searchInput = screen.getByPlaceholderText("Search external domains...")
      await user.type(searchInput, "temp")

      // Should show filtered results
      expect(screen.getByText("tempmail.com")).toBeInTheDocument()
    })

    it("adds a manual domain", async () => {
      mockAdminApi.addDisposableDomain.mockResolvedValue({ success: true, message: "Operation successful" })

      const user = userEvent.setup()
      render(<AdminRateLimitsPage />)

      await waitFor(() => {
        expect(screen.getByText("Rate Limits")).toBeInTheDocument()
      })

      await user.click(screen.getByRole("tab", { name: /blocklists/i }))

      // Type new domain in the manual domains add input
      const input = screen.getByPlaceholderText(
        "Add domain (e.g., tempmail.com)"
      )
      await user.type(input, "newspam.com")

      // Click Add button nearest to the input
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
        expect(mockAdminApi.getBlocklists).toHaveBeenCalled()
        expect(mockAdminApi.getYouTubeQueue).toHaveBeenCalled()
      })
    })
  })
})
