'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import { LyricsSegment, Word } from '@/lib/lyrics-review/types'

interface UseManualSyncProps {
  editedSegment: LyricsSegment | null
  currentTime: number
  onPlaySegment?: (startTime: number) => void
  updateSegment: (words: Word[]) => void
  onTimingClamped?: (wordText: string, snappedTo: number) => void
}

// Manual sync plays this many seconds before the segment starts, so the user can hear the
// run-up and tap the very first word on time. Taps anywhere in that lead-in are legitimate.
const LEAD_IN_SECONDS = 3
// Symmetric window AFTER the segment end: the user may tap/hold the last word past the old end
// (extending the segment). Taps within this lead-out are legitimate and grow the segment.
const LEAD_OUT_SECONDS = 3

/**
 * Clamp a manual-sync tap time into the segment's *playback* window `[segStart - leadIn, segEnd + leadOut]`.
 *
 * A tap should land within the window Tap To Sync actually plays/allows. Playback starts `leadIn`
 * seconds BEFORE the segment (see LEAD_IN_SECONDS) because the user hears the run-up and often taps
 * the first word just before the old segment start (especially when re-syncing to move it earlier).
 * Symmetrically, `leadOut` lets the last word be tapped/held just past the old end to extend the
 * segment. Only values outside that widened window (e.g. the playhead stuck at 0 for a mid-song
 * segment) are sync glitches worth clamping. Clamping to the tight `[segStart, segEnd]` instead
 * would (a) shove every legitimate lead-in tap up to segStart, (b) ratchet the segment start forward
 * so it could never be re-synced earlier, and (c) hard-cut the last word at the old end. The lower
 * bound is floored at 0. `leadIn`/`leadOut` default to 0 for back-compat; pass the constants from
 * the sync handlers. Null start/end => that bound is skipped.
 */
export function clampSyncTime(
  time: number,
  segStart: number | null,
  segEnd: number | null,
  leadIn: number = 0,
  leadOut: number = 0
): number {
  let t = time
  if (typeof segStart === 'number' && Number.isFinite(segStart)) {
    t = Math.max(t, Math.max(0, segStart - leadIn))
  }
  if (typeof segEnd === 'number' && Number.isFinite(segEnd)) t = Math.min(t, segEnd + leadOut)
  return t
}

// Constants for tap detection
const TAP_THRESHOLD_MS = 200 // If spacebar is pressed for less than this time, it's considered a tap
const DEFAULT_WORD_DURATION = 0.5 // Default duration in seconds when tapping (500ms)
const OVERLAP_BUFFER = 0.01 // Buffer to prevent word overlap (10ms)

