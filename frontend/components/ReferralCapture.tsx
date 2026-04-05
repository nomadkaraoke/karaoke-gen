'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { getReferralCode, setReferralCode } from '@/lib/referral';

/**
 * Captures ?ref=CODE query param and stores it as a referral cookie.
 * This is the secondary capture mechanism — the primary is the /r/CODE
 * interstitial page which stores the code before redirecting here.
 */
export function ReferralCapture() {
  const searchParams = useSearchParams();

  useEffect(() => {
    const ref = searchParams.get('ref');
    if (ref && !getReferralCode()) {
      setReferralCode(ref);
    }
  }, [searchParams]);

  return null;
}
