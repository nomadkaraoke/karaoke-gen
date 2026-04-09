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
      expect(config.textColor).toBe('#1a1a1a');
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
