# Referral Flyer Customisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the QR Code Generator dialog into a tabbed Referral Tools dialog with live flyer preview, customisation controls, preset themes, and client-side PDF/HTML/PNG export.

**Architecture:** A tabbed dialog (`ReferralToolsDialog`) with QR Code and Printable Flyer tabs sharing a preview-left / controls-right layout. The flyer is a React component port of the existing HTML template. Export uses html2canvas + jsPDF client-side. All config persists to localStorage.

**Tech Stack:** React, Next.js, next-intl, Radix UI (Tabs, Collapsible, Switch, Slider), qr-code-styling, html2canvas, jsPDF

**Spec:** `docs/archive/2026-04-09-referral-flyer-customisation-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `frontend/components/referrals/flyer-presets.ts` | FlyerConfig type, defaults, built-in presets, localStorage save/load |
| Create | `frontend/components/referrals/FlyerPreview.tsx` | React port of flyer HTML template, renders live preview |
| Create | `frontend/components/referrals/FlyerControls.tsx` | Right-side customisation panel (colors, text, sections, branding, layout) |
| Create | `frontend/components/referrals/FlyerExport.ts` | PDF/HTML/PNG export functions |
| Create | `frontend/components/referrals/FlyerTab.tsx` | Container: preview + controls + export buttons for Flyer tab |
| Create | `frontend/components/referrals/QRCodeTab.tsx` | Extracted QR code preview + controls from current QRCodeDialog |
| Create | `frontend/components/referrals/ReferralToolsDialog.tsx` | Tabbed shell wrapping QRCodeTab + FlyerTab |
| Modify | `frontend/components/referrals/ReferralDashboard.tsx` | Update import from QRCodeDialog → ReferralToolsDialog |
| Modify | `frontend/app/admin/referrals/page.tsx` | Update import from QRCodeDialog → ReferralToolsDialog |
| Delete | `frontend/components/referrals/QRCodeDialog.tsx` | Replaced by ReferralToolsDialog + QRCodeTab |
| Modify | `frontend/messages/en.json` | Add new i18n keys for flyer tab |
| Create | `frontend/components/referrals/__tests__/flyer-presets.test.ts` | Tests for preset logic |
| Create | `frontend/components/referrals/__tests__/FlyerExport.test.ts` | Tests for export functions |
| Modify | `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx` | Rename → ReferralToolsDialog.test.tsx, add tab/flyer tests |
| Modify | `frontend/package.json` | Add html2canvas, jspdf dependencies |

---

### Task 1: Install dependencies

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install html2canvas and jspdf**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npm install html2canvas jspdf
```

- [ ] **Step 2: Verify installation**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && node -e "require('html2canvas'); require('jspdf'); console.log('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/package.json frontend/package-lock.json && git commit -m "chore: add html2canvas and jspdf dependencies"
```

---

### Task 2: Create flyer-presets.ts (types, defaults, preset logic)

**Files:**
- Create: `frontend/components/referrals/flyer-presets.ts`
- Create: `frontend/components/referrals/__tests__/flyer-presets.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/referrals/__tests__/flyer-presets.test.ts`:

```typescript
import {
  type FlyerConfig,
  DEFAULT_FLYER_CONFIG,
  LIGHT_PRESET,
  DARK_PRESET,
  BUILT_IN_PRESETS,
  loadFlyerConfig,
  saveFlyerConfig,
  loadCustomPresets,
  saveCustomPreset,
  deleteCustomPreset,
} from '../flyer-presets';

beforeEach(() => {
  localStorage.clear();
});

