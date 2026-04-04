"use client"

import React, { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { getAccessToken } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useAdminSettings } from "@/lib/admin-settings"
import { Loader2 } from "lucide-react"
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { AdminSidebar } from "@/components/admin/admin-sidebar"
import { Separator } from "@/components/ui/separator"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { usePathname } from "next/navigation"

function AdminBreadcrumb() {
  const pathname = usePathname()

  const getBreadcrumbs = () => {
    const segments = pathname.split("/").filter(Boolean)
    const breadcrumbs: { label: string; href?: string }[] = []

    // Always start with Admin
    if (segments[0] === "admin") {
      if (segments.length === 1) {
        breadcrumbs.push({ label: "Dashboard" })
      } else {
        breadcrumbs.push({ label: "Admin", href: "/admin" })

        if (segments[1] === "users") {
          if (segments.length === 2) {
            breadcrumbs.push({ label: "Users" })
          } else {
            breadcrumbs.push({ label: "Users", href: "/admin/users" })
            breadcrumbs.push({ label: decodeURIComponent(segments[2]) })
          }
        } else if (segments[1] === "jobs") {
          if (segments.length === 2) {
            breadcrumbs.push({ label: "Jobs" })
          } else if (segments[2]) {
            breadcrumbs.push({ label: "Jobs", href: "/admin/jobs" })
            breadcrumbs.push({ label: decodeURIComponent(segments[2]) })
          }
        } else if (segments[1] === "payments") {
          breadcrumbs.push({ label: "Payments" })
        } else if (segments[1] === "referrals") {
          breadcrumbs.push({ label: "Referrals" })
        } else if (segments[1] === "searches") {
          breadcrumbs.push({ label: "Audio Searches" })
        } else if (segments[1] === "payments") {
          breadcrumbs.push({ label: "Payments" })
        } else if (segments[1] === "rate-limits") {
          breadcrumbs.push({ label: "Rate Limits" })
        } else if (segments[1] === "abuse") {
          breadcrumbs.push({ label: "Abuse Investigation" })
        }
      }
    }

    return breadcrumbs
  }

  const breadcrumbs = getBreadcrumbs()

  return (
    <Breadcrumb>
      <BreadcrumbList>
        {breadcrumbs.map((crumb, index) => (
          <React.Fragment key={`${crumb.href || ''}-${crumb.label}`}>
            {index > 0 && <BreadcrumbSeparator />}
            <BreadcrumbItem>
              {crumb.href ? (
                <BreadcrumbLink href={crumb.href}>{crumb.label}</BreadcrumbLink>
              ) : (
                <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
              )}
            </BreadcrumbItem>
          </React.Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  )
}

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const router = useRouter()
  const { user } = useAuth()
  const [isChecking, setIsChecking] = useState(true)
  // IMPORTANT: All hooks must be called before any conditional returns
  // to satisfy React's Rules of Hooks
  const { showTestData, setShowTestData } = useAdminSettings()

  useEffect(() => {
    const token = getAccessToken()
    if (!token) {
      router.replace("/")
      return
    }

    // Check if user is admin (either by role or @nomadkaraoke.com email)
    if (user) {
      const isAdmin = user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")
      if (!isAdmin) {
        router.replace("/app")
        return
      }
    }

    // If we have a token but no user yet, wait for auth to load
    if (!user) {
      return
    }

    setIsChecking(false)
  }, [router, user])

  // Show loading while checking auth
  if (isChecking || !user) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ background: "var(--bg)" }}
      >
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--text-muted)" }} />
      </div>
    )
  }

  return (
    <SidebarProvider>
      <AdminSidebar />
      <SidebarInset>
        <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <AdminBreadcrumb />
          <div className="ml-auto flex items-center gap-2">
            <Switch
              id="show-test-data"
              checked={showTestData}
              onCheckedChange={setShowTestData}
            />
            <Label htmlFor="show-test-data" className="text-sm text-muted-foreground cursor-pointer">
              Show test data
            </Label>
          </div>
        </header>
        <main className="flex-1 p-4 md:p-6 min-w-0 overflow-hidden">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  )
}
