'use client'

import { useTranslations } from 'next-intl'
import { useState, useCallback, useEffect, useRef, useMemo, memo } from 'react'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { ZoomIn, ZoomOut, AlertTriangle } from 'lucide-react'
import { Word, LyricsSegment } from '@/lib/lyrics-review/types'
import TimelineCanvas from './TimelineCanvas'
import UpcomingWordsBar from './UpcomingWordsBar'
import SyncControls from './SyncControls'

declare global {
  interface Window {
    getAudioDuration?: () => number
    toggleAudioPlayback?: () => void
    isAudioPlaying?: boolean
  }
}

interface LyricsSynchronizerProps {
  segments: LyricsSegment[]
  currentTime: number
  onPlaySegment?: (startTime: number) => void
  onSave: (segments: LyricsSegment[]) => void
  onCancel: () => void
  setModalSpacebarHandler: (handler: ((e: KeyboardEvent) => void) | undefined) => void
}

// Constants for zoom
const MIN_ZOOM_SECONDS = 2
const MAX_ZOOM_SECONDS = 24
const ZOOM_STEPS = 50

// Get all words from segments
function getAllWords(segments: LyricsSegment[]): Word[] {
  return segments.flatMap((s) => s.words)
}

// Deep clone segments
function cloneSegments(segments: LyricsSegment[]): LyricsSegment[] {
  return JSON.parse(JSON.stringify(segments))
}

