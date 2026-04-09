'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { QrCode, FileText } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import QRCodeTab, { type QRStylePrefs, DEFAULT_QR_PREFS, loadQrPrefs } from './QRCodeTab';
import FlyerTab from './FlyerTab';

interface ReferralToolsDialogProps {
  referralUrl: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** @deprecated — flyer generation is now client-side. Kept for backwards compat. */
  onGenerateFlyer?: (theme: 'light' | 'dark', qrDataUrl: string) => Promise<Blob>;
  flyerFilename?: string;
  /** Referral code (for flyer CTA display). Extracted from referralUrl if not provided. */
  referralCode?: string;
  /** Discount percentage (for flyer CTA). Defaults to 20. */
  discountPercent?: number;
}

export default function ReferralToolsDialog({
  referralUrl,
  open,
  onOpenChange,
  flyerFilename,
  referralCode: referralCodeProp,
  discountPercent = 20,
}: ReferralToolsDialogProps) {
  const t = useTranslations('referrals');
  const [activeTab, setActiveTab] = useState('qr');
  const [qrPrefs, setQrPrefs] = useState<QRStylePrefs>(DEFAULT_QR_PREFS);

  // Extract referral code from URL if not provided, stripping query/hash/path
  const referralCode = referralCodeProp
    || referralUrl.split('/r/').pop()?.split(/[?#/]/)[0]
    || 'CODE';

  // Load QR prefs on open
  useEffect(() => {
    if (open) {
      setQrPrefs(loadQrPrefs());
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-6xl bg-card border-border max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <QrCode className="w-5 h-5" />
            {t('toolsTitle')}
          </DialogTitle>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value="qr" className="flex items-center gap-1.5">
              <QrCode className="w-3.5 h-3.5" />
              {t('tabQrCode')}
            </TabsTrigger>
            <TabsTrigger value="flyer" className="flex items-center gap-1.5">
              <FileText className="w-3.5 h-3.5" />
              {t('tabFlyer')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="qr">
            <QRCodeTab
              referralUrl={referralUrl}
              prefs={qrPrefs}
              onPrefsChange={setQrPrefs}
              visible={activeTab === 'qr' && open}
            />
          </TabsContent>

          <TabsContent value="flyer">
            <FlyerTab
              referralCode={referralCode}
              discountPercent={discountPercent}
              qrPrefs={qrPrefs}
              onSwitchToQr={() => setActiveTab('qr')}
              flyerFilename={flyerFilename}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
