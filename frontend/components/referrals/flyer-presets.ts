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

const DEFAULT_STEPS: Array<{ title: string | null; desc: string | null }> = [
  { title: null, desc: null },
  { title: null, desc: null },
  { title: null, desc: null },
  { title: null, desc: null },
];

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
  steps: [...DEFAULT_STEPS],
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
  steps: [...DEFAULT_STEPS],
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

const isBrowser = typeof window !== 'undefined';

export function loadFlyerConfig(): FlyerConfig {
  if (!isBrowser) return { ...DEFAULT_FLYER_CONFIG };
  try {
    const saved = localStorage.getItem(FLYER_CONFIG_KEY);
    if (saved) {
      return { ...DEFAULT_FLYER_CONFIG, ...JSON.parse(saved) };
    }
  } catch {}
  return { ...DEFAULT_FLYER_CONFIG };
}

export function saveFlyerConfig(config: FlyerConfig): void {
  if (!isBrowser) return;
  try {
    localStorage.setItem(FLYER_CONFIG_KEY, JSON.stringify(config));
  } catch {}
}

export function loadCustomPresets(): NamedPreset[] {
  if (!isBrowser) return [];
  try {
    const saved = localStorage.getItem(CUSTOM_PRESETS_KEY);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch {}
  return [];
}

export function saveCustomPreset(name: string, config: FlyerConfig): void {
  if (!isBrowser) return;
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
  if (!isBrowser) return;
  const presets = loadCustomPresets().filter(p => p.name !== name);
  try {
    localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(presets));
  } catch {}
}
