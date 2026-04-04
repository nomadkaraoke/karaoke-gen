"use client"

import type React from "react"

import { useEffect } from "react"
import { useRouter } from "@/i18n/routing"
import { useAuth } from "@/lib/auth"

interface ProtectedRouteProps {
  children: React.ReactNode
  requireAdmin?: boolean
}

export function ProtectedRoute({ children, requireAdmin = false }: ProtectedRouteProps) {
  const { user } = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (!user) {
      router.push("/")
    } else if (requireAdmin && user.role !== "admin") {
      router.push("/app")
    }
  }, [user, requireAdmin, router])

  if (!user) return null
  if (requireAdmin && user.role !== "admin") return null

  return <>{children}</>
}
