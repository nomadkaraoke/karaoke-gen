'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { Download } from 'lucide-react';

export type DotStyle = 'square' | 'rounded' | 'dots' | 'classy' | 'classy-rounded' | 'extra-rounded';
export type CornerSquareStyle = 'square' | 'dot' | 'extra-rounded';
export type CornerDotStyle = 'square' | 'dot';
export type LogoOption = 'none' | 'nomad' | 'emoji';

export interface QRStylePrefs {
  dotStyle: DotStyle;
  cornerSquareStyle: CornerSquareStyle;
  cornerDotStyle: CornerDotStyle;
  fgColor: string;
  bgColor: string;
  logo: LogoOption;
  logoEmoji: string;
}

export const DEFAULT_QR_PREFS: QRStylePrefs = {
  dotStyle: 'square',
  cornerSquareStyle: 'square',
  cornerDotStyle: 'square',
  fgColor: '#000000',
  bgColor: '#ffffff',
  logo: 'none',
  logoEmoji: '🎤',
};

const QR_STORAGE_KEY = 'nk-qr-style-prefs';

const DOT_STYLES: DotStyle[] = ['square', 'rounded', 'dots', 'classy', 'classy-rounded', 'extra-rounded'];
const CORNER_SQUARE_STYLES: CornerSquareStyle[] = ['square', 'dot', 'extra-rounded'];
const CORNER_DOT_STYLES: CornerDotStyle[] = ['square', 'dot'];

const DOT_STYLE_LABELS: Record<DotStyle, string> = {
  'square': 'qrStyleSquare',
  'rounded': 'qrStyleRounded',
  'dots': 'qrStyleDots',
  'classy': 'qrStyleClassy',
  'classy-rounded': 'qrStyleClassyRounded',
  'extra-rounded': 'qrStyleExtraRounded',
};

const CORNER_SQUARE_LABELS: Record<CornerSquareStyle, string> = {
  'square': 'qrStyleSquare',
  'dot': 'qrStyleDot',
  'extra-rounded': 'qrStyleExtraRounded',
};

const CORNER_DOT_LABELS: Record<CornerDotStyle, string> = {
  'square': 'qrStyleSquare',
  'dot': 'qrStyleDot',
};

export function loadQrPrefs(): QRStylePrefs {
  try {
    const saved = localStorage.getItem(QR_STORAGE_KEY);
    if (saved) {
      return { ...DEFAULT_QR_PREFS, ...JSON.parse(saved) };
    }
  } catch {}
  return { ...DEFAULT_QR_PREFS };
}

export function saveQrPrefs(prefs: QRStylePrefs): void {
  try {
    localStorage.setItem(QR_STORAGE_KEY, JSON.stringify(prefs));
  } catch {}
}

function emojiToDataUrl(emoji: string): string {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';
  ctx.font = '52px serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(emoji, 32, 36);
  return canvas.toDataURL('image/png');
}

interface QRCodeTabProps {
  referralUrl: string;
  prefs: QRStylePrefs;
  onPrefsChange: (prefs: QRStylePrefs) => void;
  visible: boolean;
}

