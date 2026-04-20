'use client'

import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { installGlobalErrorHandlers } from '@/lib/client-error-setup'

export default function ClientErrorInit() {
  const user = useAuth((s) => s.user)
  useEffect(() => {
    installGlobalErrorHandlers(() => user?.email ?? null)
  }, [user])
  return null
}
