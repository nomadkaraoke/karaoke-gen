"use client"

import { useTranslations } from 'next-intl'
import { ArrowLeft, ArrowRight, Globe, Lock, ExternalLink, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"

interface VisibilityStepProps {
  isPrivate: boolean
  onPrivateChange: (value: boolean) => void
  onNext: () => void
  onBack: () => void
  disabled?: boolean
}

export function VisibilityStep({
  isPrivate,
  onPrivateChange,
  onNext,
  onBack,
  disabled,
}: VisibilityStepProps) {
  const t = useTranslations('jobFlow')
  const tc = useTranslations('common')
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            {t('howShouldVideoBeShared')}
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {t('chooseHowVideoDelivered')}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          disabled={disabled}
          style={{ color: 'var(--text-muted)' }}
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          {tc('back')}
        </Button>
      </div>

      {/* Action buttons — select and continue in one click */}
      <div className="flex flex-col sm:flex-row gap-3">
        <button
          type="button"
          onClick={() => { onPrivateChange(false); onNext(); }}
          disabled={disabled}
          className={`flex-1 rounded-lg border-2 p-4 transition-all text-center ${
            !isPrivate
              ? 'border-[var(--brand-pink)] shadow-[0_0_12px_rgba(255,122,204,0.2)]'
              : 'border-[var(--card-border)] hover:border-[var(--text-muted)]'
          }`}
          style={{ backgroundColor: !isPrivate ? 'rgba(255,122,204,0.05)' : 'transparent' }}
        >
          <div className="flex items-center justify-center gap-2">
            <Globe className="w-5 h-5" style={{ color: !isPrivate ? 'var(--brand-pink)' : 'var(--text-muted)' }} />
            <span className="font-semibold" style={{ color: 'var(--text)' }}>{t('publishShare')}</span>
            <ArrowRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
          </div>
        </button>

        <button
          type="button"
          onClick={() => { onPrivateChange(true); onNext(); }}
          disabled={disabled}
          className={`flex-1 rounded-lg border-2 p-4 transition-all text-center ${
            isPrivate
              ? 'border-[var(--brand-pink)] shadow-[0_0_12px_rgba(255,122,204,0.2)]'
              : 'border-[var(--card-border)] hover:border-[var(--text-muted)]'
          }`}
          style={{ backgroundColor: isPrivate ? 'rgba(255,122,204,0.05)' : 'transparent' }}
        >
          <div className="flex items-center justify-center gap-2">
            <Lock className="w-5 h-5" style={{ color: isPrivate ? 'var(--brand-pink)' : 'var(--text-muted)' }} />
            <span className="font-semibold" style={{ color: 'var(--text)' }}>{t('keepPrivate')}</span>
            <ArrowRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
          </div>
        </button>
      </div>

      <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
        {t('changeVisibilityAnytime')}
      </p>

      {/* Divider */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-px" style={{ backgroundColor: 'var(--card-border)' }} />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('whatsDifference')}</span>
        <div className="flex-1 h-px" style={{ backgroundColor: 'var(--card-border)' }} />
      </div>

      {/* Published detail card */}
      <button
        type="button"
        onClick={() => onPrivateChange(false)}
        disabled={disabled}
        className={`w-full text-left rounded-lg border-2 p-3 transition-all ${
          !isPrivate
            ? 'border-[var(--brand-pink)] shadow-[0_0_12px_rgba(255,122,204,0.2)]'
            : 'border-[var(--card-border)] hover:border-[var(--text-muted)]'
        }`}
        style={{ backgroundColor: !isPrivate ? 'rgba(255,122,204,0.05)' : 'transparent' }}
      >
        <div className="flex items-start gap-3">
          {/* Radio circle */}
          <div
            className={`mt-0.5 w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
              !isPrivate ? 'border-[var(--brand-pink)]' : 'border-[var(--text-muted)]'
            }`}
          >
            {!isPrivate && (
              <div className="w-2.5 h-2.5 rounded-full bg-[var(--brand-pink)]" />
            )}
          </div>

          <div className="flex-1 space-y-3">
            <div className="flex items-center gap-2">
              <Globe className="w-4 h-4" style={{ color: !isPrivate ? 'var(--brand-pink)' : 'var(--text-muted)' }} />
              <span className="font-semibold" style={{ color: 'var(--text)' }}>
                {t('published')}
              </span>
              <span
                className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: 'rgba(255,122,204,0.15)', color: 'var(--brand-pink)' }}
              >
                {t('recommended')}
              </span>
            </div>

            <div className="space-y-2 text-sm" style={{ color: 'var(--text-muted)' }}>
              <p>{t('videoSharedWithWorld')}</p>

              <ul className="space-y-2 ml-1">
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>{t('uploadedToYoutube')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>{t('sharedWithVenues')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>{t('sourceFilesDelivered')}</span>
                </li>
              </ul>

              <div
                className="rounded-md p-2.5 mt-1 text-xs"
                style={{ backgroundColor: 'rgba(255,122,204,0.08)', color: 'var(--text)' }}
              >
                {t('publishingHelpFans')}
              </div>
            </div>
          </div>
        </div>
      </button>

      {/* Private detail card */}
      <button
        type="button"
        onClick={() => onPrivateChange(true)}
        disabled={disabled}
        className={`w-full text-left rounded-lg border-2 p-3 transition-all ${
          isPrivate
            ? 'border-[var(--brand-pink)] shadow-[0_0_12px_rgba(255,122,204,0.2)]'
            : 'border-[var(--card-border)] hover:border-[var(--text-muted)]'
        }`}
        style={{ backgroundColor: isPrivate ? 'rgba(255,122,204,0.05)' : 'transparent' }}
      >
        <div className="flex items-start gap-3">
          {/* Radio circle */}
          <div
            className={`mt-0.5 w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
              isPrivate ? 'border-[var(--brand-pink)]' : 'border-[var(--text-muted)]'
            }`}
          >
            {isPrivate && (
              <div className="w-2.5 h-2.5 rounded-full bg-[var(--brand-pink)]" />
            )}
          </div>

          <div className="flex-1 space-y-3">
            <div className="flex items-center gap-2">
              <Lock className="w-4 h-4" style={{ color: isPrivate ? 'var(--brand-pink)' : 'var(--text-muted)' }} />
              <span className="font-semibold" style={{ color: 'var(--text)' }}>
                {t('private')}
              </span>
            </div>

            <div className="space-y-2 text-sm" style={{ color: 'var(--text-muted)' }}>
              <p>{t('videoStaysWithYou')}</p>

              <ul className="space-y-2 ml-1">
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>{t('deliveredViaDropboxOnly')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>{t('venueUseUpToYou')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>{t('youtubeUploadOwn')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>{t('customStylingAvailable')}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </button>

    </div>
  )
}
