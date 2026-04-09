'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Download, Pencil } from 'lucide-react';
import type { QRStylePrefs } from './QRCodeTab';
import type { FlyerConfig } from './flyer-presets';
import { DEFAULT_FLYER_CONFIG, loadFlyerConfig, saveFlyerConfig } from './flyer-presets';
import FlyerPreview from './FlyerPreview';
import FlyerControls from './FlyerControls';
import { exportFlyerPdf, exportFlyerHtml, exportFlyerPng } from './FlyerExport';

interface FlyerTabProps {
  referralCode: string;
  discountPercent: number;
  qrPrefs: QRStylePrefs;
  onSwitchToQr: () => void;
  flyerFilename?: string;
}

export default function FlyerTab({ referralCode, discountPercent, qrPrefs, onSwitchToQr, flyerFilename }: FlyerTabProps) {
  const t = useTranslations('referrals');
  const flyerRef = useRef<HTMLDivElement>(null);
  const [config, setConfig] = useState<FlyerConfig>(DEFAULT_FLYER_CONFIG);
  const [exporting, setExporting] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);

  // Load saved config
  useEffect(() => {
    setConfig(loadFlyerConfig());
  }, []);

  // Debounced save
  const updateConfig = useCallback(<K extends keyof FlyerConfig>(key: K, value: FlyerConfig[K]) => {
    setConfig(prev => {
      const next = { ...prev, [key]: value };
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => saveFlyerConfig(next), 300);
      return next;
    });
  }, []);

  const loadPreset = useCallback((presetConfig: FlyerConfig) => {
    // Cancel any pending debounce to avoid overwriting the preset
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    setConfig(presetConfig);
    saveFlyerConfig(presetConfig);
  }, []);

  // Render QR code for flyer using qr-code-styling
  useEffect(() => {
    let cancelled = false;
    const renderQr = async () => {
      const QRCodeStyling = (await import('qr-code-styling')).default;
      if (cancelled) return;

      let image: string | undefined;
      if (qrPrefs.logo === 'nomad') {
        image = '/nomad-karaoke-logo.svg';
      } else if (qrPrefs.logo === 'emoji' && qrPrefs.logoEmoji) {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.font = '52px serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(qrPrefs.logoEmoji, 32, 36);
          image = canvas.toDataURL('image/png');
        }
      }

      const qr = new QRCodeStyling({
        width: 160,
        height: 160,
        data: `https://nomadkaraoke.com/r/${referralCode.toLowerCase()}`,
        dotsOptions: { type: qrPrefs.dotStyle, color: qrPrefs.fgColor },
        cornersSquareOptions: { type: qrPrefs.cornerSquareStyle, color: qrPrefs.fgColor },
        cornersDotOptions: { type: qrPrefs.cornerDotStyle, color: qrPrefs.fgColor },
        backgroundOptions: { color: qrPrefs.bgColor },
        image,
        imageOptions: { hideBackgroundDots: true, imageSize: 0.2, margin: 4 },
      });

      if (cancelled) return;

      const blob = await qr.getRawData('png');
      if (blob && !cancelled) {
        const reader = new FileReader();
        reader.onloadend = () => {
          if (!cancelled) setQrDataUrl(reader.result as string);
        };
        reader.readAsDataURL(blob as Blob);
      }
    };
    renderQr();
    return () => { cancelled = true; };
  }, [qrPrefs, referralCode]);

  // Cleanup debounce
  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const baseFilename = flyerFilename?.replace(/\.[^.]+$/, '') || `nomad-karaoke-flyer-${referralCode}`;

  const handleExport = async (format: 'pdf' | 'html' | 'png') => {
    const el = flyerRef.current;
    if (!el) return;
    setExporting(format);
    try {
      if (format === 'pdf') {
        await exportFlyerPdf(el, {
          perPage: config.perPage,
          marginMm: config.marginMm,
          filename: `${baseFilename}.pdf`,
        });
      } else if (format === 'html') {
        const blob = exportFlyerHtml(el);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${baseFilename}.html`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        await exportFlyerPng(el, `${baseFilename}.png`);
      }
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col md:flex-row gap-6">
        {/* Left: Flyer Preview */}
        <div className="flex-shrink-0 relative w-[240px]">
          <FlyerPreview
            config={config}
            referralCode={referralCode}
            discountPercent={discountPercent}
            qrDataUrl={qrDataUrl}
            flyerRef={flyerRef}
          />
          {/* Edit QR Style link */}
          <button
            onClick={onSwitchToQr}
            className="absolute bottom-2 left-2 text-xs text-primary flex items-center gap-1 hover:underline bg-background/80 rounded px-1.5 py-0.5"
          >
            <Pencil className="w-3 h-3" />
            {t('flyerEditQr')}
          </button>
        </div>

        {/* Right: Controls */}
        <FlyerControls config={config} onChange={updateConfig} onLoadPreset={loadPreset} />
      </div>

      {/* Export buttons */}
      <div className="flex gap-2 pt-2 border-t border-border sm:justify-end">
        <button
          onClick={() => handleExport('pdf')}
          disabled={!!exporting || !qrDataUrl}
          className="flex-1 sm:flex-none px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center justify-center gap-2 disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {exporting === 'pdf' ? t('flyerExporting') : t('flyerDownloadPdf')}
        </button>
        <button
          onClick={() => handleExport('html')}
          disabled={!!exporting || !qrDataUrl}
          className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {t('flyerDownloadHtml')}
        </button>
        <button
          onClick={() => handleExport('png')}
          disabled={!!exporting || !qrDataUrl}
          className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {t('flyerDownloadPng')}
        </button>
      </div>
    </div>
  );
}
