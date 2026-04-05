# Affiliate QR Code Generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a customizable QR code generator dialog to the referral dashboard so referrers can create branded QR codes for their referral links.

**Architecture:** Frontend-only feature. A new `QRCodeDialog` component renders a `qr-code-styling` QR code with live preview and style controls inside a Radix Dialog. Style prefs persist in localStorage. No backend changes.

**Tech Stack:** React 19, Next.js 16, `qr-code-styling` (client-side QR generation), Radix Dialog, Tailwind CSS, Lucide icons, next-intl, Jest + @testing-library/react

**Spec:** `docs/archive/2026-04-05-affiliate-qr-code-generator-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx` | Unit tests for the QR dialog |
| Create | `frontend/components/referrals/QRCodeDialog.tsx` | QR code generator dialog component |
| Modify | `frontend/components/referrals/ReferralDashboard.tsx` | Add QR Code button |
| Modify | `frontend/messages/en.json` | Add i18n strings |
| Modify | `frontend/package.json` | Add `qr-code-styling` dependency |

---

### Task 1: Install dependency and add i18n strings

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/messages/en.json`

- [ ] **Step 1: Install qr-code-styling**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend && npm install qr-code-styling
```

- [ ] **Step 2: Add i18n strings to en.json**

Open `frontend/messages/en.json` and add these keys inside the existing `"referrals"` object, after the `"editProfile"` entry:

```json
"qrCode": "QR Code",
"qrTitle": "QR Code Generator",
"qrDotStyle": "Dot Style",
"qrCornerFrame": "Corner Frame",
"qrCornerDot": "Corner Dot",
"qrColors": "Colors",
"qrForeground": "Foreground",
"qrBackground": "Background",
"qrLogo": "Center Logo",
"qrLogoNone": "None",
"qrLogoNomad": "Nomad Karaoke",
"qrLogoMic": "Microphone",
"qrLogoMusic": "Music",
"qrDownloadPng": "Download PNG",
"qrDownloadSvg": "Download SVG",
"qrStyleSquare": "Square",
"qrStyleRounded": "Rounded",
"qrStyleDots": "Dots",
"qrStyleClassy": "Classy",
"qrStyleClassyRounded": "Classy Rounded",
"qrStyleExtraRounded": "Extra Rounded",
"qrStyleDot": "Dot"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/package.json frontend/package-lock.json frontend/messages/en.json
git commit -m "feat(referral): add qr-code-styling dep and i18n strings for QR generator"
```

---

### Task 2: Write failing tests for QRCodeDialog

**Files:**
- Create: `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx`

The `qr-code-styling` library requires DOM/canvas and can't run in jsdom. We'll mock it and test the component's behavior: rendering controls, localStorage persistence, button interactions.

- [ ] **Step 1: Create the test file**

