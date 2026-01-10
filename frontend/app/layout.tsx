import type React from "react"
import type { Metadata } from "next"
import { ThemeProvider } from "@/components/theme-provider"
import { TenantProvider } from "@/components/tenant-provider"
import { ImpersonationBannerWrapper } from "@/components/impersonation-banner-wrapper"
import { ServiceWorkerRegistration } from "@/components/service-worker-registration"
import "./globals.css"

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
      <body className="font-sans antialiased" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
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
