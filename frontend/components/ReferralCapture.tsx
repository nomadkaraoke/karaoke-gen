'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { getReferralCode, setReferralCode } from '@/lib/referral';

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