export default function QRCodeTab({ referralUrl, prefs, onPrefsChange, visible }: QRCodeTabProps) {
  const t = useTranslations('referrals');
  const containerRef = useRef<HTMLDivElement>(null);
  const qrRef = useRef<InstanceType<typeof import('qr-code-styling').default> | null>(null);
  const [loaded, setLoaded] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const buildOptions = useCallback((p: QRStylePrefs) => {
    let image: string | undefined;
    if (p.logo === 'nomad') {
      image = '/nomad-karaoke-logo.svg';
    } else if (p.logo === 'emoji' && p.logoEmoji) {
      image = emojiToDataUrl(p.logoEmoji);
    }
    return {
      width: 250,
      height: 250,
      data: referralUrl,
      dotsOptions: { type: p.dotStyle, color: p.fgColor },
      cornersSquareOptions: { type: p.cornerSquareStyle, color: p.fgColor },
      cornersDotOptions: { type: p.cornerDotStyle, color: p.fgColor },
      backgroundOptions: { color: p.bgColor },
      image,
      imageOptions: { hideBackgroundDots: true, imageSize: 0.2, margin: 4 },
    };
  }, [referralUrl]);

  // Initialize / update QR code
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    const init = async () => {
      const QRCodeStyling = (await import('qr-code-styling')).default;
      if (cancelled) return;
      if (!qrRef.current) {
        qrRef.current = new QRCodeStyling(buildOptions(prefs));
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = '';
          qrRef.current.append(containerRef.current);
        }
      } else if (!cancelled) {
        qrRef.current.update(buildOptions(prefs));
      }
      if (!cancelled) setLoaded(true);
    };
    init();
    return () => { cancelled = true; };
  }, [visible, prefs, buildOptions]);

  // Clean up on hide
  useEffect(() => {
    if (!visible) {
      qrRef.current = null;
      setLoaded(false);
    }
  }, [visible]);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const updatePref = <K extends keyof QRStylePrefs>(key: K, value: QRStylePrefs[K]) => {
    const next = { ...prefs, [key]: value };
    onPrefsChange(next);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => saveQrPrefs(next), 300);
  };

  const handleDownload = async (extension: 'png' | 'svg') => {
    if (!qrRef.current) {
      const QRCodeStyling = (await import('qr-code-styling')).default;
      qrRef.current = new QRCodeStyling(buildOptions(prefs));
    }
    await qrRef.current.download({ name: 'referral-qr', extension });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col md:flex-row gap-6">
        {/* Left: QR Preview */}
        <div className="flex-shrink-0 flex items-center justify-center">
          <div
            className="relative w-[250px] h-[250px] rounded-lg border border-border"
            style={{ backgroundColor: prefs.bgColor }}
          >
            <div ref={containerRef} className="w-full h-full" />
            {!loaded && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="animate-pulse w-[200px] h-[200px] bg-muted rounded" />
              </div>
            )}
          </div>
        </div>

        {/* Right: Controls */}
        <div className="flex-1 space-y-4 min-w-0">
          {/* Dot Style */}
          <div>
            <label className="text-sm font-medium text-foreground">{t('qrDotStyle')}</label>
            <div className="grid grid-cols-3 gap-1.5 mt-1">
              {DOT_STYLES.map(style => (
                <button
                  key={style}
                  onClick={() => updatePref('dotStyle', style)}
                  className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                    prefs.dotStyle === style
                      ? 'border-primary bg-primary/10 text-primary font-medium'
                      : 'border-border text-muted-foreground hover:border-primary/50'
                  }`}
                >
                  {t(DOT_STYLE_LABELS[style])}
                </button>
              ))}
            </div>
          </div>

          {/* Corner Frame */}
          <div>
            <label className="text-sm font-medium text-foreground">{t('qrCornerFrame')}</label>
            <div className="grid grid-cols-3 gap-1.5 mt-1">
              {CORNER_SQUARE_STYLES.map(style => (
                <button
                  key={style}
                  onClick={() => updatePref('cornerSquareStyle', style)}
                  className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                    prefs.cornerSquareStyle === style
                      ? 'border-primary bg-primary/10 text-primary font-medium'
                      : 'border-border text-muted-foreground hover:border-primary/50'
                  }`}
                >
                  {t(CORNER_SQUARE_LABELS[style])}
                </button>
              ))}
            </div>
          </div>

          {/* Corner Dot */}
          <div>
            <label className="text-sm font-medium text-foreground">{t('qrCornerDot')}</label>
            <div className="grid grid-cols-3 gap-1.5 mt-1">
              {CORNER_DOT_STYLES.map(style => (
                <button
                  key={style}
                  onClick={() => updatePref('cornerDotStyle', style)}
                  className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                    prefs.cornerDotStyle === style
                      ? 'border-primary bg-primary/10 text-primary font-medium'
                      : 'border-border text-muted-foreground hover:border-primary/50'
                  }`}
                >
                  {t(CORNER_DOT_LABELS[style])}
                </button>
              ))}
            </div>
          </div>

          {/* Colors */}
          <div>
            <label className="text-sm font-medium text-foreground">{t('qrColors')}</label>
            <div className="flex gap-4 mt-1">
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={prefs.fgColor}
                  onChange={(e) => updatePref('fgColor', e.target.value)}
                  className="w-8 h-8 rounded border border-border cursor-pointer"
                />
                <span className="text-xs text-muted-foreground">{t('qrForeground')}</span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={prefs.bgColor}
                  onChange={(e) => updatePref('bgColor', e.target.value)}
                  className="w-8 h-8 rounded border border-border cursor-pointer"
                />
                <span className="text-xs text-muted-foreground">{t('qrBackground')}</span>
              </div>
            </div>
          </div>

          {/* Center Logo */}
          <div>
            <label className="text-sm font-medium text-foreground">{t('qrLogo')}</label>
            <div className="flex gap-1.5 mt-1">
              {(['none', 'nomad', 'emoji'] as const).map(option => {
                const labelKeys = { none: 'qrLogoNone', nomad: 'qrLogoNomad', emoji: 'qrLogoEmoji' } as const;
                return (
                  <button
                    key={option}
                    onClick={() => updatePref('logo', option)}
                    className={`px-2 py-1.5 text-xs rounded border transition-colors ${
                      prefs.logo === option
                        ? 'border-primary bg-primary/10 text-primary font-medium'
                        : 'border-border text-muted-foreground hover:border-primary/50'
                    }`}
                  >
                    {t(labelKeys[option])}
                  </button>
                );
              })}
              {prefs.logo === 'emoji' && (
                <input
                  type="text"
                  value={prefs.logoEmoji}
                  onChange={(e) => {
                    const val = e.target.value;
                    const emoji = [...val].pop() || '';
                    updatePref('logoEmoji', emoji);
                  }}
                  className="w-10 h-7 text-center text-base rounded border border-border"
                  maxLength={4}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* QR download buttons */}
      <div className="flex gap-2 pt-2 border-t border-border sm:justify-end">
        <button
          onClick={() => handleDownload('png')}
          className="flex-1 sm:flex-none px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center justify-center gap-2"
        >
          <Download className="w-4 h-4" />
          {t('qrDownloadPng')}
        </button>
        <button
          onClick={() => handleDownload('svg')}
          className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary"
        >
          <Download className="w-4 h-4" />
          {t('qrDownloadSvg')}
        </button>
      </div>
    </div>
  );
}