Create `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import QRCodeDialog from '../QRCodeDialog';

// Mock qr-code-styling — it requires canvas/DOM APIs not available in jsdom
const mockAppend = jest.fn();
const mockUpdate = jest.fn();
const mockDownload = jest.fn().mockResolvedValue(undefined);

jest.mock('qr-code-styling', () => {
  return jest.fn().mockImplementation(() => ({
    append: mockAppend,
    update: mockUpdate,
    download: mockDownload,
  }));
});

// Load real English messages for the referrals namespace
const messages = require('../../../messages/en.json');

function renderDialog(props: Partial<React.ComponentProps<typeof QRCodeDialog>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <QRCodeDialog
        referralUrl="https://nomadkaraoke.com/r/testcode"
        open={true}
        onOpenChange={jest.fn()}
        {...props}
      />
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('QRCodeDialog', () => {
  it('renders the dialog title and download buttons when open', () => {
    renderDialog();
    expect(screen.getByText('QR Code Generator')).toBeInTheDocument();
    expect(screen.getByText('Download PNG')).toBeInTheDocument();
    expect(screen.getByText('Download SVG')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    renderDialog({ open: false });
    expect(screen.queryByText('QR Code Generator')).not.toBeInTheDocument();
  });

  it('renders all dot style options', () => {
    renderDialog();
    expect(screen.getByText('Square')).toBeInTheDocument();
    expect(screen.getByText('Rounded')).toBeInTheDocument();
    expect(screen.getByText('Dots')).toBeInTheDocument();
    expect(screen.getByText('Classy')).toBeInTheDocument();
    expect(screen.getByText('Classy Rounded')).toBeInTheDocument();
    expect(screen.getByText('Extra Rounded')).toBeInTheDocument();
  });

  it('renders corner frame and corner dot style sections', () => {
    renderDialog();
    expect(screen.getByText('Corner Frame')).toBeInTheDocument();
    expect(screen.getByText('Corner Dot')).toBeInTheDocument();
  });

  it('renders center logo options', () => {
    renderDialog();
    expect(screen.getByText('Center Logo')).toBeInTheDocument();
    expect(screen.getByText('None')).toBeInTheDocument();
    expect(screen.getByText('Nomad Karaoke')).toBeInTheDocument();
    expect(screen.getByText('Microphone')).toBeInTheDocument();
    expect(screen.getByText('Music')).toBeInTheDocument();
  });

  it('renders color picker inputs', () => {
    renderDialog();
    expect(screen.getByText('Foreground')).toBeInTheDocument();
    expect(screen.getByText('Background')).toBeInTheDocument();
    // Color inputs
    const colorInputs = screen.getAllByDisplayValue('#000000');
    expect(colorInputs.length).toBeGreaterThanOrEqual(1);
  });

  it('saves style prefs to localStorage on change', async () => {
    renderDialog();
    // Click "Rounded" dot style
    fireEvent.click(screen.getByText('Rounded'));
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem('nk-qr-style-prefs') || '{}');
      expect(saved.dotStyle).toBe('rounded');
    });
  });

  it('restores style prefs from localStorage on open', () => {
    localStorage.setItem('nk-qr-style-prefs', JSON.stringify({
      dotStyle: 'dots',
      cornerSquareStyle: 'dot',
      cornerDotStyle: 'dot',
      fgColor: '#ff0000',
      bgColor: '#00ff00',
      logo: 'nomad',
    }));
    renderDialog();
    // The foreground color input should have the saved value
    const fgInput = screen.getByDisplayValue('#ff0000');
    expect(fgInput).toBeInTheDocument();
  });

  it('calls download with PNG extension when Download PNG clicked', async () => {
    renderDialog();
    fireEvent.click(screen.getByText('Download PNG'));
    await waitFor(() => {
      expect(mockDownload).toHaveBeenCalledWith({
        name: 'referral-qr',
        extension: 'png',
      });
    });
  });

  it('calls download with SVG extension when Download SVG clicked', async () => {
    renderDialog();
    fireEvent.click(screen.getByText('Download SVG'));
    await waitFor(() => {
      expect(mockDownload).toHaveBeenCalledWith({
        name: 'referral-qr',
        extension: 'svg',
      });
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx jest components/referrals/__tests__/QRCodeDialog.test.tsx --no-coverage 2>&1 | tail -30
```

