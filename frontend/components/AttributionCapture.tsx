'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { captureAttribution } from '@/lib/attribution';

/** Records first-touch UTM/referrer attribution on any locale page (see lib/attribution.ts). */
export function AttributionCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    captureAttribution();
  }, [searchParams]);

  return null;
}
