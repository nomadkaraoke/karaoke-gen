'use client';

import { useState, useEffect } from 'react';
import { Bell, Smartphone, Monitor, Trash2, RefreshCw, Check, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePushNotifications } from '@/hooks/use-push-notifications';
import { api, PushSubscriptionInfo } from '@/lib/api';

/**
 * Full settings panel for managing push notification subscriptions.
 * Shows list of subscribed devices and allows enabling/disabling.
 */
export function PushSettings() {
  const {
    isLoading: hookLoading,
    isPushEnabled,
    isSubscribed,
    permission,
    subscribe,
    unsubscribe,
    error: hookError,
  } = usePushNotifications();

  const [subscriptions, setSubscriptions] = useState<PushSubscriptionInfo[]>([]);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load subscriptions list
  const loadSubscriptions = async () => {
    setIsLoadingList(true);
    setError(null);
    try {
      const response = await api.listPushSubscriptions();
      setSubscriptions(response.subscriptions);
    } catch (err) {
      console.error('Failed to load subscriptions:', err);
      setError('Failed to load subscriptions');
    } finally {
      setIsLoadingList(false);
    }
  };

  // Load subscriptions when component mounts (if authenticated)
  useEffect(() => {
    if (!hookLoading && isPushEnabled) {
      loadSubscriptions();
    }
  }, [hookLoading, isPushEnabled]);

  // Handle enabling notifications
  const handleEnable = async () => {
    setIsProcessing(true);
    setError(null);
    try {
      const success = await subscribe();
      if (success) {
        // Reload list to show new subscription
        await loadSubscriptions();
      }
    } finally {
      setIsProcessing(false);
    }
  };

  // Handle disabling notifications (unsubscribe current device)
  const handleDisable = async () => {
    setIsProcessing(true);
    setError(null);
    try {
      const success = await unsubscribe();
      if (success) {
        // Reload list
        await loadSubscriptions();
      }
    } finally {
      setIsProcessing(false);
    }
  };

  // Handle removing a specific subscription (admin feature - remove other devices)
  const handleRemoveSubscription = async (endpoint: string) => {
    setError(null);
    try {
      await api.unsubscribePush(endpoint);
      // Reload list
      await loadSubscriptions();
    } catch (err) {
      console.error('Failed to remove subscription:', err);
      setError('Failed to remove subscription');
    }
  };

  // Get icon for device type based on name
  const getDeviceIcon = (deviceName: string | null) => {
    if (!deviceName) return <Monitor className="h-4 w-4" />;

    const name = deviceName.toLowerCase();
    if (name.includes('phone') || name.includes('iphone') || name.includes('android')) {
      return <Smartphone className="h-4 w-4" />;
    }
    return <Monitor className="h-4 w-4" />;
  };

  // Format date for display
  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return 'Unknown';
    try {
      return new Date(dateStr).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      });
    } catch {
      return 'Unknown';
    }
  };

  if (hookLoading) {
    return (
      <div className="p-4 text-center text-[var(--text-muted)]">
        Loading...
      </div>
    );
  }

  if (!isPushEnabled) {
    return (
      <div className="p-4 bg-[var(--card)] rounded-lg border border-[var(--card-border)]">
        <div className="flex items-center gap-2 text-[var(--text-muted)]">
          <Bell className="h-5 w-5" />
          <span>Push notifications are not available on this server.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Status Section */}
      <div className="p-4 bg-[var(--card)] rounded-lg border border-[var(--card-border)]">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Bell className="h-5 w-5" />
            <h3 className="font-medium">Push Notifications</h3>
          </div>
          <div className="flex items-center gap-2">
            {isSubscribed ? (
              <span className="flex items-center gap-1 text-sm text-green-400">
                <Check className="h-4 w-4" />
                Enabled
              </span>
            ) : permission === 'denied' ? (
              <span className="flex items-center gap-1 text-sm text-red-400">
                <X className="h-4 w-4" />
                Blocked
              </span>
            ) : (
              <span className="text-sm text-[var(--text-muted)]">Disabled</span>
            )}
          </div>
        </div>

        {permission === 'denied' ? (
          <div className="text-sm text-[var(--text-muted)] mb-4">
            Notifications are blocked in your browser. To enable them, you&apos;ll need to allow notifications in your browser settings.
          </div>
        ) : (
          <div className="flex gap-2">
            {isSubscribed ? (
              <Button
                variant="outline"
                size="sm"
                onClick={handleDisable}
                disabled={isProcessing}
              >
                {isProcessing ? 'Processing...' : 'Disable on this device'}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={handleEnable}
                disabled={isProcessing}
              >
                {isProcessing ? 'Processing...' : 'Enable notifications'}
              </Button>
            )}
          </div>
        )}

        {(hookError || error) && (
          <p className="text-sm text-red-400 mt-2">{hookError || error}</p>
        )}
      </div>

      {/* Subscriptions List */}
      <div className="p-4 bg-[var(--card)] rounded-lg border border-[var(--card-border)]">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium">Subscribed Devices</h3>
          <Button
            variant="ghost"
            size="sm"
            onClick={loadSubscriptions}
            disabled={isLoadingList}
            className="h-8 w-8 p-0"
            title="Refresh"
          >
            <RefreshCw className={`h-4 w-4 ${isLoadingList ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {isLoadingList ? (
          <div className="text-sm text-[var(--text-muted)]">Loading...</div>
        ) : subscriptions.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">
            No devices are subscribed to push notifications.
          </div>
        ) : (
          <ul className="space-y-2">
            {subscriptions.map((sub, index) => (
              <li
                key={sub.endpoint}
                className="flex items-center justify-between p-2 bg-[var(--secondary)] rounded"
              >
                <div className="flex items-center gap-3">
                  {getDeviceIcon(sub.device_name)}
                  <div>
                    <div className="text-sm font-medium">
                      {sub.device_name || `Device ${index + 1}`}
                    </div>
                    <div className="text-xs text-[var(--text-muted)]">
                      Added {formatDate(sub.created_at)}
                      {sub.last_used_at && ` • Last used ${formatDate(sub.last_used_at)}`}
                    </div>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRemoveSubscription(sub.endpoint)}
                  className="h-8 w-8 p-0 text-[var(--text-muted)] hover:text-red-400"
                  title="Remove this device"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}

        <p className="text-xs text-[var(--text-muted)] mt-4">
          You can have up to 5 devices subscribed. Adding more will remove the oldest subscription.
        </p>
      </div>
    </div>
  );
}
