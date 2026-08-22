import { render, screen } from "@testing-library/react"
import { LinkifiedText } from "../linkified-text"

describe("LinkifiedText", () => {
  it("renders a URL inside a message as a clickable link", () => {
    render(
      <LinkifiedText text="Download from https://am-dl.pages.dev, then upload the file." />
    )
    const link = screen.getByRole("link", { name: "https://am-dl.pages.dev" })
    expect(link).toHaveAttribute("href", "https://am-dl.pages.dev")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
  })

  it("keeps surrounding text and trailing punctuation as plain text", () => {
    const { container } = render(
      <LinkifiedText text="See https://example.com. Thanks!" />
    )
    // The trailing period must not be part of the href.
    const link = screen.getByRole("link")
    expect(link).toHaveAttribute("href", "https://example.com")
    expect(container.textContent).toBe("See https://example.com. Thanks!")
  })

  it("keeps brackets that are part of the URL path", () => {
    render(
      <LinkifiedText text="See https://en.wikipedia.org/wiki/Foo_(bar) here." />
    )
    const link = screen.getByRole("link")
    expect(link).toHaveAttribute(
      "href",
      "https://en.wikipedia.org/wiki/Foo_(bar)"
    )
  })

  it("renders plain text unchanged when there is no URL", () => {
    render(<LinkifiedText text="Apple Music links can't be downloaded." />)
    expect(screen.queryByRole("link")).toBeNull()
    expect(
      screen.getByText("Apple Music links can't be downloaded.")
    ).toBeInTheDocument()
  })
})