export default function useManualSync({
  editedSegment,
  currentTime,
  onPlaySegment,
  updateSegment,
  onTimingClamped,
}: UseManualSyncProps) {
  const [isManualSyncing, setIsManualSyncing] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [syncWordIndex, setSyncWordIndex] = useState<number>(-1)
  const currentTimeRef = useRef(currentTime)
  const [isSpacebarPressed, setIsSpacebarPressed] = useState(false)
  const wordStartTimeRef = useRef<number | null>(null)
  const wordsRef = useRef<Word[]>([])
  const spacebarPressTimeRef = useRef<number | null>(null)

  // Use ref to track if we need to update segment to avoid calling it too frequently
  const needsSegmentUpdateRef = useRef(false)

  // Keep currentTimeRef up to date
  useEffect(() => {
    currentTimeRef.current = currentTime
  }, [currentTime])

  // Mirror isSpacebarPressed into a ref so the auto-stop interval can read the live value
  // (its closure would otherwise capture a stale state snapshot) without restarting the interval.
  const isSpacebarPressedRef = useRef(isSpacebarPressed)
  useEffect(() => {
    isSpacebarPressedRef.current = isSpacebarPressed
  }, [isSpacebarPressed])

  // Keep wordsRef up to date
  useEffect(() => {
    if (editedSegment) {
      wordsRef.current = [...editedSegment.words]
    }
  }, [editedSegment])

  // Debounced segment update to batch multiple word changes
  useEffect(() => {
    if (needsSegmentUpdateRef.current) {
      needsSegmentUpdateRef.current = false
      updateSegment(wordsRef.current)
    }
  }, [updateSegment, syncWordIndex]) // Only update when syncWordIndex changes

  const cleanupManualSync = useCallback(() => {
    setIsManualSyncing(false)
    setIsPaused(false)
    setSyncWordIndex(-1)
    setIsSpacebarPressed(false)
    wordStartTimeRef.current = null
    spacebarPressTimeRef.current = null
    needsSegmentUpdateRef.current = false

    // Stop audio playback when cleaning up manual sync
    if (window.toggleAudioPlayback && window.isAudioPlaying) {
      window.toggleAudioPlayback()
    }
  }, [])

  const pauseManualSync = useCallback(() => {
    if (isManualSyncing && !isPaused) {
      setIsPaused(true)
      // Pause audio playback
      if (window.toggleAudioPlayback && window.isAudioPlaying) {
        window.toggleAudioPlayback()
      }
    }
  }, [isManualSyncing, isPaused])

  const resumeManualSync = useCallback(() => {
    if (isManualSyncing && isPaused) {
      setIsPaused(false)

      // Find the first unsynced word and resume from there
      if (editedSegment) {
        const firstUnsyncedIndex = editedSegment.words.findIndex(
          (word) => word.start_time === null || word.end_time === null
        )

        if (firstUnsyncedIndex !== -1 && firstUnsyncedIndex !== syncWordIndex) {
          setSyncWordIndex(firstUnsyncedIndex)
        }
      }

      // Resume audio playback if we have an onPlaySegment function
      if (onPlaySegment && currentTimeRef.current !== undefined) {
        onPlaySegment(currentTimeRef.current)
      }
    }
  }, [isManualSyncing, isPaused, onPlaySegment, editedSegment, syncWordIndex])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.code !== 'Space') return

      e.preventDefault()
      e.stopPropagation()

      if (isManualSyncing && editedSegment && !isSpacebarPressed && !isPaused) {
        setIsSpacebarPressed(true)

        // Record the start time of the current word
        wordStartTimeRef.current = currentTimeRef.current

        // Record when the spacebar was pressed (for tap detection)
        spacebarPressTimeRef.current = Date.now()

        // Update the word's start time immediately
        if (syncWordIndex < editedSegment.words.length) {
          const newWords = [...wordsRef.current]
          const currentWord = newWords[syncWordIndex]
          const rawStartTime = currentTimeRef.current
          // Keep this clamp+callback block in sync with the other sync path
          const currentStartTime = clampSyncTime(
            rawStartTime,
            editedSegment?.start_time ?? null,
            editedSegment?.end_time ?? null,
            LEAD_IN_SECONDS,
            LEAD_OUT_SECONDS
          )
          if (currentStartTime !== rawStartTime) {
            onTimingClamped?.(newWords[syncWordIndex]?.text ?? '', currentStartTime)
          }

          // Set the start time for the current word
          currentWord.start_time = currentStartTime

          // Handle the end time of the previous word (if it exists)
          if (syncWordIndex > 0) {
            const previousWord = newWords[syncWordIndex - 1]
            if (previousWord.start_time !== null) {
              const timeSincePreviousStart = currentStartTime - previousWord.start_time

              const needsAdjustment =
                previousWord.end_time === null ||
                (previousWord.end_time !== null && previousWord.end_time > currentStartTime)

              if (needsAdjustment) {
                if (timeSincePreviousStart > 1.0) {
                  // Gap of over 1 second - set previous word's end time to 500ms after its start
                  previousWord.end_time = previousWord.start_time + 0.5
                } else {
                  // Normal flow - set previous word's end time to current word's start time minus 5ms
                  previousWord.end_time = Math.max(currentStartTime - 0.005, previousWord.start_time ?? (currentStartTime - 0.005))
                }
              }
            }
          }

          // Update our ref
          wordsRef.current = newWords

          // Mark that we need to update the segment
          needsSegmentUpdateRef.current = true
        }
      } else if (!isManualSyncing && editedSegment && onPlaySegment) {
        // For global replacement segments, don't handle general playback
        if (editedSegment.id === 'global-replacement') {
          return
        }

        // Toggle segment playback when not in manual sync mode
        const startTime = editedSegment.start_time ?? 0
        const endTime = editedSegment.end_time ?? 0

        if (currentTimeRef.current >= startTime && currentTimeRef.current <= endTime) {
          if (window.toggleAudioPlayback) {
            window.toggleAudioPlayback()
          }
        } else {
          onPlaySegment(startTime)
        }
      }
    },
    [isManualSyncing, editedSegment, syncWordIndex, onPlaySegment, isSpacebarPressed, isPaused, onTimingClamped]
  )

  const handleKeyUp = useCallback(
    (e: KeyboardEvent) => {
      if (e.code !== 'Space') return

      e.preventDefault()
      e.stopPropagation()

      if (isManualSyncing && editedSegment && isSpacebarPressed && !isPaused) {
        const pressDuration = spacebarPressTimeRef.current
          ? Date.now() - spacebarPressTimeRef.current
          : 0
        const isTap = pressDuration < TAP_THRESHOLD_MS

        setIsSpacebarPressed(false)

        if (syncWordIndex < editedSegment.words.length) {
          const newWords = [...wordsRef.current]
          const currentWord = newWords[syncWordIndex]

          // Set the end time for the current word based on whether it was a tap or hold
          if (isTap) {
            // For a tap, set a default duration
            const defaultEndTime =
              (wordStartTimeRef.current || currentTimeRef.current) + DEFAULT_WORD_DURATION
            currentWord.end_time = Math.max(defaultEndTime, currentWord.start_time ?? defaultEndTime)
          } else {
            // For a hold, use the current time as the end time
            const rawEndTime = currentTimeRef.current
            currentWord.end_time = Math.max(rawEndTime, currentWord.start_time ?? rawEndTime)
          }

          // Update our ref
          wordsRef.current = newWords

          // Move to the next word
          if (syncWordIndex === editedSegment.words.length - 1) {
            // If this was the last word, finish manual sync
            setIsManualSyncing(false)
            setSyncWordIndex(-1)
            wordStartTimeRef.current = null
            spacebarPressTimeRef.current = null
          } else {
            // Otherwise, move to the next word
            setSyncWordIndex(syncWordIndex + 1)
          }

          // Mark that we need to update the segment
          needsSegmentUpdateRef.current = true
        }
      }
    },
    [isManualSyncing, editedSegment, syncWordIndex, isSpacebarPressed, isPaused]
  )

  // Safety net for when we advance to the next word: if the word we just synced ends
  // after the *next* word's start, pull its end back so they don't overlap.
  //
  // Two guards are essential here. When this effect fires, `currentWord` (the next word)
  // has NOT been re-synced yet — it still holds its OLD start_time from a previous sync.
  // So we must only act when that start is genuinely *later* than the previous word's
  // start (`currentWord.start_time > prevWord.start_time`); otherwise a re-sync that moves
  // words to later times would pull the just-tapped word's end back to a stale, earlier
  // timestamp — producing end_time < start_time (a negative-duration word the segment
  // sanitizer then has to "fix" on reopen). We also clamp the new end to never fall below
  // the previous word's own start_time.
  useEffect(() => {
    if (isManualSyncing && editedSegment && syncWordIndex > 0) {
      const newWords = [...wordsRef.current]
      const prevWord = newWords[syncWordIndex - 1]
      const currentWord = newWords[syncWordIndex]

      if (
        prevWord &&
        currentWord &&
        prevWord.end_time !== null &&
        prevWord.start_time !== null &&
        currentWord.start_time !== null &&
        currentWord.start_time > prevWord.start_time &&
        prevWord.end_time > currentWord.start_time
      ) {
        prevWord.end_time = Math.max(currentWord.start_time - OVERLAP_BUFFER, prevWord.start_time)

        // Update our ref
        wordsRef.current = newWords

        // Mark that we need to update the segment
        needsSegmentUpdateRef.current = true
      }
    }
  }, [syncWordIndex, isManualSyncing, editedSegment])

  // Combine the key handlers into a single function for external use
  const handleSpacebar = useCallback(
    (e: KeyboardEvent) => {
      if (e.type === 'keydown') {
        handleKeyDown(e)
      } else if (e.type === 'keyup') {
        handleKeyUp(e)
      }
    },
    [handleKeyDown, handleKeyUp]
  )

  // Touch-friendly handlers for mobile (simulates spacebar press/release)
  const handleTapStart = useCallback(() => {
    if (!isManualSyncing || !editedSegment || isSpacebarPressed || isPaused) return

    setIsSpacebarPressed(true)

    // Record the start time of the current word
    wordStartTimeRef.current = currentTimeRef.current

    // Record when the tap started (for tap detection)
    spacebarPressTimeRef.current = Date.now()

    // Update the word's start time immediately
    if (syncWordIndex < editedSegment.words.length) {
      const newWords = [...wordsRef.current]
      const currentWord = newWords[syncWordIndex]
      const rawStartTime = currentTimeRef.current
      // Keep this clamp+callback block in sync with the other sync path
      const currentStartTime = clampSyncTime(
        rawStartTime,
        editedSegment?.start_time ?? null,
        editedSegment?.end_time ?? null
      )
      if (currentStartTime !== rawStartTime) {
        onTimingClamped?.(newWords[syncWordIndex]?.text ?? '', currentStartTime)
      }

      // Set the start time for the current word
      currentWord.start_time = currentStartTime

      // Handle the end time of the previous word (if it exists)
      if (syncWordIndex > 0) {
        const previousWord = newWords[syncWordIndex - 1]
        if (previousWord.start_time !== null) {
          const timeSincePreviousStart = currentStartTime - previousWord.start_time

          const needsAdjustment =
            previousWord.end_time === null ||
            (previousWord.end_time !== null && previousWord.end_time > currentStartTime)

          if (needsAdjustment) {
            if (timeSincePreviousStart > 1.0) {
              previousWord.end_time = previousWord.start_time + 0.5
            } else {
              previousWord.end_time = Math.max(currentStartTime - 0.005, previousWord.start_time ?? (currentStartTime - 0.005))
            }
          }
        }
      }

      // Update our ref
      wordsRef.current = newWords

      // Mark that we need to update the segment
      needsSegmentUpdateRef.current = true
    }
  }, [isManualSyncing, editedSegment, syncWordIndex, isSpacebarPressed, isPaused, onTimingClamped])

  const handleTapEnd = useCallback(() => {
    if (!isManualSyncing || !editedSegment || !isSpacebarPressed || isPaused) return

    const pressDuration = spacebarPressTimeRef.current
      ? Date.now() - spacebarPressTimeRef.current
      : 0
    const isTap = pressDuration < TAP_THRESHOLD_MS

    setIsSpacebarPressed(false)

    if (syncWordIndex < editedSegment.words.length) {
      const newWords = [...wordsRef.current]
      const currentWord = newWords[syncWordIndex]

      // Set the end time for the current word based on whether it was a tap or hold
      if (isTap) {
        const defaultEndTime =
          (wordStartTimeRef.current || currentTimeRef.current) + DEFAULT_WORD_DURATION
        currentWord.end_time = Math.max(defaultEndTime, currentWord.start_time ?? defaultEndTime)
      } else {
        const rawEndTime = currentTimeRef.current
        currentWord.end_time = Math.max(rawEndTime, currentWord.start_time ?? rawEndTime)
      }

      // Update our ref
      wordsRef.current = newWords

      // Move to the next word
      if (syncWordIndex === editedSegment.words.length - 1) {
        // If this was the last word, finish manual sync
        setIsManualSyncing(false)
        setSyncWordIndex(-1)
        wordStartTimeRef.current = null
        spacebarPressTimeRef.current = null
      } else {
        // Otherwise, move to the next word
        setSyncWordIndex(syncWordIndex + 1)
      }

      // Mark that we need to update the segment
      needsSegmentUpdateRef.current = true
    }
  }, [isManualSyncing, editedSegment, syncWordIndex, isSpacebarPressed, isPaused])

  const startManualSync = useCallback(() => {
    if (isManualSyncing) {
      cleanupManualSync()
      return
    }

    if (!editedSegment || !onPlaySegment) return

    // Make sure we have the latest words
    wordsRef.current = [...editedSegment.words]

    // Find the first unsynced word to start from
    const firstUnsyncedIndex = editedSegment.words.findIndex(
      (word) => word.start_time === null || word.end_time === null
    )

    const startIndex = firstUnsyncedIndex !== -1 ? firstUnsyncedIndex : 0

    setIsManualSyncing(true)
    setSyncWordIndex(startIndex)
    setIsSpacebarPressed(false)
    wordStartTimeRef.current = null
    spacebarPressTimeRef.current = null
    needsSegmentUpdateRef.current = false
    // Start playing 3 seconds before segment start
    onPlaySegment((editedSegment.start_time ?? 0) - LEAD_IN_SECONDS)
  }, [isManualSyncing, editedSegment, onPlaySegment, cleanupManualSync])

  // Auto-stop sync if we go past the end time (but not for global replacement segments)
  useEffect(() => {
    if (!editedSegment || !isManualSyncing) return

    // Don't auto-stop for global replacement segments - let user manually finish
    if (editedSegment.id === 'global-replacement') {
      return
    }

    // Set up an interval to check if we should auto-stop
    const checkAutoStop = () => {
      const endTime = editedSegment.end_time ?? 0

      // Never cut the user off mid-tap/hold: they may be holding the LAST word past the old
      // segment end to extend it (updateSegment grows end_time once the word lands). Only stop
      // once playback is a full lead-out past the end AND the spacebar isn't currently down.
      if (isSpacebarPressedRef.current) return

      if (window.isAudioPlaying && currentTimeRef.current > endTime + LEAD_OUT_SECONDS) {
        window.toggleAudioPlayback?.()
        cleanupManualSync()
      }
    }

    // Check immediately and then every 100ms
    checkAutoStop()
    const intervalId = setInterval(checkAutoStop, 100)

    return () => {
      clearInterval(intervalId)
    }
  }, [isManualSyncing, editedSegment, cleanupManualSync])

  return {
    isManualSyncing,
    isPaused,
    syncWordIndex,
    startManualSync,
    pauseManualSync,
    resumeManualSync,
    cleanupManualSync,
    handleSpacebar,
    isSpacebarPressed,
    handleTapStart,
    handleTapEnd,
  }
}
