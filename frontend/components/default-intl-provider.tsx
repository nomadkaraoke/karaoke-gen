"use client"

import { NextIntlClientProvider } from "next-intl"
import enMessages from "@/messages/en.json"

/**
 * Provides default English translations for routes outside [locale] (e.g., /admin, /app).
 * The [locale]/layout.tsx provides locale-specific translations that override this.
 */
export function DefaultIntlProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {children}
    </NextIntlClientProvider>
  )
}
