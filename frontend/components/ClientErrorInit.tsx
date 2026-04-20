'use client'

import { useEffect } from 'react'
import { useAuth } from '@/lib/auth'
import { installGlobalErrorHandlers } from '@/lib/client-error-setup'

interface Props {
  locale: string
}

export default function ClientErrorInit({ locale }: Props) {
  const user = useAuth((s) => s.user)
  useEffect(() => {
    installGlobalErrorHandlers(() => user?.email ?? null, locale)
  }, [locale, user])
  return null
}
