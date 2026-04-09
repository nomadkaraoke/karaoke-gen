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

const STEP_PLACEHOLDERS = [
  { title: 'Pick any song', desc: 'Search by artist & title...' },
  { title: 'Our system does the work', desc: 'Vocals removed, lyrics synced...' },
  { title: 'Review & correct the lyrics and instrumental', desc: 'Fine-tune everything...' },
  { title: 'Download or publish to YouTube', desc: 'Get your finished karaoke video...' },
];

function detectActivePreset(config: FlyerConfig): string {
  const all = [...BUILT_IN_PRESETS, ...loadCustomPresets()];
  for (const preset of all) {
    if (preset.config.bgColor === config.bgColor
      && preset.config.textColor === config.textColor
      && preset.config.accentColor === config.accentColor
      && JSON.stringify(preset.config.headlineGradient) === JSON.stringify(config.headlineGradient)) {
      return preset.name;
    }
  }
  return 'Custom';
}

export default function FlyerControls({ config, onChange, onLoadPreset }: FlyerControlsProps) {
  const t = useTranslations('referrals');
  const [customPresets, setCustomPresets] = useState<NamedPreset[]>(loadCustomPresets);
  const [activePreset, setActivePreset] = useState<string>(() => detectActivePreset(config));
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
                  placeholder={STEP_PLACEHOLDERS[i].title}
                  className="w-full rounded border border-border bg-background text-foreground text-xs px-2 py-1"
                />
                <input
                  value={step.desc ?? ''}
                  onChange={(e) => handleStepChange(i, 'desc', e.target.value)}
                  placeholder={STEP_PLACEHOLDERS[i].desc}
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
