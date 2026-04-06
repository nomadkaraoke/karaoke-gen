'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { QrCode, Download, Mic, Music } from 'lucide-react';
import { generateFlyer } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';

const STORAGE_KEY = 'nk-qr-style-prefs';

type DotStyle = 'square' | 'rounded' | 'dots' | 'classy' | 'classy-rounded' | 'extra-rounded';
type CornerSquareStyle = 'square' | 'dot' | 'extra-rounded';
type CornerDotStyle = 'square' | 'dot';
type LogoOption = 'none' | 'nomad' | 'mic' | 'music';

interface QRStylePrefs {
  dotStyle: DotStyle;
  cornerSquareStyle: CornerSquareStyle;
  cornerDotStyle: CornerDotStyle;
  fgColor: string;
  bgColor: string;
  logo: LogoOption;
}

const DEFAULT_PREFS: QRStylePrefs = {
  dotStyle: 'square',
  cornerSquareStyle: 'square',
  cornerDotStyle: 'square',
  fgColor: '#000000',
  bgColor: '#ffffff',
  logo: 'none',
};

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

function loadPrefs(): QRStylePrefs {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return { ...DEFAULT_PREFS, ...JSON.parse(saved) };
    }
  } catch {}
  return { ...DEFAULT_PREFS };
}

function savePrefs(prefs: QRStylePrefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {}
}

function getLucideIconDataUrl(IconComponent: typeof Mic, color: string = '#000000'): string {
  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('xmlns', svgNs);
  svg.setAttribute('width', '48');
  svg.setAttribute('height', '48');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', color);
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');

  const iconNode = (IconComponent as unknown as { iconNode: Array<[string, Record<string, string>]> }).iconNode;
  if (iconNode) {
    for (const [tag, attrs] of iconNode) {
      const el = document.createElementNS(svgNs, tag);
      for (const [key, val] of Object.entries(attrs)) {
        el.setAttribute(key, val);
      }
      svg.appendChild(el);
    }
  }

  const svgStr = new XMLSerializer().serializeToString(svg);
  return `data:image/svg+xml;base64,${btoa(svgStr)}`;
}

interface QRCodeDialogProps {
  referralUrl: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function QRCodeDialog({ referralUrl, open, onOpenChange }: QRCodeDialogProps) {
  const t = useTranslations('referrals');
  const containerRef = useRef<HTMLDivElement>(null);
  const qrRef = useRef<InstanceType<typeof import('qr-code-styling').default> | null>(null);
  const [prefs, setPrefs] = useState<QRStylePrefs>(DEFAULT_PREFS);
  const [loaded, setLoaded] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [flyerTheme, setFlyerTheme] = useState<'light' | 'dark'>('light');
  const [flyerLoading, setFlyerLoading] = useState(false);
  const [flyerError, setFlyerError] = useState('');

  // Load prefs from localStorage on open
  useEffect(() => {
    if (open) {
      setPrefs(loadPrefs());
    }
  }, [open]);

  // Build QR options from prefs
  const buildOptions = useCallback((p: QRStylePrefs) => {
    let image: string | undefined;
    if (p.logo === 'nomad') {
      image = '/nomad-karaoke-logo.svg';
    } else if (p.logo === 'mic') {
      image = getLucideIconDataUrl(Mic, p.fgColor);
    } else if (p.logo === 'music') {
      image = getLucideIconDataUrl(Music, p.fgColor);
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
      imageOptions: image ? { hideBackgroundDots: true, imageSize: 0.2, margin: 4 } : undefined,
    };
  }, [referralUrl]);

  // Initialize / update QR code
  useEffect(() => {
    if (!open) return;

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

      if (!cancelled) {
        setLoaded(true);
      }
    };

    init();

    return () => {
      cancelled = true;
    };
  }, [open, prefs, buildOptions]);

  // Clean up on close
  useEffect(() => {
    if (!open) {
      qrRef.current = null;
      setLoaded(false);
    }
  }, [open]);

