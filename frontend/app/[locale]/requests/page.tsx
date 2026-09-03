import { Suspense } from 'react';
import { RequestsBoardClient } from './client';

// Public song-request voting board (served at requests.nomadkaraoke.com).
export default function RequestsBoardPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg)' }}>
          <div className="animate-spin rounded-full h-8 w-8 border-b-2" style={{ borderColor: 'var(--accent)' }} />
        </div>
      }
    >
      <RequestsBoardClient />
    </Suspense>
  );
}
