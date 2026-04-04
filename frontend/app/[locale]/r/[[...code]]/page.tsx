import { ReferralInterstitialClient } from './client';

// Required for static export — referral codes are dynamic, rendered client-side
export async function generateStaticParams(): Promise<{ code?: string[] }[]> {
  return [
    { code: undefined }, // /r/ base path
  ];
}

export default function ReferralInterstitialPage() {
  return <ReferralInterstitialClient />;
}
