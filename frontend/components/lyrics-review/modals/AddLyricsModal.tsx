'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Loader2 } from 'lucide-react'

interface AddLyricsModalProps {
  open: boolean
  onClose: () => void
  onAdd: (source: string, lyrics: string) => Promise<void>
}

export default function AddLyricsModal({ open, onClose, onAdd }: AddLyricsModalProps) {
  const [source, setSource] = useState('')
  const [lyrics, setLyrics] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAdd = async () => {
    if (!source.trim() || !lyrics.trim()) return

    setIsAdding(true)
    setError(null)

    try {
      await onAdd(source.trim(), lyrics.trim())
      setSource('')
      setLyrics('')
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add lyrics')
    } finally {
      setIsAdding(false)
    }
  }

  const handleClose = () => {
    if (!isAdding) {
      setSource('')
      setLyrics('')
      setError(null)
      onClose()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Reference Lyrics</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="source">Source Name</Label>
            <Input
              id="source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="e.g., manual, genius, azlyrics..."
              disabled={isAdding}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="lyrics">Lyrics</Label>
            <Textarea
              id="lyrics"
              value={lyrics}
              onChange={(e) => setLyrics(e.target.value)}
              placeholder="Paste lyrics here..."
              rows={10}
              disabled={isAdding}
              className="font-mono text-sm"
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={isAdding}>
            Cancel
          </Button>
          <Button
            onClick={handleAdd}
            disabled={isAdding || !source.trim() || !lyrics.trim()}
          >
            {isAdding ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Adding...
              </>
            ) : (
              'Add Lyrics'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