const LyricsSynchronizer = memo(function LyricsSynchronizer({
  segments: initialSegments,
  currentTime,
  onPlaySegment,
  onSave,
  onCancel,
  setModalSpacebarHandler,
}: LyricsSynchronizerProps) {
  const t = useTranslations('lyricsReview.synchronizer')
  const isDarkMode = typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  // Working copy of segments
  const [workingSegments, setWorkingSegments] = useState<LyricsSegment[]>(() =>
    cloneSegments(initialSegments)
  )

  // Get all words flattened
  const allWords = useMemo(() => getAllWords(workingSegments), [workingSegments])

  // Audio duration
  const audioDuration = useMemo(() => {
    if (typeof window !== 'undefined' && typeof window.getAudioDuration === 'function') {
      const duration = window.getAudioDuration()
      return duration > 0 ? duration : 300
    }
    return 300 // 5 minute fallback
  }, [])

  // Zoom state
  const [zoomSeconds, setZoomSeconds] = useState(12)

  // Visible time range
  const [visibleStartTime, setVisibleStartTime] = useState(0)
  const visibleEndTime = useMemo(
    () => Math.min(visibleStartTime + zoomSeconds, audioDuration),
    [visibleStartTime, zoomSeconds, audioDuration]
  )

  // Manual sync state
  const [isManualSyncing, setIsManualSyncing] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [syncWordIndex, setSyncWordIndex] = useState(-1)
  const [isSpacebarPressed, setIsSpacebarPressed] = useState(false)
  const wordStartTimeRef = useRef<number | null>(null)
  const spacebarPressTimeRef = useRef<number | null>(null)
  const currentTimeRef = useRef(currentTime)

  // Selection state
  const [selectedWordIds, setSelectedWordIds] = useState<Set<string>>(new Set())

  // Edit lyrics modal state
  const [showEditLyricsModal, setShowEditLyricsModal] = useState(false)
  const [editLyricsText, setEditLyricsText] = useState('')

  // Edit word modal state
  const [showEditWordModal, setShowEditWordModal] = useState(false)
  const [editWordText, setEditWordText] = useState('')
  const [editWordId, setEditWordId] = useState<string | null>(null)

  // Keep currentTimeRef up to date
  useEffect(() => {
    currentTimeRef.current = currentTime
  }, [currentTime])

  // Auto-scroll to follow playhead during sync
  useEffect(() => {
    if (isManualSyncing && !isPaused && currentTime > 0) {
      if (currentTime > visibleEndTime - zoomSeconds * 0.1) {
        const newStart = Math.max(0, currentTime - zoomSeconds * 0.1)
        setVisibleStartTime(newStart)
      } else if (currentTime < visibleStartTime) {
        setVisibleStartTime(Math.max(0, currentTime - 1))
      }
    }
  }, [currentTime, isManualSyncing, isPaused, visibleStartTime, visibleEndTime, zoomSeconds])

  // Handle zoom slider change
  const handleZoomChange = useCallback((value: number[]) => {
    const zoomValue = value[0]
    const newZoomSeconds =
      MIN_ZOOM_SECONDS + (zoomValue / ZOOM_STEPS) * (MAX_ZOOM_SECONDS - MIN_ZOOM_SECONDS)
    setZoomSeconds(newZoomSeconds)
  }, [])

  // Get slider value from zoom seconds
  const sliderValue = useMemo(() => {
    return ((zoomSeconds - MIN_ZOOM_SECONDS) / (MAX_ZOOM_SECONDS - MIN_ZOOM_SECONDS)) * ZOOM_STEPS
  }, [zoomSeconds])

  // Handle scroll change from timeline
  const handleScrollChange = useCallback((newStartTime: number) => {
    setVisibleStartTime(newStartTime)
  }, [])

  // Update words in segments
  const updateWords = useCallback((newWords: Word[]) => {
    setWorkingSegments((prevSegments) => {
      const newSegments = cloneSegments(prevSegments)
      const wordMap = new Map(newWords.map((w) => [w.id, w]))

      for (const segment of newSegments) {
        segment.words = segment.words.map((w) => wordMap.get(w.id) || w)

        const timedWords = segment.words.filter(
          (w) => w.start_time !== null && w.end_time !== null
        )

        if (timedWords.length > 0) {
          segment.start_time = Math.min(...timedWords.map((w) => w.start_time!))
          segment.end_time = Math.max(...timedWords.map((w) => w.end_time!))
        } else {
          segment.start_time = null
          segment.end_time = null
        }
      }

      return newSegments
    })
  }, [])

  // Check if audio is playing
  const [isPlaying, setIsPlaying] = useState(false)

  useEffect(() => {
    const checkPlaying = () => {
      setIsPlaying(
        typeof window !== 'undefined' && typeof window.isAudioPlaying === 'boolean'
          ? window.isAudioPlaying
          : false
      )
    }
    checkPlaying()
    const interval = setInterval(checkPlaying, 100)
    return () => clearInterval(interval)
  }, [])

  // Play audio from current position
  const handlePlayAudio = useCallback(() => {
    if (onPlaySegment) {
      onPlaySegment(currentTimeRef.current)
    }
  }, [onPlaySegment])

  // Stop audio playback
  const handleStopAudio = useCallback(() => {
    if (typeof window !== 'undefined' && typeof window.toggleAudioPlayback === 'function' && window.isAudioPlaying) {
      window.toggleAudioPlayback()
    }
    if (isManualSyncing) {
      setIsManualSyncing(false)
      setIsPaused(false)
      setIsSpacebarPressed(false)
    }
  }, [isManualSyncing])

  // Start manual sync
  const handleStartSync = useCallback(() => {
    if (isManualSyncing) {
      setIsManualSyncing(false)
      setIsPaused(false)
      setSyncWordIndex(-1)
      setIsSpacebarPressed(false)
      handleStopAudio()
      return
    }

    const firstUnsyncedIndex = allWords.findIndex(
      (w) => w.start_time === null || w.end_time === null
    )

    const startIndex = firstUnsyncedIndex !== -1 ? firstUnsyncedIndex : 0

    setIsManualSyncing(true)
    setIsPaused(false)
    setSyncWordIndex(startIndex)
    setIsSpacebarPressed(false)

    if (onPlaySegment) {
      onPlaySegment(Math.max(0, currentTimeRef.current - 1))
    }
  }, [isManualSyncing, allWords, onPlaySegment, handleStopAudio])

  // Pause sync
  const handlePauseSync = useCallback(() => {
    setIsPaused(true)
    handleStopAudio()
  }, [handleStopAudio])

  // Resume sync
  const handleResumeSync = useCallback(() => {
    setIsPaused(false)

    const firstUnsyncedIndex = allWords.findIndex(
      (w) => w.start_time === null || w.end_time === null
    )

    if (firstUnsyncedIndex !== -1 && firstUnsyncedIndex !== syncWordIndex) {
      setSyncWordIndex(firstUnsyncedIndex)
    }

    if (onPlaySegment) {
      onPlaySegment(currentTimeRef.current)
    }
  }, [allWords, syncWordIndex, onPlaySegment])

  // Clear all sync data
  const handleClearSync = useCallback(() => {
    setWorkingSegments((prevSegments) => {
      const newSegments = cloneSegments(prevSegments)

      for (const segment of newSegments) {
        for (const word of segment.words) {
          word.start_time = null
          word.end_time = null
        }
        segment.start_time = null
        segment.end_time = null
      }

      return newSegments
    })

    setSyncWordIndex(-1)
  }, [])

  // Unsync from cursor position
  const handleUnsyncFromCursor = useCallback(() => {
    const cursorTime = currentTimeRef.current

    setWorkingSegments((prevSegments) => {
      const newSegments = cloneSegments(prevSegments)

      for (const segment of newSegments) {
        for (const word of segment.words) {
          if (word.start_time !== null && word.start_time > cursorTime) {
            word.start_time = null
            word.end_time = null
          }
        }

        const timedWords = segment.words.filter(
          (w) => w.start_time !== null && w.end_time !== null
        )

        if (timedWords.length > 0) {
          segment.start_time = Math.min(...timedWords.map((w) => w.start_time!))
          segment.end_time = Math.max(...timedWords.map((w) => w.end_time!))
        } else {
          segment.start_time = null
          segment.end_time = null
        }
      }

      return newSegments
    })
  }, [])

  // Check if there are words after cursor that can be unsynced
  const canUnsyncFromCursor = useMemo(() => {
    const cursorTime = currentTimeRef.current
    return allWords.some((w) => w.start_time !== null && w.start_time > cursorTime)
  }, [allWords, currentTime])

  // Open edit lyrics modal
  const handleEditLyrics = useCallback(() => {
    const text = workingSegments.map((s) => s.text).join('\n')
    setEditLyricsText(text)
    setShowEditLyricsModal(true)
  }, [workingSegments])

  // Save edited lyrics
  const handleSaveEditedLyrics = useCallback(() => {
    const lines = editLyricsText.split('\n').filter((l) => l.trim())

    const newSegments: LyricsSegment[] = lines.map((line, idx) => {
      const words = line
        .trim()
        .split(/\s+/)
        .map((text, wIdx) => ({
          id: `word-${idx}-${wIdx}-${Date.now()}`,
          text,
          start_time: null,
          end_time: null,
          confidence: 1.0,
        }))

      return {
        id: `segment-${idx}-${Date.now()}`,
        text: line.trim(),
        words,
        start_time: null,
        end_time: null,
      }
    })

    setWorkingSegments(newSegments)
    setShowEditLyricsModal(false)
    setSyncWordIndex(-1)
  }, [editLyricsText])

  // Open edit word modal
  const handleEditSelectedWord = useCallback(() => {
    if (selectedWordIds.size !== 1) return

    const wordId = Array.from(selectedWordIds)[0]
    const word = allWords.find((w) => w.id === wordId)

    if (word) {
      setEditWordId(wordId)
      setEditWordText(word.text)
      setShowEditWordModal(true)
    }
  }, [selectedWordIds, allWords])

  // Save edited word
  const handleSaveEditedWord = useCallback(() => {
    if (!editWordId) return

    const newText = editWordText.trim()
    if (!newText) return

    const newWords = newText.split(/\s+/)

    if (newWords.length === 1) {
      const updatedWords = allWords.map((w) =>
        w.id === editWordId ? { ...w, text: newWords[0] } : w
      )
      updateWords(updatedWords)
    } else {
      const originalWord = allWords.find((w) => w.id === editWordId)
      if (!originalWord) return

      setWorkingSegments((prevSegments) => {
        const newSegments = cloneSegments(prevSegments)

        for (const segment of newSegments) {
          const wordIndex = segment.words.findIndex((w) => w.id === editWordId)
          if (wordIndex !== -1) {
            const newWordObjects = newWords.map((text, idx) => ({
              id: idx === 0 ? editWordId : `${editWordId}-split-${idx}`,
              text,
              start_time: idx === 0 ? originalWord.start_time : null,
              end_time: idx === 0 ? originalWord.end_time : null,
              confidence: 1.0,
            }))

            segment.words.splice(wordIndex, 1, ...newWordObjects)
            segment.text = segment.words.map((w) => w.text).join(' ')
            break
          }
        }

        return newSegments
      })
    }

    setShowEditWordModal(false)
    setEditWordId(null)
    setEditWordText('')
    setSelectedWordIds(new Set())
  }, [editWordId, editWordText, allWords, updateWords])

  // Delete selected words
  const handleDeleteSelected = useCallback(() => {
    if (selectedWordIds.size === 0) return

    setWorkingSegments((prevSegments) => {
      const newSegments = cloneSegments(prevSegments)

      for (const segment of newSegments) {
        segment.words = segment.words.filter((w) => !selectedWordIds.has(w.id))
        segment.text = segment.words.map((w) => w.text).join(' ')

        const timedWords = segment.words.filter(
          (w) => w.start_time !== null && w.end_time !== null
        )

        if (timedWords.length > 0) {
          segment.start_time = Math.min(...timedWords.map((w) => w.start_time!))
          segment.end_time = Math.max(...timedWords.map((w) => w.end_time!))
        } else {
          segment.start_time = null
          segment.end_time = null
        }
      }

      return newSegments.filter((s) => s.words.length > 0)
    })

    setSelectedWordIds(new Set())
  }, [selectedWordIds])

  // Handle word click
  const handleWordClick = useCallback((wordId: string, event: React.MouseEvent) => {
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      setSelectedWordIds((prev) => {
        const newSet = new Set(prev)
        if (newSet.has(wordId)) {
          newSet.delete(wordId)
        } else {
          newSet.add(wordId)
        }
        return newSet
      })
    } else {
      setSelectedWordIds(new Set([wordId]))
    }
  }, [])

  // Handle background click
  const handleBackgroundClick = useCallback(() => {
    setSelectedWordIds(new Set())
  }, [])

  // Handle single word timing change
  const handleWordTimingChange = useCallback(
    (wordId: string, newStartTime: number, newEndTime: number) => {
      setWorkingSegments((prevSegments) => {
        const newSegments = cloneSegments(prevSegments)

        for (const segment of newSegments) {
          const word = segment.words.find((w) => w.id === wordId)
          if (word) {
            word.start_time = Math.max(0, newStartTime)
            word.end_time = Math.max(word.start_time + 0.05, newEndTime)

            const timedWords = segment.words.filter(
              (w) => w.start_time !== null && w.end_time !== null
            )
            if (timedWords.length > 0) {
              segment.start_time = Math.min(...timedWords.map((w) => w.start_time!))
              segment.end_time = Math.max(...timedWords.map((w) => w.end_time!))
            }
            break
          }
        }

        return newSegments
      })
    },
    []
  )

  // Handle moving multiple words
  const handleWordsMove = useCallback(
    (updates: Array<{ wordId: string; newStartTime: number; newEndTime: number }>) => {
      setWorkingSegments((prevSegments) => {
        const newSegments = cloneSegments(prevSegments)
        const updateMap = new Map(updates.map((u) => [u.wordId, u]))

        for (const segment of newSegments) {
          for (const word of segment.words) {
            const update = updateMap.get(word.id)
            if (update) {
              word.start_time = update.newStartTime
              word.end_time = update.newEndTime
            }
          }

          const timedWords = segment.words.filter(
            (w) => w.start_time !== null && w.end_time !== null
          )
          if (timedWords.length > 0) {
            segment.start_time = Math.min(...timedWords.map((w) => w.start_time!))
            segment.end_time = Math.max(...timedWords.map((w) => w.end_time!))
          }
        }

        return newSegments
      })
    },
    []
  )

  // Handle time bar click
  const handleTimeBarClick = useCallback(
    (time: number) => {
      const newStart = Math.max(0, time - zoomSeconds / 2)
      setVisibleStartTime(Math.min(newStart, Math.max(0, audioDuration - zoomSeconds)))

      if (onPlaySegment) {
        onPlaySegment(time)
        setTimeout(() => {
          if (typeof window !== 'undefined' && typeof window.toggleAudioPlayback === 'function' && window.isAudioPlaying) {
            window.toggleAudioPlayback()
          }
        }, 50)
      }
    },
    [zoomSeconds, audioDuration, onPlaySegment]
  )

  // Handle selection complete
  const handleSelectionComplete = useCallback((wordIds: string[]) => {
    setSelectedWordIds(new Set(wordIds))
  }, [])

  // Handle spacebar for manual sync
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      if (!isManualSyncing || isPaused) return
      if (syncWordIndex < 0 || syncWordIndex >= allWords.length) return

      e.preventDefault()
      e.stopPropagation()

      if (isSpacebarPressed) return

      setIsSpacebarPressed(true)
      wordStartTimeRef.current = currentTimeRef.current
      spacebarPressTimeRef.current = Date.now()

      const newWords = [...allWords]
      const currentWord = newWords[syncWordIndex]
      currentWord.start_time = currentTimeRef.current

      if (syncWordIndex > 0) {
        const prevWord = newWords[syncWordIndex - 1]
        if (prevWord.start_time !== null && prevWord.end_time === null) {
          const gap = currentTimeRef.current - prevWord.start_time
          if (gap > 1.0) {
            prevWord.end_time = prevWord.start_time + 0.5
          } else {
            prevWord.end_time = currentTimeRef.current - 0.005
          }
        }
      }

      updateWords(newWords)
    },
    [isManualSyncing, isPaused, syncWordIndex, allWords, isSpacebarPressed, updateWords]
  )

  const handleKeyUp = useCallback(
    (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      if (!isManualSyncing || isPaused) return
      if (!isSpacebarPressed) return

      e.preventDefault()
      e.stopPropagation()

      setIsSpacebarPressed(false)

      const pressDuration = spacebarPressTimeRef.current
        ? Date.now() - spacebarPressTimeRef.current
        : 0
      const isTap = pressDuration < 200

      const newWords = [...allWords]
      const currentWord = newWords[syncWordIndex]

      if (isTap) {
        currentWord.end_time = (wordStartTimeRef.current || currentTimeRef.current) + 0.5
      } else {
        currentWord.end_time = currentTimeRef.current
      }

      updateWords(newWords)

      if (syncWordIndex < allWords.length - 1) {
        setSyncWordIndex(syncWordIndex + 1)
      } else {
        setIsManualSyncing(false)
        setSyncWordIndex(-1)
        handleStopAudio()
      }

      wordStartTimeRef.current = null
      spacebarPressTimeRef.current = null
    },
    [isManualSyncing, isPaused, isSpacebarPressed, syncWordIndex, allWords, updateWords, handleStopAudio]
  )

  // Combined spacebar handler
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

  const spacebarHandlerRef = useRef(handleSpacebar)
  spacebarHandlerRef.current = handleSpacebar

  // Set up spacebar handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        e.preventDefault()
        e.stopPropagation()
        spacebarHandlerRef.current(e)
      }
    }

    setModalSpacebarHandler(handler)

    return () => {
      setModalSpacebarHandler(undefined)
    }
  }, [setModalSpacebarHandler])

  // Tap handlers for mobile
  const handleTapStart = useCallback(() => {
    if (!isManualSyncing || isPaused) return
    if (syncWordIndex < 0 || syncWordIndex >= allWords.length) return
    if (isSpacebarPressed) return

    setIsSpacebarPressed(true)
    wordStartTimeRef.current = currentTimeRef.current
    spacebarPressTimeRef.current = Date.now()

    const newWords = [...allWords]
    const currentWord = newWords[syncWordIndex]
    currentWord.start_time = currentTimeRef.current

    if (syncWordIndex > 0) {
      const prevWord = newWords[syncWordIndex - 1]
      if (prevWord.start_time !== null && prevWord.end_time === null) {
        const gap = currentTimeRef.current - prevWord.start_time
        if (gap > 1.0) {
          prevWord.end_time = prevWord.start_time + 0.5
        } else {
          prevWord.end_time = currentTimeRef.current - 0.005
        }
      }
    }

    updateWords(newWords)
  }, [isManualSyncing, isPaused, syncWordIndex, allWords, isSpacebarPressed, updateWords])

  const handleTapEnd = useCallback(() => {
    if (!isManualSyncing || isPaused) return
    if (!isSpacebarPressed) return

    setIsSpacebarPressed(false)

    const pressDuration = spacebarPressTimeRef.current
      ? Date.now() - spacebarPressTimeRef.current
      : 0
    const isTap = pressDuration < 200

    const newWords = [...allWords]
    const currentWord = newWords[syncWordIndex]

    if (isTap) {
      currentWord.end_time = (wordStartTimeRef.current || currentTimeRef.current) + 0.5
    } else {
      currentWord.end_time = currentTimeRef.current
    }

    updateWords(newWords)

    if (syncWordIndex < allWords.length - 1) {
      setSyncWordIndex(syncWordIndex + 1)
    } else {
      setIsManualSyncing(false)
      setSyncWordIndex(-1)
      handleStopAudio()
    }

    wordStartTimeRef.current = null
    spacebarPressTimeRef.current = null
  }, [isManualSyncing, isPaused, isSpacebarPressed, syncWordIndex, allWords, updateWords, handleStopAudio])

  // Handle save
  const handleSave = useCallback(() => {
    onSave(workingSegments)
  }, [workingSegments, onSave])

  // Progress stats
  const stats = useMemo(() => {
    const total = allWords.length
    const synced = allWords.filter((w) => w.start_time !== null && w.end_time !== null).length
    return { total, synced, remaining: total - synced }
  }, [allWords])

  // Get instruction text based on state
  const getInstructionText = useCallback(() => {
    if (isManualSyncing) {
      if (isSpacebarPressed) {
        return {
          primary: t('holdingRelease'),
          secondary: t('releaseSpacebar'),
        }
      }
      if (stats.remaining === 0) {
        return {
          primary: t('allWordsSynced'),
          secondary: t('clickStopSync'),
        }
      }
      return {
        primary: t('pressSpacebar'),
        secondary: t('tapForShort'),
      }
    }
    if (stats.synced === 0) {
      return {
        primary: t('clickStartSync'),
        secondary: t('audioWillPlay'),
      }
    }
    if (stats.remaining > 0) {
      return {
        primary: t('wordsRemaining', { remaining: stats.remaining }),
        secondary: t('clickStartContinue'),
      }
    }
    return {
      primary: t('allWordsSynced'),
      secondary: t('allSyncedApply'),
    }
  }, [isManualSyncing, isSpacebarPressed, stats.synced, stats.remaining])

  const instruction = getInstructionText()

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Stats bar */}
      <div className="flex justify-end items-center h-6">
        <span className="text-sm text-muted-foreground">
          {t('wordsSynced', { synced: stats.synced, total: stats.total })}
          {stats.remaining > 0 && ` ${t('remaining', { remaining: stats.remaining })}`}
        </span>
      </div>

      {/* Instruction banner */}
      <div className="h-14 flex-shrink-0">
        <div
          className={`p-3 h-full rounded flex flex-col justify-center ${
            isManualSyncing ? 'bg-green-600 text-white' : 'bg-muted'
          }`}
        >
          <p className="text-sm font-medium leading-tight">{instruction.primary}</p>
          <p className="text-xs opacity-90 leading-tight">{instruction.secondary}</p>
        </div>
      </div>

      {/* Controls section */}
      <div className="min-h-[88px] flex-shrink-0">
        <SyncControls
          isManualSyncing={isManualSyncing}
          isPaused={isPaused}
          onStartSync={handleStartSync}
          onPauseSync={handlePauseSync}
          onResumeSync={handleResumeSync}
          onClearSync={handleClearSync}
          onEditLyrics={handleEditLyrics}
          onPlay={handlePlayAudio}
          onStop={handleStopAudio}
          isPlaying={isPlaying}
          hasSelectedWords={selectedWordIds.size > 0}
          selectedWordCount={selectedWordIds.size}
          onUnsyncFromCursor={handleUnsyncFromCursor}
          onEditSelectedWord={handleEditSelectedWord}
          onDeleteSelected={handleDeleteSelected}
          canUnsyncFromCursor={canUnsyncFromCursor}
          isMobile={isMobile}
          onTapStart={handleTapStart}
          onTapEnd={handleTapEnd}
          isTapping={isSpacebarPressed}
        />
      </div>

      {/* Upcoming words bar */}
      <div className="h-11 flex-shrink-0">
        {isManualSyncing && (
          <UpcomingWordsBar
            words={allWords}
            syncWordIndex={syncWordIndex}
            isManualSyncing={isManualSyncing}
          />
        )}
      </div>

      {/* Timeline canvas */}
      <div className="flex-1 min-h-[200px]">
        <TimelineCanvas
          words={allWords}
          segments={workingSegments}
          visibleStartTime={visibleStartTime}
          visibleEndTime={visibleEndTime}
          currentTime={currentTime}
          selectedWordIds={selectedWordIds}
          onWordClick={handleWordClick}
          onBackgroundClick={handleBackgroundClick}
          onTimeBarClick={handleTimeBarClick}
          onSelectionComplete={handleSelectionComplete}
          onWordTimingChange={handleWordTimingChange}
          onWordsMove={handleWordsMove}
          syncWordIndex={syncWordIndex}
          isManualSyncing={isManualSyncing}
          onScrollChange={handleScrollChange}
          audioDuration={audioDuration}
          zoomSeconds={zoomSeconds}
          height={200}
          isDarkMode={isDarkMode}
        />
      </div>

      {/* Zoom slider */}
      <div className="flex items-center gap-4 px-2">
        <ZoomIn className="h-4 w-4 text-muted-foreground" />
        <Slider
          value={[sliderValue]}
          onValueChange={handleZoomChange}
          min={0}
          max={ZOOM_STEPS}
          step={1}
          className="flex-1"
          disabled={isManualSyncing && !isPaused}
        />
        <ZoomOut className="h-4 w-4 text-muted-foreground" />
        <span className="text-xs text-muted-foreground min-w-[60px]">
          {zoomSeconds.toFixed(1)}s view
        </span>
      </div>

      {/* Action buttons */}
      <div className="flex justify-end gap-4 pt-4 border-t">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={handleSave} disabled={isManualSyncing && !isPaused}>
          Apply
        </Button>
      </div>

      {/* Edit Lyrics Modal */}
      <Dialog open={showEditLyricsModal} onOpenChange={setShowEditLyricsModal}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('controls.editLyrics')}</DialogTitle>
          </DialogHeader>

          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              {t('editingResetsWarning')}
            </AlertDescription>
          </Alert>

          <Textarea
            value={editLyricsText}
            onChange={(e) => setEditLyricsText(e.target.value)}
            placeholder={t('lyricsPlaceholder')}
            rows={15}
            className="font-mono"
          />

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditLyricsModal(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleSaveEditedLyrics}>
              {t('saveAndResetTiming')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Word Modal */}
      <Dialog open={showEditWordModal} onOpenChange={setShowEditWordModal}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('editWord')}</DialogTitle>
          </DialogHeader>

          <p className="text-sm text-muted-foreground">
            {t('editWordDesc')}
          </p>

          <Input
            value={editWordText}
            onChange={(e) => setEditWordText(e.target.value)}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleSaveEditedWord()
              }
            }}
          />

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditWordModal(false)}>
              Cancel
            </Button>
            <Button onClick={handleSaveEditedWord}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
})

export default LyricsSynchronizer
