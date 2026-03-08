"use client"

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
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            How should your video be shared?
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Choose how your finished karaoke video will be delivered.
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
          Back
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
            <span className="font-semibold" style={{ color: 'var(--text)' }}>Publish & Share</span>
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
            <span className="font-semibold" style={{ color: 'var(--text)' }}>Keep Private</span>
            <ArrowRight className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
          </div>
        </button>
      </div>

      <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
        You can change the visibility anytime after the video is produced.
      </p>

      {/* Divider */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-px" style={{ backgroundColor: 'var(--card-border)' }} />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>What&apos;s the difference?</span>
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
                Published
              </span>
              <span
                className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: 'rgba(255,122,204,0.15)', color: 'var(--brand-pink)' }}
              >
                Recommended
              </span>
            </div>

            <div className="space-y-2 text-sm" style={{ color: 'var(--text-muted)' }}>
              <p>Your karaoke video will be <span className="font-semibold" style={{ color: '#f59e0b' }}>shared with the world</span>:</p>

              <ul className="space-y-2 ml-1">
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Uploaded to YouTube</strong> on the{" "}
                    <a
                      href="https://www.youtube.com/@nomadkaraoke/videos"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 underline"
                      style={{ color: 'var(--brand-pink)' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      Nomad Karaoke channel
                      <ExternalLink className="w-3 h-3" />
                    </a>
                    {" "}&mdash; the easiest way for you (and anyone else) to sing this song anytime
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Shared with karaoke venues</strong> via Google Drive &mdash; your song will be available to sing at bars and venues around the world
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: 'var(--brand-pink)' }} />
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Source files delivered to you</strong> via Dropbox &mdash; all stems, highest quality formats, ready to download
                  </span>
                </li>
              </ul>

              <div
                className="rounded-md p-2.5 mt-1 text-xs"
                style={{ backgroundColor: 'rgba(255,122,204,0.08)', color: 'var(--text)' }}
              >
                By publishing, you&apos;re helping other fans of this song discover and enjoy it at karaoke nights everywhere. Share the joy!
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
                Private
              </span>
            </div>

            <div className="space-y-2 text-sm" style={{ color: 'var(--text-muted)' }}>
              <p>Your karaoke video stays just with you:</p>

              <ul className="space-y-2 ml-1">
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Delivered via Dropbox only</strong> &mdash; no YouTube upload, no Google Drive sharing. Download and use however you see fit.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Venue use is up to you</strong> &mdash; if you want to perform this track at a venue, you&apos;ll need to provide the files to the host yourself.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>
                    <strong style={{ color: 'var(--text)' }}>YouTube self-upload</strong> &mdash; you can upload to your own channel, but monetization is unlikely as song copyrights are typically held by record labels.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 flex items-center justify-center text-[10px]">&bull;</span>
                  <span>
                    <strong style={{ color: 'var(--text)' }}>Custom styling available</strong> &mdash; choose your own background images, colors, and text styles on the next step.
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </button>

    </div>
  )
}
