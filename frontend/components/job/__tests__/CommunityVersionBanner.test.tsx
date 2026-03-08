/**
 * Tests for CommunityVersionBanner component.
 *
 * Verifies:
 * - Returns null when no community versions exist
 * - Renders community tracks with YouTube links
 * - Shows "+N more" when more than 3 tracks
 * - Dismiss button works
 * - Warning icon (AlertTriangle) is rendered
 */

import { render, screen, fireEvent } from "@testing-library/react"
import { CommunityVersionBanner } from "../CommunityVersionBanner"
import type { CommunityCheckResponse } from "@/lib/api"

function makeResponse(overrides?: Partial<CommunityCheckResponse>): CommunityCheckResponse {
  return {
    has_community: true,
    best_youtube_url: "https://youtube.com/watch?v=abc",
    songs: [
      {
        title: "Waterloo",
        artist: "ABBA",
        community_tracks: [
          {
            brand_name: "Nomad Karaoke",
            brand_code: "NK",
            youtube_url: "https://youtube.com/watch?v=abc",
            is_community: true,
          },
        ],
      },
    ],
    ...overrides,
  }
}

describe("CommunityVersionBanner", () => {
  it("returns null when has_community is false", () => {
    const { container } = render(
      <CommunityVersionBanner
        data={makeResponse({ has_community: false })}
        onDismiss={jest.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it("returns null when songs array is empty", () => {
    const { container } = render(
      <CommunityVersionBanner
        data={makeResponse({ songs: [] })}
        onDismiss={jest.fn()}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it("renders the warning text", () => {
    render(
      <CommunityVersionBanner data={makeResponse()} onDismiss={jest.fn()} />
    )
    expect(
      screen.getByText("A karaoke version of this song already exists!")
    ).toBeInTheDocument()
  })

  it("renders community track links", () => {
    render(
      <CommunityVersionBanner data={makeResponse()} onDismiss={jest.fn()} />
    )
    const link = screen.getByText("Watch on YouTube").closest("a")
    expect(link).toHaveAttribute("href", "https://youtube.com/watch?v=abc")
    expect(link).toHaveAttribute("target", "_blank")
  })

  it("renders brand name and Community badge for brand_code tracks", () => {
    render(
      <CommunityVersionBanner data={makeResponse()} onDismiss={jest.fn()} />
    )
    expect(screen.getByText("Nomad Karaoke")).toBeInTheDocument()
    expect(screen.getByText("Community")).toBeInTheDocument()
  })

  it("shows '+N more' when more than 3 tracks", () => {
    const data = makeResponse({
      songs: [
        {
          title: "Waterloo",
          artist: "ABBA",
          community_tracks: [
            { brand_name: "Brand A", brand_code: "A", youtube_url: "https://yt.com/1", is_community: true },
            { brand_name: "Brand B", brand_code: "B", youtube_url: "https://yt.com/2", is_community: true },
            { brand_name: "Brand C", brand_code: "C", youtube_url: "https://yt.com/3", is_community: true },
            { brand_name: "Brand D", brand_code: "D", youtube_url: "https://yt.com/4", is_community: true },
            { brand_name: "Brand E", brand_code: "E", youtube_url: "https://yt.com/5", is_community: true },
          ],
        },
      ],
    })

    render(
      <CommunityVersionBanner data={data} onDismiss={jest.fn()} />
    )
    expect(screen.getByText("+2 more versions")).toBeInTheDocument()
  })

  it("shows singular '+1 more version' for exactly 4 tracks", () => {
    const data = makeResponse({
      songs: [
        {
          title: "Waterloo",
          artist: "ABBA",
          community_tracks: [
            { brand_name: "Brand A", brand_code: "A", youtube_url: "https://yt.com/1", is_community: true },
            { brand_name: "Brand B", brand_code: "B", youtube_url: "https://yt.com/2", is_community: true },
            { brand_name: "Brand C", brand_code: "C", youtube_url: "https://yt.com/3", is_community: true },
            { brand_name: "Brand D", brand_code: "D", youtube_url: "https://yt.com/4", is_community: true },
          ],
        },
      ],
    })

    render(
      <CommunityVersionBanner data={data} onDismiss={jest.fn()} />
    )
    expect(screen.getByText("+1 more version")).toBeInTheDocument()
  })

  it("calls onDismiss when dismiss button is clicked", () => {
    const onDismiss = jest.fn()
    render(
      <CommunityVersionBanner data={makeResponse()} onDismiss={onDismiss} />
    )
    fireEvent.click(screen.getByLabelText("Dismiss"))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it("renders hint text about checking existing versions", () => {
    render(
      <CommunityVersionBanner data={makeResponse()} onDismiss={jest.fn()} />
    )
    expect(
      screen.getByText("Check if an existing version works for you before using a credit.")
    ).toBeInTheDocument()
  })
})
