'use client';

import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';

interface WarmingUpLoaderProps {
  /** Custom class name for the container */
  className?: string;
  /** Custom class name for the spinner */
  spinnerClassName?: string;
  /** Time in ms before showing "warming up" message (default: 5000) */
  warmUpDelay?: number;
  /** Time in ms before showing "almost there" message (default: 15000) */
  almostThereDelay?: number;
}

/**
 * A loading component that gracefully handles cold start delays.
 * Shows increasingly reassuring messages as load time increases.
 */
export function WarmingUpLoader({
  className = '',
  spinnerClassName = 'w-8 h-8',
  warmUpDelay = 5000,
  almostThereDelay = 15000,
}: WarmingUpLoaderProps) {
  const t = useTranslations('warmingUp');
  const [phase, setPhase] = useState<'initial' | 'warming' | 'almost'>('initial');

  useEffect(() => {
    const warmUpTimer = setTimeout(() => {
      setPhase('warming');
    }, warmUpDelay);

    const almostThereTimer = setTimeout(() => {
      setPhase('almost');
    }, almostThereDelay);

    return () => {
      clearTimeout(warmUpTimer);
      clearTimeout(almostThereTimer);
    };
  }, [warmUpDelay, almostThereDelay]);

  return (
    <div className={`flex flex-col items-center justify-center gap-3 ${className}`}>
      <Loader2
        className={`animate-spin ${spinnerClassName}`}
        style={{ color: 'var(--primary)' }}
      />
      {phase === 'warming' && (
        <p className="text-sm animate-fade-in" style={{ color: 'var(--text-muted)' }}>
          {t('title')}
        </p>
      )}
      {phase === 'almost' && (
        <div className="text-center animate-fade-in">
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('almostThere')}</p>
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
            {t('firstLoadNote')}
          </p>
        </div>
      )}
    </div>
  );
}