describe('flyer-presets', () => {
  describe('DEFAULT_FLYER_CONFIG', () => {
    it('has all required fields with sensible defaults', () => {
      expect(DEFAULT_FLYER_CONFIG.bgColor).toBe('#faf8f5');
      expect(DEFAULT_FLYER_CONFIG.headlineGradient).toEqual(['#c45aff', '#ff7acc', '#ff5a8a']);
      expect(DEFAULT_FLYER_CONFIG.headlineSubColor).toBe('#1a1a1a');
      expect(DEFAULT_FLYER_CONFIG.textColor).toBe('#1a1a1a');
      expect(DEFAULT_FLYER_CONFIG.subtextColor).toBe('#666666');
      expect(DEFAULT_FLYER_CONFIG.accentColor).toBe('#c45aff');
      expect(DEFAULT_FLYER_CONFIG.headlineMain).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.headlineSub).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.subtitle).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.ctaLabel).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.ctaNote).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.steps).toHaveLength(4);
      expect(DEFAULT_FLYER_CONFIG.steps[0]).toEqual({ title: null, desc: null });
      expect(DEFAULT_FLYER_CONFIG.showSubtitle).toBe(true);
      expect(DEFAULT_FLYER_CONFIG.showSteps).toBe(true);
      expect(DEFAULT_FLYER_CONFIG.showDivider).toBe(true);
      expect(DEFAULT_FLYER_CONFIG.showBottomFeatures).toBe(true);
      expect(DEFAULT_FLYER_CONFIG.showBottomTagline).toBe(true);
      expect(DEFAULT_FLYER_CONFIG.customLogoUrl).toBeNull();
      expect(DEFAULT_FLYER_CONFIG.perPage).toBe(1);
      expect(DEFAULT_FLYER_CONFIG.marginMm).toBe(15);
    });
  });

  describe('BUILT_IN_PRESETS', () => {
    it('has Light and Dark presets', () => {
      expect(BUILT_IN_PRESETS).toHaveLength(2);
      expect(BUILT_IN_PRESETS[0].name).toBe('Light');
      expect(BUILT_IN_PRESETS[1].name).toBe('Dark');
    });

    it('Light preset matches default config colors', () => {
      expect(LIGHT_PRESET.bgColor).toBe('#faf8f5');
      expect(LIGHT_PRESET.textColor).toBe('#1a1a1a');
    });

    it('Dark preset has dark background and light text', () => {
      expect(DARK_PRESET.bgColor).toBe('#0c0a1a');
      expect(DARK_PRESET.textColor).toBe('#ffffff');
    });
  });

  describe('loadFlyerConfig / saveFlyerConfig', () => {
    it('returns default config when nothing saved', () => {
      const config = loadFlyerConfig();
      expect(config).toEqual(DEFAULT_FLYER_CONFIG);
    });

    it('round-trips a config through save and load', () => {
      const custom: FlyerConfig = {
        ...DEFAULT_FLYER_CONFIG,
        bgColor: '#ffffff',
        perPage: 2,
        marginMm: 5,
      };
      saveFlyerConfig(custom);
      expect(loadFlyerConfig()).toEqual(custom);
    });

    it('merges partial saved data with defaults', () => {
      localStorage.setItem('nk-flyer-prefs', JSON.stringify({ bgColor: '#ff0000' }));
      const config = loadFlyerConfig();
      expect(config.bgColor).toBe('#ff0000');
      expect(config.textColor).toBe('#1a1a1a'); // default filled in
    });
  });

  describe('custom presets', () => {
    it('returns empty array when no custom presets saved', () => {
      expect(loadCustomPresets()).toEqual([]);
    });

    it('saves and loads a custom preset', () => {
      const config: FlyerConfig = { ...DEFAULT_FLYER_CONFIG, bgColor: '#ff0000' };
      saveCustomPreset('My Red Theme', config);
      const presets = loadCustomPresets();
      expect(presets).toHaveLength(1);
      expect(presets[0].name).toBe('My Red Theme');
      expect(presets[0].config.bgColor).toBe('#ff0000');
    });

    it('overwrites preset with same name', () => {
      saveCustomPreset('Theme A', { ...DEFAULT_FLYER_CONFIG, bgColor: '#111111' });
      saveCustomPreset('Theme A', { ...DEFAULT_FLYER_CONFIG, bgColor: '#222222' });
      const presets = loadCustomPresets();
      expect(presets).toHaveLength(1);
      expect(presets[0].config.bgColor).toBe('#222222');
    });

    it('deletes a custom preset by name', () => {
      saveCustomPreset('To Delete', DEFAULT_FLYER_CONFIG);
      saveCustomPreset('To Keep', DEFAULT_FLYER_CONFIG);
      deleteCustomPreset('To Delete');
      const presets = loadCustomPresets();
      expect(presets).toHaveLength(1);
      expect(presets[0].name).toBe('To Keep');
    });

    it('no-ops when deleting non-existent preset', () => {
      saveCustomPreset('Exists', DEFAULT_FLYER_CONFIG);
      deleteCustomPreset('Does Not Exist');
      expect(loadCustomPresets()).toHaveLength(1);
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest components/referrals/__tests__/flyer-presets.test.ts --no-coverage 2>&1 | tail -20
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement flyer-presets.ts**

Create `frontend/components/referrals/flyer-presets.ts`:

```typescript
export interface FlyerConfig {
  // Colors
  bgColor: string;
  headlineGradient: string[];
  headlineSubColor: string;
  textColor: string;
  subtextColor: string;
  accentColor: string;

  // Text overrides (null = use default)
  headlineMain: string | null;
  headlineSub: string | null;
  subtitle: string | null;
  ctaLabel: string | null;
  ctaNote: string | null;
  steps: Array<{ title: string | null; desc: string | null }>;

  // Section visibility
  showSubtitle: boolean;
  showSteps: boolean;
  showDivider: boolean;
  showBottomFeatures: boolean;
  showBottomTagline: boolean;

  // Custom branding
  customLogoUrl: string | null;

  // Print layout
  perPage: 1 | 2 | 4;
  marginMm: number;
}

export interface NamedPreset {
  name: string;
  config: FlyerConfig;
}

const FLYER_CONFIG_KEY = 'nk-flyer-prefs';
const CUSTOM_PRESETS_KEY = 'nk-flyer-custom-presets';

export const LIGHT_PRESET: FlyerConfig = {
  bgColor: '#faf8f5',
  headlineGradient: ['#c45aff', '#ff7acc', '#ff5a8a'],
  headlineSubColor: '#1a1a1a',
  textColor: '#1a1a1a',
  subtextColor: '#666666',
  accentColor: '#c45aff',
  headlineMain: null,
  headlineSub: null,
  subtitle: null,
  ctaLabel: null,
  ctaNote: null,
  steps: [
    { title: null, desc: null },
    { title: null, desc: null },
    { title: null, desc: null },
    { title: null, desc: null },
  ],
  showSubtitle: true,
  showSteps: true,
  showDivider: true,
  showBottomFeatures: true,
  showBottomTagline: true,
  customLogoUrl: null,
  perPage: 1,
  marginMm: 15,
};

export const DARK_PRESET: FlyerConfig = {
  bgColor: '#0c0a1a',
  headlineGradient: ['#ffd86b', '#ff7acc', '#c45aff'],
  headlineSubColor: '#ffffff',
  textColor: '#ffffff',
  subtextColor: 'rgba(255, 255, 255, 0.6)',
  accentColor: '#c45aff',
  headlineMain: null,
  headlineSub: null,
  subtitle: null,
  ctaLabel: null,
  ctaNote: null,
  steps: [
    { title: null, desc: null },
    { title: null, desc: null },
    { title: null, desc: null },
    { title: null, desc: null },
  ],
  showSubtitle: true,
  showSteps: true,
  showDivider: true,
  showBottomFeatures: true,
  showBottomTagline: true,
  customLogoUrl: null,
  perPage: 1,
  marginMm: 15,
};

export const DEFAULT_FLYER_CONFIG: FlyerConfig = { ...LIGHT_PRESET };

export const BUILT_IN_PRESETS: NamedPreset[] = [
  { name: 'Light', config: LIGHT_PRESET },
  { name: 'Dark', config: DARK_PRESET },
];

export function loadFlyerConfig(): FlyerConfig {
  try {
    const saved = localStorage.getItem(FLYER_CONFIG_KEY);
    if (saved) {
      return { ...DEFAULT_FLYER_CONFIG, ...JSON.parse(saved) };
    }
  } catch {}
  return { ...DEFAULT_FLYER_CONFIG };
}

export function saveFlyerConfig(config: FlyerConfig): void {
  try {
    localStorage.setItem(FLYER_CONFIG_KEY, JSON.stringify(config));
  } catch {}
}

export function loadCustomPresets(): NamedPreset[] {
  try {
    const saved = localStorage.getItem(CUSTOM_PRESETS_KEY);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch {}
  return [];
}

export function saveCustomPreset(name: string, config: FlyerConfig): void {
  const presets = loadCustomPresets();
  const idx = presets.findIndex(p => p.name === name);
  if (idx >= 0) {
    presets[idx] = { name, config };
  } else {
    presets.push({ name, config });
  }
  try {
    localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(presets));
  } catch {}
}

export function deleteCustomPreset(name: string): void {
  const presets = loadCustomPresets().filter(p => p.name !== name);
  try {
    localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(presets));
  } catch {}
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest components/referrals/__tests__/flyer-presets.test.ts --no-coverage 2>&1 | tail -20
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/flyer-presets.ts frontend/components/referrals/__tests__/flyer-presets.test.ts && git commit -m "feat: add flyer config types, presets, and localStorage persistence"
```

---

### Task 3: Create FlyerPreview.tsx (React port of HTML template)

**Files:**
- Create: `frontend/components/referrals/FlyerPreview.tsx`

This is the core visual component — a React port of `backend/templates/printables/website-referral-flyer.html`. It renders inline in the dialog.

- [ ] **Step 1: Create FlyerPreview.tsx**

Create `frontend/components/referrals/FlyerPreview.tsx`:

```tsx
'use client';

import { useEffect, useRef } from 'react';
import type { FlyerConfig } from './flyer-presets';

// Default text values matching the original HTML template
const DEFAULTS = {
  headlineMain: 'MAKE YOUR OWN',
  headlineSub: 'KARAOKE VIDEOS',
  subtitle:
    'Turn any song into a professional karaoke video with synced lyrics, vocal removal, and automatic YouTube publishing.',
  ctaLabel: (discountPercent: number) =>
    `First track free + ${discountPercent}% off with this link`,
  ctaNote: 'Scan the QR code or visit the link above',
  steps: [
    { title: 'Pick any song', desc: 'Search by artist & title or paste a YouTube link' },
    { title: 'Our system does the work', desc: 'Vocals removed, lyrics synced, video rendered automatically' },
    { title: 'Review & correct the lyrics and instrumental', desc: 'Fine-tune everything before publishing' },
    { title: 'Download or publish to YouTube', desc: 'Get your finished karaoke video in minutes' },
  ],
};

// Inline SVG logo matching the template (Nomad Karaoke wordmark)
function NomadLogo({ color }: { color: string }) {
  return (
    <svg className="w-[260px] h-auto" viewBox="45 60 255 135" xmlns="http://www.w3.org/2000/svg">
      <g fill={color} transform="translate(48.368003845214844,62.62239074707031)">
        <g transform="translate(0,0)">
          <g transform="scale(1.4000000000000004)">
            <g stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" paintOrder="stroke" fillOpacity="0">
              <path d="M1.4-33.44L1.4 0C31.48 0 3.96 0 34.04 0L34.04-32.64 17.81-32.64 17.81-20.61 1.4-33.44ZM52.21-33.76C42.6-33.76 34.81-25.97 34.81-16.37 34.81-6.76 42.6 1.03 52.21 1.03 61.81 1.03 69.6-6.76 69.6-16.37 69.6-25.97 61.81-33.76 52.21-33.76ZM70.7 0L103.34 0 103.25-33.9 86.93-21.54 70.61-33.9 70.7 0ZM102.34 0C138.2 0 107.52 0 143.56 0L122.95-34.93 102.34 0ZM158.84 0.05C167.84 0.05 175.16-7.27 175.16-16.27 175.16-25.27 167.84-32.64 158.84-32.64L142.52-32.64C142.52 0.75 142.52-33.34 142.52 0.05L158.84 0.05Z" transform="translate(-1.399999976158142, 34.93000030517578)" />
            </g>
            <g transform="translate(0,38.959999084472656)">
              <g stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" paintOrder="stroke" fill={color} fillOpacity="0" transform="scale(1.02719)">
                <path d="M24.43 0L17.67 0 7.98-11.87 7.98 0 2.63 0 2.63-26.64 7.98-26.64 7.98-14.69 17.67-26.64 24.12-26.64 13.13-13.44 24.43 0ZM45.03 0L43.27-5.08 32.66-5.08 30.91 0 25.3 0 34.88-26.68 41.1-26.68 50.68 0 45.03 0ZM34.11-9.35L41.82-9.35 37.97-20.5 34.11-9.35ZM73.19 0L67.01 0 61.13-10.38 58.61-10.38 58.61 0 53.27 0 53.27-26.64 63.27-26.64Q66.36-26.64 68.53-25.55 70.71-24.47 71.8-22.61 72.89-20.76 72.89-18.47L72.89-18.47Q72.89-15.84 71.36-13.72 69.83-11.6 66.82-10.8L66.82-10.8 73.19 0ZM58.61-22.21L58.61-14.39 63.08-14.39Q65.25-14.39 66.32-15.44 67.39-16.49 67.39-18.36L67.39-18.36Q67.39-20.19 66.32-21.2 65.25-22.21 63.08-22.21L63.08-22.21 58.61-22.21ZM94.82 0L93.07-5.08 82.46-5.08 80.7 0 75.09 0 84.67-26.68 90.89-26.68 100.47 0 94.82 0ZM83.91-9.35L91.62-9.35 87.76-20.5 83.91-9.35ZM115.43 0.27Q111.69 0.27 108.56-1.49 105.43-3.24 103.6-6.35 101.76-9.47 101.76-13.4L101.76-13.4Q101.76-17.29 103.6-20.4 105.43-23.51 108.56-25.27 111.69-27.02 115.43-27.02L115.43-27.02Q119.21-27.02 122.32-25.27 125.43-23.51 127.24-20.4 129.05-17.29 129.05-13.4L129.05-13.4Q129.05-9.47 127.24-6.35 125.43-3.24 122.3-1.49 119.17 0.27 115.43 0.27L115.43 0.27ZM115.43-4.5Q117.83-4.5 119.66-5.59 121.5-6.68 122.53-8.7 123.56-10.73 123.56-13.4L123.56-13.4Q123.56-16.07 122.53-18.07 121.5-20.08 119.66-21.15 117.83-22.21 115.43-22.21L115.43-22.21Q113.02-22.21 111.17-21.15 109.32-20.08 108.29-18.07 107.26-16.07 107.26-13.4L107.26-13.4Q107.26-10.73 108.29-8.7 109.32-6.68 111.17-5.59 113.02-4.5 115.43-4.5L115.43-4.5ZM153.82 0L147.06 0 137.37-11.87 137.37 0 132.02 0 132.02-26.64 137.37-26.64 137.37-14.69 147.06-26.64 153.51-26.64 142.52-13.44 153.82 0ZM171.79-22.33L161.67-22.33 161.67-15.65 170.64-15.65 170.64-11.41 161.67-11.41 161.67-4.35 171.79-4.35 171.79 0 156.33 0 156.33-26.68 171.79-26.68 171.79-22.33Z" transform="translate(-2.630000114440918, 27.020000457763672)" />
              </g>
            </g>
          </g>
        </g>
      </g>
    </svg>
  );
}

interface FlyerPreviewProps {
  config: FlyerConfig;
  referralCode: string;
  discountPercent: number;
  qrDataUrl: string | null;
  /** Ref to the inner flyer element for export */
  flyerRef?: React.RefObject<HTMLDivElement | null>;
}

export default function FlyerPreview({
  config,
  referralCode,
  discountPercent,
  qrDataUrl,
  flyerRef,
}: FlyerPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Calculate scale to fit the preview container
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => {
      const flyerEl = container.querySelector('[data-flyer-page]') as HTMLElement;
      if (!flyerEl) return;

      const parentW = container.clientWidth;
      const parentH = container.clientHeight;
      // Flyer is 8.5in x 11in at 96dpi = 816 x 1056 px
      const flyerW = 816;
      const flyerH = 1056;
      const scale = Math.min(parentW / flyerW, parentH / flyerH, 1);
      flyerEl.style.transform = `scale(${scale})`;
      flyerEl.style.transformOrigin = 'top left';
      // Reserve the scaled space so the container doesn't collapse
      container.style.width = `${flyerW * scale}px`;
      container.style.height = `${flyerH * scale}px`;
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const headlineMain = config.headlineMain ?? DEFAULTS.headlineMain;
  const headlineSub = config.headlineSub ?? DEFAULTS.headlineSub;
  const subtitle = config.subtitle ?? DEFAULTS.subtitle;
  const ctaLabel = config.ctaLabel ?? DEFAULTS.ctaLabel(discountPercent);
  const ctaNote = config.ctaNote ?? DEFAULTS.ctaNote;
  const steps = DEFAULTS.steps.map((def, i) => ({
    title: config.steps[i]?.title ?? def.title,
    desc: config.steps[i]?.desc ?? def.desc,
  }));

  const gradientStr = `linear-gradient(135deg, ${config.headlineGradient.join(', ')})`;

  return (
    <div ref={containerRef} className="relative overflow-hidden">
      <div
        ref={flyerRef}
        data-flyer-page
        style={{
          width: 816,
          height: 1056,
          fontFamily: "'Outfit', sans-serif",
          background: config.bgColor,
          position: 'relative',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '0.6in 0.65in 0.5in',
          overflow: 'hidden',
        }}
      >
        {/* Google Fonts link for export */}
        <link
          href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Outfit:wght@400;500;600;700;800&display=swap"
          rel="stylesheet"
        />

        {/* Logo */}
        <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 16 }}>
          <NomadLogo color={config.accentColor} />
          {config.customLogoUrl && (
            <img
              src={config.customLogoUrl}
              alt="Custom logo"
              style={{ height: 60, width: 'auto', objectFit: 'contain' }}
            />
          )}
        </div>

        {/* Headline */}
        <div style={{ fontFamily: "'Bebas Neue', sans-serif", textAlign: 'center', lineHeight: 0.92, marginBottom: 6 }}>
          <div
            style={{
              fontSize: 96,
              letterSpacing: 3,
              background: gradientStr,
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >
            {headlineMain}
          </div>
          <div style={{ fontSize: 62, letterSpacing: 2, color: config.headlineSubColor }}>
            {headlineSub}
          </div>
        </div>

        {/* Subtitle */}
        {config.showSubtitle && (
          <div
            style={{
              fontSize: 20,
              fontWeight: 500,
              color: config.subtextColor,
              textAlign: 'center',
              maxWidth: '5.5in',
              lineHeight: 1.4,
              marginBottom: 28,
            }}
          >
            {subtitle}
          </div>
        )}

        {/* Steps */}
        {config.showSteps && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, width: '100%', maxWidth: '6in', marginBottom: 28 }}>
            {steps.map((step, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
                <div
                  style={{
                    width: 52,
                    height: 52,
                    flexShrink: 0,
                    borderRadius: '50%',
                    background: `linear-gradient(135deg, ${config.accentColor}, ${config.headlineGradient[1] || config.accentColor})`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: "'Bebas Neue', sans-serif",
                    fontSize: 30,
                    color: '#fff',
                    letterSpacing: 1,
                  }}
                >
                  {i + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 22, fontWeight: 800, color: config.textColor, letterSpacing: -0.3 }}>
                    {step.title}
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 400, color: config.subtextColor, marginTop: 2 }}>
                    {step.desc}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Divider */}
        {config.showDivider && (
          <div
            style={{
              width: '80%',
              height: 2,
              background: `linear-gradient(90deg, transparent, ${config.accentColor}4D, ${config.headlineGradient[1] || config.accentColor}4D, transparent)`,
              margin: '4px 0 24px',
            }}
          />
        )}

        {/* CTA section */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 40, width: '100%', marginBottom: 20 }}>
          <div
            style={{
              width: 180,
              height: 180,
              borderRadius: 16,
              background: '#fff',
              border: '3px solid #e8e4e0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              boxShadow: '0 4px 20px rgba(0,0,0,0.06)',
              overflow: 'hidden',
            }}
          >
            {qrDataUrl ? (
              <img src={qrDataUrl} alt={`QR Code - nomadkaraoke.com/r/${referralCode.toLowerCase()}`} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            ) : (
              <span style={{ fontSize: 14, color: '#999', textAlign: 'center', padding: 20 }}>QR Code</span>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: config.subtextColor, textTransform: 'uppercase', letterSpacing: 2, marginBottom: 6 }}>
              {ctaLabel}
            </div>
            <div style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: 38, letterSpacing: 2, color: config.textColor, lineHeight: 1, whiteSpace: 'nowrap' }}>
              NOMADKARAOKE.COM/R/
              <span
                style={{
                  background: `linear-gradient(135deg, ${config.headlineGradient.join(', ')}, #ffdf6b)`,
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                  letterSpacing: 4,
                }}
              >
                {referralCode.toUpperCase()}
              </span>
            </div>
            <div style={{ fontSize: 15, color: config.subtextColor, marginTop: 8, fontWeight: 500 }}>
              {ctaNote}
            </div>
          </div>
        </div>

        {/* Bottom */}
        <div style={{ marginTop: 'auto', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {config.showBottomFeatures && (
            <div style={{ display: 'flex', gap: 24 }}>
              {[
                { icon: '\u{1F3A4}', label: 'Any Song' },
                { icon: '\u26A1', label: 'Automatic' },
                { icon: '\u{1F3AC}', label: 'Pro Quality' },
              ].map(({ icon, label }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600, color: config.subtextColor }}>
                  <span style={{ fontSize: 20 }}>{icon}</span>
                  <span>{label}</span>
                </div>
              ))}
            </div>
          )}
          {config.showBottomTagline && (
            <div style={{ fontSize: 14, fontWeight: 500, color: config.subtextColor, letterSpacing: 1, marginLeft: 'auto' }}>
              Wherever you are, sing!
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | grep -E "FlyerPreview|error" | head -20
```

Expected: No errors related to FlyerPreview

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/FlyerPreview.tsx && git commit -m "feat: add FlyerPreview React component (port of HTML template)"
```

---

### Task 4: Create FlyerExport.ts (PDF/HTML/PNG export)

**Files:**
- Create: `frontend/components/referrals/FlyerExport.ts`
- Create: `frontend/components/referrals/__tests__/FlyerExport.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/referrals/__tests__/FlyerExport.test.ts`:

```typescript
import { exportFlyerPdf, exportFlyerHtml, exportFlyerPng } from '../FlyerExport';

// Mock html2canvas
jest.mock('html2canvas', () => {
  return jest.fn().mockResolvedValue({
    toDataURL: jest.fn().mockReturnValue('data:image/png;base64,fakepng'),
    width: 1632,
    height: 2112,
  });
});

// Mock jsPDF
const mockAddImage = jest.fn();
const mockSave = jest.fn();
const mockGetImageProperties = jest.fn().mockReturnValue({ width: 1632, height: 2112 });
jest.mock('jspdf', () => {
  return jest.fn().mockImplementation(() => ({
    addImage: mockAddImage,
    save: mockSave,
    getImageProperties: mockGetImageProperties,
    internal: { pageSize: { getWidth: () => 215.9, getHeight: () => 279.4 } },
    addPage: jest.fn(),
  }));
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('FlyerExport', () => {
  const mockElement = document.createElement('div');
  mockElement.setAttribute('data-flyer-page', '');

  describe('exportFlyerPdf', () => {
    it('generates PDF with 1 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 1, marginMm: 15, filename: 'test.pdf' });
      const jsPDF = require('jspdf');
      expect(jsPDF).toHaveBeenCalled();
      expect(mockAddImage).toHaveBeenCalled();
      expect(mockSave).toHaveBeenCalledWith('test.pdf');
    });

    it('generates PDF with 2 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 2, marginMm: 10, filename: 'test-2up.pdf' });
      expect(mockAddImage).toHaveBeenCalledTimes(2);
      expect(mockSave).toHaveBeenCalledWith('test-2up.pdf');
    });

    it('generates PDF with 4 per page', async () => {
      await exportFlyerPdf(mockElement, { perPage: 4, marginMm: 5, filename: 'test-4up.pdf' });
      expect(mockAddImage).toHaveBeenCalledTimes(4);
      expect(mockSave).toHaveBeenCalledWith('test-4up.pdf');
    });
  });

  describe('exportFlyerHtml', () => {
    it('returns a blob containing the flyer HTML', () => {
      const blob = exportFlyerHtml(mockElement);
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.type).toBe('text/html');
    });
  });

  describe('exportFlyerPng', () => {
    it('triggers download of PNG', async () => {
      // Mock URL.createObjectURL and revokeObjectURL
      const mockUrl = 'blob:test';
      global.URL.createObjectURL = jest.fn().mockReturnValue(mockUrl);
      global.URL.revokeObjectURL = jest.fn();

      // Mock canvas.toBlob
      const mockCanvas = {
        toDataURL: jest.fn().mockReturnValue('data:image/png;base64,fakepng'),
        toBlob: jest.fn((cb: (blob: Blob) => void) => cb(new Blob(['png'], { type: 'image/png' }))),
        width: 1632,
        height: 2112,
      };
      const html2canvas = require('html2canvas');
      html2canvas.mockResolvedValueOnce(mockCanvas);

      await exportFlyerPng(mockElement, 'test.png');
      expect(html2canvas).toHaveBeenCalledWith(mockElement, expect.objectContaining({ scale: 2 }));
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest components/referrals/__tests__/FlyerExport.test.ts --no-coverage 2>&1 | tail -20
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement FlyerExport.ts**

Create `frontend/components/referrals/FlyerExport.ts`:

```typescript
import html2canvas from 'html2canvas';
import jsPDF from 'jspdf';

// US Letter in mm
const PAGE_W_MM = 215.9;
const PAGE_H_MM = 279.4;

interface PdfOptions {
  perPage: 1 | 2 | 4;
  marginMm: number;
  filename: string;
}

export async function exportFlyerPdf(
  flyerElement: HTMLElement,
  options: PdfOptions,
): Promise<void> {
  const { perPage, marginMm, filename } = options;

  // Render the flyer to canvas at 2x for quality
  const canvas = await html2canvas(flyerElement, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: null,
  });

  const imgData = canvas.toDataURL('image/png');
  const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' });

  const margin = marginMm;
  const usableW = PAGE_W_MM - margin * 2;
  const usableH = PAGE_H_MM - margin * 2;

  if (perPage === 1) {
    // Single flyer centered on page
    const flyerAspect = 8.5 / 11;
    let w = usableW;
    let h = w / flyerAspect;
    if (h > usableH) {
      h = usableH;
      w = h * flyerAspect;
    }
    const x = margin + (usableW - w) / 2;
    const y = margin + (usableH - h) / 2;
    pdf.addImage(imgData, 'PNG', x, y, w, h);
  } else if (perPage === 2) {
    // Two flyers stacked vertically
    const gapMm = margin;
    const slotH = (usableH - gapMm) / 2;
    const flyerAspect = 8.5 / 11;
    let w = usableW;
    let h = w / flyerAspect;
    if (h > slotH) {
      h = slotH;
      w = h * flyerAspect;
    }
    const x = margin + (usableW - w) / 2;
    pdf.addImage(imgData, 'PNG', x, margin, w, h);
    pdf.addImage(imgData, 'PNG', x, margin + slotH + gapMm, w, h);
  } else {
    // Four flyers in 2x2 grid
    const gapMm = margin;
    const slotW = (usableW - gapMm) / 2;
    const slotH = (usableH - gapMm) / 2;
    const flyerAspect = 8.5 / 11;
    let w = slotW;
    let h = w / flyerAspect;
    if (h > slotH) {
      h = slotH;
      w = h * flyerAspect;
    }
    const positions = [
      [margin, margin],
      [margin + slotW + gapMm, margin],
      [margin, margin + slotH + gapMm],
      [margin + slotW + gapMm, margin + slotH + gapMm],
    ];
    for (const [x, y] of positions) {
      const offsetX = x + (slotW - w) / 2;
      const offsetY = y + (slotH - h) / 2;
      pdf.addImage(imgData, 'PNG', offsetX, offsetY, w, h);
    }
  }

  pdf.save(filename);
}

export function exportFlyerHtml(flyerElement: HTMLElement): Blob {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nomad Karaoke Referral Flyer</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*, html { margin: 0; padding: 0; box-sizing: border-box; }
@page { size: 8.5in 11in; margin: 0; }
body { display: flex; justify-content: center; }
@media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
</style>
</head>
<body>
${flyerElement.outerHTML}
</body>
</html>`;
  return new Blob([html], { type: 'text/html' });
}

export async function exportFlyerPng(
  flyerElement: HTMLElement,
  filename: string,
): Promise<void> {
  const canvas = await html2canvas(flyerElement, {
    scale: 2,
    useCORS: true,
    allowTaint: true,
    backgroundColor: null,
  });

  const blob = await new Promise<Blob>((resolve) => {
    canvas.toBlob((b: Blob | null) => resolve(b!), 'image/png');
  });

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest components/referrals/__tests__/FlyerExport.test.ts --no-coverage 2>&1 | tail -20
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/FlyerExport.ts frontend/components/referrals/__tests__/FlyerExport.test.ts && git commit -m "feat: add client-side flyer export (PDF, HTML, PNG)"
```

---

### Task 5: Create FlyerControls.tsx (customisation panel)

**Files:**
- Create: `frontend/components/referrals/FlyerControls.tsx`

- [ ] **Step 1: Create FlyerControls.tsx**

Create `frontend/components/referrals/FlyerControls.tsx`:

```tsx
'use client';

import { useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { ChevronDown, Trash2, Upload, X } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import type { FlyerConfig, NamedPreset } from './flyer-presets';
import {
  BUILT_IN_PRESETS,
  loadCustomPresets,
  saveCustomPreset,
  deleteCustomPreset,
} from './flyer-presets';

interface FlyerControlsProps {
  config: FlyerConfig;
  onChange: <K extends keyof FlyerConfig>(key: K, value: FlyerConfig[K]) => void;
  onLoadPreset: (config: FlyerConfig) => void;
}

function ColorPicker({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        value={value.startsWith('#') ? value : '#000000'}
        onChange={(e) => onChange(e.target.value)}
        className="w-8 h-8 rounded border border-border cursor-pointer"
      />
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

function Section({ title, defaultOpen, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen ?? true);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center justify-between w-full py-1.5 text-sm font-medium text-foreground hover:text-primary transition-colors">
        {title}
        <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2 pb-3 space-y-3">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

export default function FlyerControls({ config, onChange, onLoadPreset }: FlyerControlsProps) {
  const t = useTranslations('referrals');
  const [customPresets, setCustomPresets] = useState<NamedPreset[]>(loadCustomPresets);
  const [activePreset, setActivePreset] = useState<string>('Light');
  const [saveName, setSaveName] = useState('');
  const [showSave, setShowSave] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const allPresets = [...BUILT_IN_PRESETS, ...customPresets];

  const handleLoadPreset = (name: string) => {
    const preset = allPresets.find(p => p.name === name);
    if (preset) {
      onLoadPreset(preset.config);
      setActivePreset(name);
    }
  };

  const handleSavePreset = () => {
    if (!saveName.trim()) return;
    saveCustomPreset(saveName.trim(), config);
    setCustomPresets(loadCustomPresets());
    setActivePreset(saveName.trim());
    setSaveName('');
    setShowSave(false);
  };

  const handleDeletePreset = (name: string) => {
    deleteCustomPreset(name);
    setCustomPresets(loadCustomPresets());
    if (activePreset === name) setActivePreset('Custom');
  };

  const handleConfigChange = <K extends keyof FlyerConfig>(key: K, value: FlyerConfig[K]) => {
    setActivePreset('Custom');
    onChange(key, value);
  };

  const handleStepChange = (index: number, field: 'title' | 'desc', value: string) => {
    const newSteps = [...config.steps];
    newSteps[index] = { ...newSteps[index], [field]: value || null };
    handleConfigChange('steps', newSteps);
  };

  const handleGradientChange = (index: number, value: string) => {
    const newGradient = [...config.headlineGradient];
    newGradient[index] = value;
    handleConfigChange('headlineGradient', newGradient);
  };

  const handleLogoUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => {
      handleConfigChange('customLogoUrl', reader.result as string);
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="flex-1 space-y-1 min-w-0 overflow-y-auto max-h-[65vh] pr-1">
      {/* Preset selector */}
      <div className="flex items-center gap-2 pb-2 border-b border-border">
        <select
          value={activePreset}
          onChange={(e) => handleLoadPreset(e.target.value)}
          className="flex-1 rounded border border-border bg-background text-foreground text-sm px-2 py-1.5"
        >
          {activePreset === 'Custom' && <option value="Custom">{t('flyerPresetCustom')}</option>}
          {allPresets.map(p => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
        {showSave ? (
          <div className="flex gap-1">
            <input
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSavePreset()}
              placeholder={t('flyerPresetName')}
              className="w-28 rounded border border-border bg-background text-foreground text-xs px-2 py-1"
              autoFocus
            />
            <button onClick={handleSavePreset} className="text-xs px-2 py-1 bg-primary text-primary-foreground rounded">
              {t('save')}
            </button>
            <button onClick={() => setShowSave(false)} className="text-xs px-1 py-1 text-muted-foreground">
              <X className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <button onClick={() => setShowSave(true)} className="text-xs px-2 py-1.5 border border-border rounded text-muted-foreground hover:text-foreground">
            {t('flyerPresetSave')}
          </button>
        )}
        {customPresets.some(p => p.name === activePreset) && (
          <button onClick={() => handleDeletePreset(activePreset)} className="text-xs px-1 py-1 text-destructive">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Colors */}
      <Section title={t('flyerColors')}>
        <ColorPicker label={t('qrBackground')} value={config.bgColor} onChange={(v) => handleConfigChange('bgColor', v)} />
        <div>
          <span className="text-xs text-muted-foreground">{t('flyerHeadlineGradient')}</span>
          <div className="flex gap-2 mt-1">
            {config.headlineGradient.map((c, i) => (
              <input
                key={i}
                type="color"
                value={c}
                onChange={(e) => handleGradientChange(i, e.target.value)}
                className="w-8 h-8 rounded border border-border cursor-pointer"
              />
            ))}
          </div>
        </div>
        <ColorPicker label={t('flyerHeadlineSubColor')} value={config.headlineSubColor} onChange={(v) => handleConfigChange('headlineSubColor', v)} />
        <ColorPicker label={t('flyerTextColor')} value={config.textColor} onChange={(v) => handleConfigChange('textColor', v)} />
        <ColorPicker label={t('flyerSubtextColor')} value={config.subtextColor} onChange={(v) => handleConfigChange('subtextColor', v)} />
        <ColorPicker label={t('flyerAccentColor')} value={config.accentColor} onChange={(v) => handleConfigChange('accentColor', v)} />
      </Section>

      {/* Text Overrides */}
      <Section title={t('flyerTextOverrides')} defaultOpen={false}>
        <div className="space-y-2">
          <input
            value={config.headlineMain ?? ''}
            onChange={(e) => handleConfigChange('headlineMain', e.target.value || null)}
            placeholder="MAKE YOUR OWN"
            className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
          />
          <input
            value={config.headlineSub ?? ''}
            onChange={(e) => handleConfigChange('headlineSub', e.target.value || null)}
            placeholder="KARAOKE VIDEOS"
            className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
          />
          <textarea
            value={config.subtitle ?? ''}
            onChange={(e) => handleConfigChange('subtitle', e.target.value || null)}
            placeholder="Turn any song into a professional karaoke video..."
            className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1.5 h-16 resize-none"
          />
          <input
            value={config.ctaLabel ?? ''}
            onChange={(e) => handleConfigChange('ctaLabel', e.target.value || null)}
            placeholder="First track free + X% off with this link"
            className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
          />
          <input
            value={config.ctaNote ?? ''}
            onChange={(e) => handleConfigChange('ctaNote', e.target.value || null)}
            placeholder="Scan the QR code or visit the link above"
            className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1.5"
          />
          {config.steps.map((step, i) => (
            <Collapsible key={i}>
              <CollapsibleTrigger className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
                <ChevronDown className="w-3 h-3" /> Step {i + 1}
              </CollapsibleTrigger>
              <CollapsibleContent className="pl-4 pt-1 space-y-1">
                <input
                  value={step.title ?? ''}
                  onChange={(e) => handleStepChange(i, 'title', e.target.value)}
                  placeholder={['Pick any song', 'Our system does the work', 'Review & correct the lyrics and instrumental', 'Download or publish to YouTube'][i]}
                  className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1"
                />
                <input
                  value={step.desc ?? ''}
                  onChange={(e) => handleStepChange(i, 'desc', e.target.value)}
                  placeholder={['Search by artist & title...', 'Vocals removed, lyrics synced...', 'Fine-tune everything...', 'Get your finished karaoke video...'][i]}
                  className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1"
                />
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      </Section>

      {/* Sections */}
      <Section title={t('flyerSections')}>
        {([
          ['showSubtitle', t('flyerToggleSubtitle')],
          ['showSteps', t('flyerToggleSteps')],
          ['showDivider', t('flyerToggleDivider')],
          ['showBottomFeatures', t('flyerToggleFeatures')],
          ['showBottomTagline', t('flyerToggleTagline')],
        ] as const).map(([key, label]) => (
          <div key={key} className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">{label}</span>
            <Switch
              checked={config[key] as boolean}
              onCheckedChange={(v) => handleConfigChange(key, v)}
            />
          </div>
        ))}
      </Section>

      {/* Branding */}
      <Section title={t('flyerBranding')} defaultOpen={false}>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/svg+xml"
          onChange={handleLogoUpload}
          className="hidden"
        />
        {config.customLogoUrl ? (
          <div className="flex items-center gap-2">
            <img src={config.customLogoUrl} alt="Custom logo" className="h-10 w-auto rounded border border-border" />
            <button
              onClick={() => handleConfigChange('customLogoUrl', null)}
              className="text-xs text-destructive flex items-center gap-1"
            >
              <X className="w-3 h-3" /> {t('flyerRemoveLogo')}
            </button>
          </div>
        ) : (
          <button
            onClick={() => fileInputRef.current?.click()}
            className="text-xs px-3 py-1.5 border border-border rounded text-muted-foreground hover:text-foreground flex items-center gap-1.5"
          >
            <Upload className="w-3.5 h-3.5" /> {t('flyerUploadLogo')}
          </button>
        )}
      </Section>

      {/* Print Layout */}
      <Section title={t('flyerPrintLayout')}>
        <div>
          <span className="text-xs text-muted-foreground">{t('flyerPerPage')}</span>
          <div className="flex gap-1.5 mt-1">
            {([1, 2, 4] as const).map(n => (
              <button
                key={n}
                onClick={() => handleConfigChange('perPage', n)}
                className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                  config.perPage === n
                    ? 'border-primary bg-primary/10 text-primary font-medium'
                    : 'border-border text-muted-foreground hover:border-primary/50'
                }`}
              >
                {n}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">{t('flyerMargins')}</span>
            <span className="text-xs text-muted-foreground">{config.marginMm}mm</span>
          </div>
          <Slider
            value={[config.marginMm]}
            onValueChange={([v]) => handleConfigChange('marginMm', v)}
            min={0}
            max={30}
            className="mt-1"
          />
        </div>
      </Section>
    </div>
  );
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | grep -E "FlyerControls|error" | head -20
```

Expected: No errors (some i18n keys won't exist yet — that's fine, they're string lookups)

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/FlyerControls.tsx && git commit -m "feat: add FlyerControls customisation panel"
```

---

### Task 6: Create FlyerTab.tsx (container for preview + controls + export)

**Files:**
- Create: `frontend/components/referrals/FlyerTab.tsx`

- [ ] **Step 1: Create FlyerTab.tsx**

Create `frontend/components/referrals/FlyerTab.tsx`:

```tsx
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
  const qrCanvasRef = useRef<HTMLDivElement>(null);
  const qrInstanceRef = useRef<InstanceType<typeof import('qr-code-styling').default> | null>(null);
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
        // Simple emoji-to-data-url
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
      qrInstanceRef.current = qr;

      // Get data URL for the flyer preview
      const blob = await qr.getRawData('png');
      if (blob && !cancelled) {
        const reader = new FileReader();
        reader.onloadend = () => {
          if (!cancelled) setQrDataUrl(reader.result as string);
        };
        reader.readAsDataURL(blob);
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
        <div className="flex-shrink-0 relative">
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
          disabled={!!exporting}
          className="flex-1 sm:flex-none px-4 py-2 bg-primary text-primary-foreground rounded text-sm flex items-center justify-center gap-2 disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {exporting === 'pdf' ? t('flyerExporting') : t('flyerDownloadPdf')}
        </button>
        <button
          onClick={() => handleExport('html')}
          disabled={!!exporting}
          className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {t('flyerDownloadHtml')}
        </button>
        <button
          onClick={() => handleExport('png')}
          disabled={!!exporting}
          className="flex-1 sm:flex-none px-4 py-2 rounded text-sm border border-border text-foreground flex items-center justify-center gap-2 hover:bg-secondary disabled:opacity-50"
        >
          <Download className="w-4 h-4" />
          {t('flyerDownloadPng')}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | grep -E "FlyerTab|error" | head -20
```

Expected: No errors (some i18n keys are string lookups, resolved at runtime)

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/FlyerTab.tsx && git commit -m "feat: add FlyerTab container (preview + controls + export)"
```

---

### Task 7: Extract QRCodeTab.tsx from QRCodeDialog.tsx

**Files:**
- Create: `frontend/components/referrals/QRCodeTab.tsx`

Extract the QR code preview and controls from `QRCodeDialog.tsx` into a standalone tab component. This component should export both the component and the `QRStylePrefs` type (needed by FlyerTab).

- [ ] **Step 1: Create QRCodeTab.tsx**

Create `frontend/components/referrals/QRCodeTab.tsx`. This is the current QRCodeDialog content minus the Dialog wrapper, with the QR prefs state lifted to props:

```tsx
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
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | grep -E "QRCodeTab|error" | head -20
```

Expected: No errors

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/QRCodeTab.tsx && git commit -m "feat: extract QRCodeTab from QRCodeDialog"
```

---

### Task 8: Create ReferralToolsDialog.tsx (tabbed shell)

**Files:**
- Create: `frontend/components/referrals/ReferralToolsDialog.tsx`

- [ ] **Step 1: Create ReferralToolsDialog.tsx**

Create `frontend/components/referrals/ReferralToolsDialog.tsx`:

```tsx
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

  // Extract referral code from URL if not provided
  const referralCode = referralCodeProp || referralUrl.split('/r/').pop() || 'CODE';

  // Load QR prefs on open
  useEffect(() => {
    if (open) {
      setQrPrefs(loadQrPrefs());
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl bg-card border-border max-h-[90vh] overflow-y-auto">
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
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | grep -E "ReferralToolsDialog|error" | head -20
```

Expected: No errors

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/ReferralToolsDialog.tsx && git commit -m "feat: add ReferralToolsDialog tabbed shell"
```

---

### Task 9: Update consumers and remove old QRCodeDialog

**Files:**
- Modify: `frontend/components/referrals/ReferralDashboard.tsx` (lines 19, 516-520)
- Modify: `frontend/app/admin/referrals/page.tsx` (lines 23, 499-509)
- Delete: `frontend/components/referrals/QRCodeDialog.tsx`

- [ ] **Step 1: Update ReferralDashboard.tsx**

Change the import on line 19 from:
```typescript
import QRCodeDialog from './QRCodeDialog';
```
to:
```typescript
import ReferralToolsDialog from './ReferralToolsDialog';
```

Change the usage on lines 516-520 from:
```tsx
<QRCodeDialog
  referralUrl={`https://nomadkaraoke.com/r/${data.link.code}`}
  open={qrOpen}
  onOpenChange={setQrOpen}
/>
```
to:
```tsx
<ReferralToolsDialog
  referralUrl={`https://nomadkaraoke.com/r/${data.link.code}`}
  open={qrOpen}
  onOpenChange={setQrOpen}
  referralCode={data.link.code}
  discountPercent={data.link.discount_percent}
/>
```

- [ ] **Step 2: Update admin/referrals/page.tsx**

Change the import on line 23 from:
```typescript
import QRCodeDialog from "@/components/referrals/QRCodeDialog"
```
to:
```typescript
import ReferralToolsDialog from "@/components/referrals/ReferralToolsDialog"
```

Change the usage on lines 501-506 from:
```tsx
<QRCodeDialog
  referralUrl={`https://nomadkaraoke.com/r/${qrDialogCode}`}
  open={!!qrDialogCode}
  onOpenChange={(open) => { if (!open) setQrDialogCode(null) }}
  onGenerateFlyer={(theme, qrDataUrl) => adminApi.generateFlyer(qrDialogCode, theme, qrDataUrl)}
  flyerFilename={`nomad-karaoke-flyer-${qrDialogCode}.pdf`}
/>
```
to:
```tsx
<ReferralToolsDialog
  referralUrl={`https://nomadkaraoke.com/r/${qrDialogCode}`}
  open={!!qrDialogCode}
  onOpenChange={(open) => { if (!open) setQrDialogCode(null) }}
  flyerFilename={`nomad-karaoke-flyer-${qrDialogCode}`}
  referralCode={qrDialogCode}
/>
```

- [ ] **Step 3: Delete QRCodeDialog.tsx**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && rm frontend/components/referrals/QRCodeDialog.tsx
```

- [ ] **Step 4: Verify no remaining references to QRCodeDialog**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && grep -r "QRCodeDialog" frontend/components/ frontend/app/ frontend/lib/ --include="*.tsx" --include="*.ts" | grep -v __tests__ | grep -v node_modules
```

Expected: No output (no remaining imports)

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add -A frontend/components/referrals/QRCodeDialog.tsx frontend/components/referrals/ReferralDashboard.tsx frontend/app/admin/referrals/page.tsx && git commit -m "refactor: replace QRCodeDialog with ReferralToolsDialog"
```

---

### Task 10: Add i18n translation keys

**Files:**
- Modify: `frontend/messages/en.json` (referrals namespace)

- [ ] **Step 1: Add new keys to en.json**

Add the following keys to the `referrals` object in `frontend/messages/en.json`, after the existing `flyerGenerating` key:

```json
"toolsTitle": "QR Code & Flyer Generator",
"tabQrCode": "QR Code",
"tabFlyer": "Printable Flyer",
"flyerPresetCustom": "Custom",
"flyerPresetName": "Preset name",
"flyerPresetSave": "Save Preset",
"flyerColors": "Colors",
"flyerHeadlineGradient": "Headline Gradient",
"flyerHeadlineSubColor": "Headline Sub",
"flyerTextColor": "Text",
"flyerSubtextColor": "Subtext",
"flyerAccentColor": "Accent",
"flyerTextOverrides": "Text Overrides",
"flyerSections": "Sections",
"flyerToggleSubtitle": "Subtitle",
"flyerToggleSteps": "How It Works Steps",
"flyerToggleDivider": "Divider",
"flyerToggleFeatures": "Bottom Features",
"flyerToggleTagline": "Bottom Tagline",
"flyerBranding": "Branding",
"flyerUploadLogo": "Upload Logo",
"flyerRemoveLogo": "Remove",
"flyerPrintLayout": "Print Layout",
"flyerPerPage": "Flyers Per Page",
"flyerMargins": "Page Margins",
"flyerEditQr": "Edit QR Style",
"flyerDownloadPdf": "Download PDF",
"flyerDownloadHtml": "Download HTML",
"flyerDownloadPng": "Download PNG",
"flyerExporting": "Exporting..."
```

- [ ] **Step 2: Run translations for all locales**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && python frontend/scripts/translate.py --messages-dir frontend/messages --target all 2>&1 | tail -10
```

Expected: Translations generated for all 33 locales

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/messages/ && git commit -m "feat(i18n): add flyer customisation translation keys for all locales"
```

---

### Task 11: Update tests

**Files:**
- Rename: `frontend/components/referrals/__tests__/QRCodeDialog.test.tsx` → `ReferralToolsDialog.test.tsx`
- Modify: updated test file

- [ ] **Step 1: Rename and update test file**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && mv frontend/components/referrals/__tests__/QRCodeDialog.test.tsx frontend/components/referrals/__tests__/ReferralToolsDialog.test.tsx
```

Then replace the contents of `frontend/components/referrals/__tests__/ReferralToolsDialog.test.tsx`:

```typescript
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NextIntlClientProvider } from 'next-intl';
import ReferralToolsDialog from '../ReferralToolsDialog';

// Mock qr-code-styling
const mockAppend = jest.fn();
const mockUpdate = jest.fn();
const mockDownload = jest.fn().mockResolvedValue(undefined);
const mockGetRawData = jest.fn().mockResolvedValue(new Blob(['fake'], { type: 'image/png' }));

jest.mock('qr-code-styling', () => {
  return jest.fn().mockImplementation(() => ({
    append: mockAppend,
    update: mockUpdate,
    download: mockDownload,
    getRawData: mockGetRawData,
  }));
});

// Mock html2canvas
jest.mock('html2canvas', () => {
  return jest.fn().mockResolvedValue({
    toDataURL: jest.fn().mockReturnValue('data:image/png;base64,fake'),
    toBlob: jest.fn((cb: (b: Blob) => void) => cb(new Blob(['png']))),
    width: 1632,
    height: 2112,
  });
});

// Mock jsPDF
jest.mock('jspdf', () => {
  return jest.fn().mockImplementation(() => ({
    addImage: jest.fn(),
    save: jest.fn(),
    getImageProperties: jest.fn().mockReturnValue({ width: 1632, height: 2112 }),
    internal: { pageSize: { getWidth: () => 215.9, getHeight: () => 279.4 } },
    addPage: jest.fn(),
  }));
});

const messages = require('../../../messages/en.json');

function renderDialog(props: Partial<React.ComponentProps<typeof ReferralToolsDialog>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ReferralToolsDialog
        referralUrl="https://nomadkaraoke.com/r/testcode"
        open={true}
        onOpenChange={jest.fn()}
        referralCode="testcode"
        discountPercent={20}
        {...props}
      />
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('ReferralToolsDialog', () => {
  it('renders with tabs for QR Code and Flyer', () => {
    renderDialog();
    expect(screen.getByText('QR Code & Flyer Generator')).toBeInTheDocument();
    expect(screen.getByText('QR Code')).toBeInTheDocument();
    expect(screen.getByText('Printable Flyer')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    renderDialog({ open: false });
    expect(screen.queryByText('QR Code & Flyer Generator')).not.toBeInTheDocument();
  });

  it('shows QR Code tab content by default', () => {
    renderDialog();
    expect(screen.getByText('Dot Style')).toBeInTheDocument();
    expect(screen.getByText('Download PNG')).toBeInTheDocument();
    expect(screen.getByText('Download SVG')).toBeInTheDocument();
  });

  it('switches to Flyer tab when clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Download PDF')).toBeInTheDocument();
    expect(screen.getByText('Download HTML')).toBeInTheDocument();
    expect(screen.getByText('Download PNG')).toBeInTheDocument();
  });

  it('renders all QR dot style options on QR tab', () => {
    renderDialog();
    expect(screen.getAllByText('Square').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Rounded')).toBeInTheDocument();
    expect(screen.getByText('Dots')).toBeInTheDocument();
  });

  it('saves QR style prefs to localStorage on change', async () => {
    jest.useFakeTimers();
    renderDialog();
    fireEvent.click(screen.getByText('Rounded'));
    act(() => { jest.runAllTimers(); });
    const saved = JSON.parse(localStorage.getItem('nk-qr-style-prefs') || '{}');
    expect(saved.dotStyle).toBe('rounded');
    jest.useRealTimers();
  });

  it('restores QR style prefs from localStorage on open', () => {
    localStorage.setItem('nk-qr-style-prefs', JSON.stringify({
      dotStyle: 'dots',
      cornerSquareStyle: 'dot',
      cornerDotStyle: 'dot',
      fgColor: '#ff0000',
      bgColor: '#00ff00',
      logo: 'nomad',
      logoEmoji: '🎤',
    }));
    renderDialog();
    const fgInput = screen.getByDisplayValue('#ff0000');
    expect(fgInput).toBeInTheDocument();
  });

  it('renders flyer controls on Flyer tab', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    expect(screen.getByText('Colors')).toBeInTheDocument();
    expect(screen.getByText('Sections')).toBeInTheDocument();
    expect(screen.getByText('Print Layout')).toBeInTheDocument();
  });

  it('persists flyer config to localStorage', async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    renderDialog();
    await user.click(screen.getByText('Printable Flyer'));
    // Click "2" for perPage
    await user.click(screen.getByRole('button', { name: '2' }));
    act(() => { jest.runAllTimers(); });
    const saved = JSON.parse(localStorage.getItem('nk-flyer-prefs') || '{}');
    expect(saved.perPage).toBe(2);
    jest.useRealTimers();
  });
});
```

- [ ] **Step 2: Run all referral tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest components/referrals/__tests__/ --no-coverage 2>&1 | tail -30
```

Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add frontend/components/referrals/__tests__/ && git commit -m "test: update tests for ReferralToolsDialog with QR and Flyer tabs"
```

---

### Task 12: Run full test suite and fix any issues

**Files:** Various (depends on what breaks)

- [ ] **Step 1: Run full frontend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx jest --no-coverage 2>&1 | tail -40
```

Expected: All tests PASS

- [ ] **Step 2: Run TypeScript compilation check**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation/frontend && npx tsc --noEmit --pretty 2>&1 | tail -30
```

Expected: No errors

- [ ] **Step 3: Fix any issues found in steps 1-2**

If any tests fail or TypeScript errors appear, fix them. Common issues:
- Import paths not updated in test files
- Missing i18n keys causing runtime errors in tests
- Type mismatches between QRCodeTab exports and FlyerTab imports

- [ ] **Step 4: Commit fixes if any**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add -A && git commit -m "fix: resolve test and type issues from dialog refactor"
```

---

### Task 13: Version bump

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Check current version**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && grep 'version' pyproject.toml | head -3
```

- [ ] **Step 2: Bump minor version**

Increment the minor version (e.g. `1.2.3` → `1.3.0`) since this is a new feature.

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-referral-flyer-customisation && git add pyproject.toml && git commit -m "chore: bump version for flyer customisation feature"
```
