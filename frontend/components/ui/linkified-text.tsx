import React from "react"

// Capturing split so http(s) URLs are preserved as their own array entries.
// The final character class trims trailing sentence punctuation (so
// "see https://x.com." links "https://x.com" and leaves the period as text) but
// deliberately keeps brackets, which are often part of the path (e.g. a
// Wikipedia URL ending in "_(disambiguation)").
const URL_SPLIT_RE = /(https?:\/\/[^\s]+[^\s.,;:!?])/g

/**
 * Render a plain string with any http(s) URLs turned into clickable links.
 * Used for backend-provided messages (e.g. error details) that may suggest a
 * URL the user should open, since those messages are otherwise shown verbatim.
 */
export function LinkifiedText({ text, className }: { text: string; className?: string }) {
  const parts = text.split(URL_SPLIT_RE)
  return (
    <span className={className}>
      {parts.map((part, i) => {
        if (part.startsWith("http://") || part.startsWith("https://")) {
          return (
            <a
              key={i}
              href={part}
              target="_blank"
              rel="noopener noreferrer"
              className="underline break-all hover:text-red-300"
            >
              {part}
            </a>
          )
        }
        return <React.Fragment key={i}>{part}</React.Fragment>
      })}
    </span>
  )
}
