'use client';

import { useState } from 'react';
import { Bell, X, Smartphone, Share } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePushNotifications } from '@/hooks/use-push-notifications';
import { useTranslations } from 'next-intl';

/**
 * Soft-ask banner prompting users to enable push notifications.
 * Shows different UI for iOS (Add to Home Screen instructions) vs other browsers.
 */
export function PushNotificationPrompt() {
  const t = useTranslations('push');
  const {
    isLoading,
    isPushEnabled,
    shouldShowPrompt,
    needsIOSInstall,
    isIOS,
    isPWAInstalled,
    permission,
    subscribe,
    dismissPrompt,
    error,
  } = usePushNotifications();

  const [isSubscribing, setIsSubscribing] = useState(false);

  // Don't render if loading, not enabled, or shouldn't show
  if (isLoading || !isPushEnabled) {
    return null;
  }

  // Show iOS install instructions if on iOS but not installed as PWA
  if (needsIOSInstall) {
    return (
      <div className="bg-blue-500/10 border border-blue-500/20 text-blue-400 px-4 py-3 rounded-lg">
        <div className="flex items-start gap-3">
          <Smartphone className="h-5 w-5 mt-0.5 flex-shrink-0" />
          <div className="flex-1">
            <p className="font-medium mb-1">{t('iosTitle')}</p>
            <p className="text-sm text-[var(--text-muted)] mb-3">
              {t('iosInstructions')}
            </p>
            <ol className="text-sm text-[var(--text-muted)] space-y-2 mb-3">
              <li className="flex items-center gap-2">
                <span className="bg-blue-500/20 rounded-full w-5 h-5 flex items-center justify-center text-xs">1</span>
                <span>{t('iosStep1')}</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="bg-blue-500/20 rounded-full w-5 h-5 flex items-center justify-center text-xs">2</span>
                <span>{t('iosStep2')}</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="bg-blue-500/20 rounded-full w-5 h-5 flex items-center justify-center text-xs">3</span>
                <span>{t('iosStep3')}</span>
              </li>
            </ol>
            <p className="text-xs text-[var(--text-muted)]">
              {t('iosNote')}
            </p>
          </div>
          <button
            onClick={() => dismissPrompt(false)}
            className="text-[var(--text-muted)] hover:text-[var(--text)]"
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    );
  }

  // Show regular prompt for other browsers (or iOS after install)
  if (!shouldShowPrompt) {
    // Don't show if already subscribed, permission denied, or dismissed
    return null;
  }

  const handleEnable = async () => {
    setIsSubscribing(true);
    try {
      await subscribe();
    } finally {
      setIsSubscribing(false);
    }
  };

  const handleDismiss = () => {
    dismissPrompt(false); // Temporary dismiss (will show again in 7 days)
  };

  const handleNeverAsk = () => {
    dismissPrompt(true); // Permanent dismiss
  };

  return (
    <div className="bg-purple-500/10 border border-purple-500/20 text-purple-300 px-4 py-3 rounded-lg">
      <div className="flex items-start gap-3">
        <Bell className="h-5 w-5 mt-0.5 flex-shrink-0" />
        <div className="flex-1">
          <p className="font-medium mb-1">{t('promptTitle')}</p>
          <p className="text-sm text-[var(--text-muted)] mb-3">
            {t('promptDescription')}
          </p>
          {error && (
            <p className="text-sm text-red-400 mb-3">{error}</p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              onClick={handleEnable}
              size="sm"
              disabled={isSubscribing}
              className="bg-purple-600 hover:bg-purple-700 text-white"
            >
              {isSubscribing ? t('enabling') : t('enable')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDismiss}
              className="text-[var(--text-muted)] hover:text-[var(--text)]"
            >
              {t('notNow')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleNeverAsk}
              className="text-[var(--text-muted)] hover:text-[var(--text)] text-xs"
            >
              {t('dontAskAgain')}
            </Button>
          </div>
        </div>
        <button
          onClick={handleDismiss}
          className="text-[var(--text-muted)] hover:text-[var(--text)]"
          aria-label="Dismiss"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/**
 * Compact version of the prompt for use in menus/settings.
 */
export function PushNotificationToggle() {
  const t = useTranslations('push');
  const {
    isLoading,
    isPushEnabled,
    isSubscribed,
    permission,
    subscribe,
    unsubscribe,
    error,
  } = usePushNotifications();

  const [isProcessing, setIsProcessing] = useState(false);

  if (isLoading || !isPushEnabled) {
    return null;
  }

  const handleToggle = async () => {
    setIsProcessing(true);
    try {
      if (isSubscribed) {
        await unsubscribe();
      } else {
        await subscribe();
      }
    } finally {
      setIsProcessing(false);
    }
  };

  // Permission was denied - show disabled state
  if (permission === 'denied') {
    return (
      <div className="flex items-center justify-between text-sm">
        <div className="flex items-center gap-2 text-[var(--text-muted)]">
          <Bell className="h-4 w-4" />
          <span>{t('settingsTitle')}</span>
        </div>
        <span className="text-xs text-[var(--text-muted)]">{t('blockedInBrowser')}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <Bell className="h-4 w-4 text-[var(--text-muted)]" />
        <span className="text-sm">{t('settingsTitle')}</span>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={handleToggle}
        disabled={isProcessing}
        className="h-7 text-xs"
      >
        {isProcessing ? '...' : isSubscribed ? t('disableOnDevice') : t('enableNotifications')}
      </Button>
    </div>
  );
}
