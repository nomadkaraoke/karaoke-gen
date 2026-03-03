"use client"

import * as React from "react"
import { useRef, useState, useEffect, useCallback } from "react"
import { Command, CommandEmpty, CommandGroup, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverAnchor } from "@/components/ui/popover"
import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

export interface AutocompleteSuggestion {
  /** Unique key for the suggestion */
  key: string
  /** Primary display text (will be set as the input value on select) */
  label: string
  /** Optional secondary text displayed below the label */
  description?: string
  /** Optional right-aligned text (e.g., popularity score) */
  meta?: string
  /** The full data object from the API */
  data?: any
}

interface AutocompleteInputProps {
  /** Current input value */
  value: string
  /** Called when the input value changes (typing) */
  onChange: (value: string) => void
  /** Called when a suggestion is selected */
  onSelect?: (suggestion: AutocompleteSuggestion) => void
  /** Async function to fetch suggestions for a query */
  fetchSuggestions: (query: string) => Promise<AutocompleteSuggestion[]>
  /** Minimum characters before fetching suggestions */
  minChars?: number
  /** Debounce delay in ms */
  debounceMs?: number
  /** Input placeholder */
  placeholder?: string
  /** Disable the input */
  disabled?: boolean
  /** HTML id for the input */
  id?: string
  /** data-testid for testing */
  "data-testid"?: string
  /** Inline style for the input element */
  style?: React.CSSProperties
  /** Additional className for the input */
  className?: string
}

export function AutocompleteInput({
  value,
  onChange,
  onSelect,
  fetchSuggestions,
  minChars = 2,
  debounceMs = 300,
  placeholder,
  disabled,
  id,
  "data-testid": dataTestId,
  style,
  className,
}: AutocompleteInputProps) {
  const [open, setOpen] = useState(false)
  const [suggestions, setSuggestions] = useState<AutocompleteSuggestion[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Track whether the user just selected a suggestion to avoid re-fetching
  const justSelectedRef = useRef(false)

  const doFetch = useCallback(
    async (query: string) => {
      if (query.length < minChars) {
        setSuggestions([])
        setOpen(false)
        return
      }

      setIsLoading(true)
      try {
        const results = await fetchSuggestions(query)
        setSuggestions(results)
        setOpen(results.length > 0)
      } catch {
        setSuggestions([])
        setOpen(false)
      } finally {
        setIsLoading(false)
      }
    },
    [fetchSuggestions, minChars]
  )

  // Debounced fetch on value change
  useEffect(() => {
    if (justSelectedRef.current) {
      justSelectedRef.current = false
      return
    }

    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
    }

    if (value.length < minChars) {
      setSuggestions([])
      setOpen(false)
      return
    }

    debounceRef.current = setTimeout(() => {
      doFetch(value)
    }, debounceMs)

    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current)
      }
    }
  }, [value, doFetch, debounceMs, minChars])

  function handleSelect(suggestion: AutocompleteSuggestion) {
    justSelectedRef.current = true
    onChange(suggestion.label)
    onSelect?.(suggestion)
    setOpen(false)
    setSuggestions([])
    // Re-focus input after selection
    inputRef.current?.focus()
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    onChange(e.target.value)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // Let cmdk handle arrow keys and enter when popover is open
    if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      // Don't prevent - let the event bubble to Command
      return
    }
    if (e.key === "Escape") {
      setOpen(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="relative">
          <input
            ref={inputRef}
            id={id}
            data-testid={dataTestId}
            type="text"
            value={value}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            onFocus={() => {
              // Re-open if we have suggestions
              if (suggestions.length > 0 && value.length >= minChars) {
                setOpen(true)
              }
            }}
            placeholder={placeholder}
            disabled={disabled}
            style={style}
            className={cn(
              "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
              "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
              className
            )}
            autoComplete="off"
          />
          {isLoading && (
            <Loader2
              className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 animate-spin"
              style={{ color: "var(--text-muted)" }}
            />
          )}
        </div>
      </PopoverAnchor>
      <PopoverContent
        className="p-0 w-[var(--radix-popover-trigger-width)]"
        align="start"
        sideOffset={4}
        onOpenAutoFocus={(e) => {
          // Prevent popover from stealing focus from input
          e.preventDefault()
        }}
      >
        <Command shouldFilter={false}>
          <CommandList>
            <CommandEmpty className="py-3 text-center text-sm" style={{ color: "var(--text-muted)" }}>
              No results found
            </CommandEmpty>
            <CommandGroup>
              {suggestions.map((suggestion) => (
                <CommandItem
                  key={suggestion.key}
                  value={suggestion.key}
                  onSelect={() => handleSelect(suggestion)}
                  className="cursor-pointer"
                >
                  <div className="flex items-center justify-between w-full gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate" style={{ color: "var(--text)" }}>
                        {suggestion.label}
                      </div>
                      {suggestion.description && (
                        <div className="text-xs truncate" style={{ color: "var(--text-muted)" }}>
                          {suggestion.description}
                        </div>
                      )}
                    </div>
                    {suggestion.meta && (
                      <span className="text-xs shrink-0" style={{ color: "var(--text-muted)" }}>
                        {suggestion.meta}
                      </span>
                    )}
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
