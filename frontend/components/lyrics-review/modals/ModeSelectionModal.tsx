'use client'

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { RefreshCw, ClipboardPaste, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ModeSelectionModalProps {
  open: boolean
  onClose: () => void
  onSelectReplace: () => void
  onSelectResync: () => void
  hasExistingLyrics: boolean
}

export default function ModeSelectionModal({
  open,
  onClose,
  onSelectReplace,
  onSelectResync,
  hasExistingLyrics,
}: ModeSelectionModalProps) {
  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between">
            <span>Edit All Lyrics</span>
            <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">Choose how you want to edit the lyrics:</p>

          <div className="flex flex-col gap-3">
            {/* Re-sync Existing option - only show if there are existing lyrics */}
            {hasExistingLyrics && (
              <div
                className={cn(
                  'p-4 border-2 border-primary rounded-lg cursor-pointer',
                  'hover:bg-primary/10 transition-colors'
                )}
                onClick={onSelectResync}
              >
                <div className="flex items-start gap-3">
                  <RefreshCw className="h-10 w-10 text-primary mt-0.5" />
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-primary">Re-sync Existing Lyrics</h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      Keep the current lyrics text and fix timing issues. Use this when lyrics are
                      correct but timing has drifted, especially in the second half of the song.
                    </p>
                    <p className="text-xs text-green-500 mt-2">Recommended for fixing timing drift</p>
                  </div>
                </div>
              </div>
            )}

            {/* Replace All option */}
            <div
              className={cn(
                'p-4 border rounded-lg cursor-pointer',
                'hover:bg-muted/30 hover:border-muted-foreground transition-colors'
              )}
              onClick={onSelectReplace}
            >
              <div className="flex items-start gap-3">
                <ClipboardPaste className="h-10 w-10 text-muted-foreground mt-0.5" />
                <div className="flex-1">
                  <h3 className="text-lg font-semibold">Replace All Lyrics</h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Paste completely new lyrics from clipboard and manually sync timing for all
                    words from scratch.
                  </p>
                  <p className="text-xs text-yellow-500 mt-2">
                    All existing timing data will be lost
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