  // Clean up debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const updatePref = <K extends keyof QRStylePrefs>(key: K, value: QRStylePrefs[K]) => {
    setPrefs(prev => {
      const next = { ...prev, [key]: value };
      // Debounce localStorage save
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => savePrefs(next), 300);
      return next;
    });
  };

  const handleGenerateFlyer = async () => {
    setFlyerLoading(true);
    setFlyerError('');
    try {
      let qrDataUrl: string;
      if (qrRef.current) {
        const blob = await qrRef.current.getRawData('svg');
        if (blob) {
          qrDataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result as string);
            reader.onerror = () => reject(new Error('Failed to read QR code data'));
            reader.readAsDataURL(blob);
          });
        } else {
          throw new Error('Failed to generate QR code image');
        }
      } else {
        throw new Error('QR code not initialized');
      }

      const pdfBlob = await generateFlyer(flyerTheme, qrDataUrl);
      const url = URL.createObjectURL(pdfBlob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'nomad-karaoke-referral-flyer.pdf';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to generate flyer';
      setFlyerError(message);
      console.error('Failed to generate flyer:', err);
    } finally {
      setFlyerLoading(false);
    }
  };

  const handleDownload = async (extension: 'png' | 'svg') => {
    // Initialize QR instance if not yet ready (e.g., download clicked before init effect completes)
    if (!qrRef.current) {
      const QRCodeStyling = (await import('qr-code-styling')).default;
      qrRef.current = new QRCodeStyling(buildOptions(prefs));
    }
    await qrRef.current.download({ name: 'referral-qr', extension });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl bg-card border-border max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <QrCode className="w-5 h-5" />
            {t('qrTitle')}
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-col md:flex-row gap-6">
          {/* Left: QR Preview */}
          <div className="flex-shrink-0 flex items-center justify-center">
            <div
              className="relative w-[250px] h-[250px] rounded-lg border border-border"
              style={{ backgroundColor: prefs.bgColor }}
            >
              {/* QR code is appended into this container by qr-code-styling */}
              <div ref={containerRef} className="w-full h-full" />
              {/* Loading skeleton rendered as a sibling so innerHTML clear doesn't affect React tree */}
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
              <div className="grid grid-cols-2 gap-1.5 mt-1">
                {(['none', 'nomad', 'mic', 'music'] as LogoOption[]).map(option => {
                  const labelKeys: Record<LogoOption, string> = {
                    none: 'qrLogoNone',
                    nomad: 'qrLogoNomad',
                    mic: 'qrLogoMic',
                    music: 'qrLogoMusic',
                  };
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
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <div className="w-full space-y-3">
            {/* Flyer generation */}
            <div className="flex items-center gap-3 pt-2 border-t border-border">
              <span className="text-sm font-medium text-foreground">{t('flyerTheme')}</span>
              <div className="flex gap-1.5">
                {(['light', 'dark'] as const).map(theme => (
                  <button
                    key={theme}
                    onClick={() => setFlyerTheme(theme)}
                    className={`px-2 py-1 text-xs rounded border transition-colors ${
                      flyerTheme === theme
                        ? 'border-primary bg-primary/10 text-primary font-medium'
                        : 'border-border text-muted-foreground hover:border-primary/50'
                    }`}
                  >
                    {t(theme === 'light' ? 'flyerThemeLight' : 'flyerThemeDark')}
                  </button>
                ))}
              </div>
              <button
                onClick={handleGenerateFlyer}
                disabled={flyerLoading}
                className="ml-auto px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center gap-2 disabled:opacity-50"
              >
                {flyerLoading ? t('flyerGenerating') : t('flyerGenerate')}
              </button>
            </div>
            {flyerError && (
              <p className="text-xs text-destructive">{flyerError}</p>
            )}
            {/* QR download buttons */}
            <div className="flex gap-2 sm:justify-end">
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
