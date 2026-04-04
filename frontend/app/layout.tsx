import type React from "react"
import type { Metadata } from "next"
import localFont from "next/font/local"
import { ThemeProvider } from "@/components/theme-provider"
import { TenantProvider } from "@/components/tenant-provider"
import { ImpersonationBannerWrapper } from "@/components/impersonation-banner-wrapper"
import { ServiceWorkerRegistration } from "@/components/service-worker-registration"
import { GoogleAnalytics } from "@/components/google-analytics"
import "./globals.css"

// AvenirNext Bold - matches the font used in actual Nomad theme title card generation
// (karaoke_gen/resources/AvenirNext-Bold.ttf)
const titleCardFont = localFont({
  src: "../public/fonts/AvenirNext-Bold.ttf",
  display: "swap",
  variable: "--font-title-card",
})

export const metadata: Metadata = {
  title: "Nomad Karaoke: Generator",
  description: "Generate professional karaoke videos with AI-powered vocal separation and synchronized lyrics",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
  manifest: "/manifest.webmanifest",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`font-sans antialiased ${titleCardFont.variable}`} style={{ background: 'var(--bg)', color: 'var(--text)' }}>
        <GoogleAnalytics />
        <ServiceWorkerRegistration />
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          <TenantProvider>
            <ImpersonationBannerWrapper />
            {children}
          </TenantProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
