'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'

interface TimingOffsetModalProps {
  open: boolean
  onClose: () => void
  currentOffset: number
  onApply: (offsetMs: number) => void
}

export default function TimingOffsetModal({
  open,
  onClose,
  currentOffset,
  onApply,
}: TimingOffsetModalProps) {
  const [offset, setOffset] = useState(currentOffset)

  const handleApply = () => {
    onApply(offset)
    onClose()
  }

  const handleReset = () => {
    setOffset(0)
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Adjust Timing Offset</DialogTitle>
        </DialogHeader>

        <div className="space-y-6">
          <div className="space-y-2">
            <Label>Offset (milliseconds)</Label>
            <div className="flex gap-4 items-center">
              <Slider
                value={[offset]}
                min={-5000}
                max={5000}
                step={10}
                onValueChange={([value]) => setOffset(value)}
                className="flex-1"
              />
              <Input
                type="number"
                value={offset}
                onChange={(e) => setOffset(Number(e.target.value))}
                className="w-24"
              />
            </div>
            <p className="text-sm text-muted-foreground">
              Positive values shift lyrics later, negative values shift them earlier.
            </p>
          </div>

          <div className="bg-muted p-3 rounded space-y-1">
            <p className="text-sm">
              <span className="text-muted-foreground">Current offset:</span>{' '}
              <span className="font-mono">{currentOffset}ms</span>
            </p>
            <p className="text-sm">
              <span className="text-muted-foreground">New offset:</span>{' '}
              <span className="font-mono font-medium">{offset}ms</span>
            </p>
            <p className="text-sm">
              <span className="text-muted-foreground">Change:</span>{' '}
              <span className="font-mono">{offset - currentOffset}ms</span>
            </p>
          </div>
        </div>

        <DialogFooter className="flex gap-2 sm:justify-between">
          <Button variant="outline" onClick={handleReset}>
            Reset to 0
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleApply}>Apply</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
