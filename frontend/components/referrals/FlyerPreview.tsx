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

// Inline SVG Nomad Karaoke wordmark (from the HTML template)
function NomadLogo({ color }: { color: string }) {
  return (
    <svg style={{ width: 260, height: 'auto' }} viewBox="45 60 255 135" xmlns="http://www.w3.org/2000/svg">
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

  // Scale flyer to fit its preview container
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const resize = () => {
      const flyerEl = container.querySelector('[data-flyer-page]') as HTMLElement;
      if (!flyerEl) return;
      const parentW = container.parentElement?.clientWidth || container.clientWidth;
      // Flyer: 8.5in x 11in at 96dpi = 816 x 1056 px
      const flyerW = 816;
      const flyerH = 1056;
      const scale = Math.min(parentW / flyerW, 1);
      flyerEl.style.transform = `scale(${scale})`;
      flyerEl.style.transformOrigin = 'top left';
      container.style.width = `${flyerW * scale}px`;
      container.style.height = `${flyerH * scale}px`;
    };

    const observer = new ResizeObserver(resize);
    observer.observe(container.parentElement || container);
    resize();
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
        data-flyer-page=""
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
        {/* Logo row */}
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