Expected: All tests FAIL because `QRCodeDialog` component doesn't exist yet.

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/components/referrals/__tests__/QRCodeDialog.test.tsx
git commit -m "test(referral): add failing tests for QR code dialog"
```

---

### Task 3: Implement QRCodeDialog component

**Files:**
- Create: `frontend/components/referrals/QRCodeDialog.tsx`

- [ ] **Step 1: Create the QRCodeDialog component**

Create `frontend/components/referrals/QRCodeDialog.tsx`:

```tsx
'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { QrCode, Download, Mic, Music } from 'lucide-react';
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
  // Render Lucide icon to SVG string for use as QR center image
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

  // Get the icon's path data from lucide
  // lucide-react icons have an iconNode property with SVG element definitions
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

    const init = async () => {
      const QRCodeStyling = (await import('qr-code-styling')).default;

      if (!qrRef.current) {
        qrRef.current = new QRCodeStyling(buildOptions(prefs));
        if (containerRef.current) {
          containerRef.current.innerHTML = '';
          qrRef.current.append(containerRef.current);
        }
      } else {
        qrRef.current.update(buildOptions(prefs));
      }
      setLoaded(true);
    };

    init();
  }, [open, prefs, buildOptions]);

  // Clean up on close
  useEffect(() => {
    if (!open) {
      qrRef.current = null;
      setLoaded(false);
    }
  }, [open]);

  const updatePref = <K extends keyof QRStylePrefs>(key: K, value: QRStylePrefs[K]) => {
    setPrefs(prev => {
      const next = { ...prev, [key]: value };
      // Debounce localStorage save
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => savePrefs(next), 300);
      return next;
    });
  };

  const handleDownload = async (extension: 'png' | 'svg') => {
    if (qrRef.current) {
      await qrRef.current.download({ name: 'referral-qr', extension });
    }
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
              ref={containerRef}
              className="w-[250px] h-[250px] rounded-lg border border-border flex items-center justify-center"
              style={{ backgroundColor: prefs.bgColor }}
            >
              {!loaded && (
                <div className="animate-pulse w-[200px] h-[200px] bg-muted rounded" />
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
          <div className="flex gap-2 w-full sm:w-auto">
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx jest components/referrals/__tests__/QRCodeDialog.test.tsx --no-coverage 2>&1 | tail -30
```

Expected: All 9 tests PASS.

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/components/referrals/QRCodeDialog.tsx
git commit -m "feat(referral): implement QR code generator dialog with style customization"
```

---

### Task 4: Wire QRCodeDialog into ReferralDashboard

**Files:**
- Modify: `frontend/components/referrals/ReferralDashboard.tsx`

- [ ] **Step 1: Add QR Code button and dialog to ReferralDashboard**

In `frontend/components/referrals/ReferralDashboard.tsx`, make these changes:

**Add imports** at the top (after the existing imports):

```tsx
import { useState } from 'react'; // already imported, just ensure it's there
import { QrCode } from 'lucide-react';
import QRCodeDialog from './QRCodeDialog';
```

**Add state** inside the `ReferralDashboard` component (after the existing `useState` calls around line 21):

```tsx
const [qrOpen, setQrOpen] = useState(false);
```

**Add the QR Code button** next to the existing Copy Link button. Replace the `<div className="flex gap-2">` block (lines 85-94) with:

```tsx
<div className="flex gap-2">
  <input
    readOnly
    value={`${typeof window !== 'undefined' ? window.location.origin : ''}/r/${data.link.code}`}
    className="flex-1 rounded px-3 py-2 text-sm font-mono"
    style={{ backgroundColor: 'var(--secondary)', color: 'var(--text)' }}
  />
  <button onClick={copyLink} className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm">
    {copied ? t('copied') : t('copyLink')}
  </button>
  <button
    onClick={() => setQrOpen(true)}
    className="px-3 py-2 rounded text-sm border"
    style={{ borderColor: 'var(--card-border)', color: 'var(--text)' }}
    title={t('qrCode')}
  >
    <QrCode className="w-4 h-4" />
  </button>
</div>
```

**Add the QRCodeDialog** just before the closing `</div>` of the component (before line 212):

```tsx
<QRCodeDialog
  referralUrl={`${typeof window !== 'undefined' ? window.location.origin : ''}/r/${data.link.code}`}
  open={qrOpen}
  onOpenChange={setQrOpen}
/>
```

- [ ] **Step 2: Run all tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx jest --no-coverage 2>&1 | tail -30
```

Expected: All tests PASS.

- [ ] **Step 3: Verify the build compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator/frontend
npx next build 2>&1 | tail -20
```

Expected: Build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add frontend/components/referrals/ReferralDashboard.tsx
git commit -m "feat(referral): wire QR code dialog into referral dashboard"
```

---

### Task 5: Fix any test issues and final verification

**Files:**
- Possibly adjust: `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx`
- Possibly adjust: `frontend/components/referrals/QRCodeDialog.tsx`

This task handles any test adjustments discovered during Task 3/4 and runs the full test suite.

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
make test 2>&1 | tail -50
```

Expected: All backend and frontend tests pass.

- [ ] **Step 2: Fix any failures**

If tests fail, fix the specific issues. Common things to watch for:
- `qr-code-styling` mock may need adjustment if the dynamic import pattern doesn't match the mock
- `next-intl` test provider may need `timeZone` or `now` props depending on the test setup
- Color input `getByDisplayValue` may need adjusting if the browser normalizes hex colors

- [ ] **Step 3: Commit fixes if any**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-affiliate-qr-generator
git add -A
git commit -m "fix(referral): address test issues in QR code dialog"
```

Only commit if there were actual fixes. Skip if all tests passed in Step 1.
