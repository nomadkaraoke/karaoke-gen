'use client'

import { useState, memo, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Trash2, Split, Wand2 } from 'lucide-react'
import { Word } from '@/lib/lyrics-review/types'
import WordDivider from './WordDivider'

interface EditWordListProps {
  words: Word[]
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onSplitWord: (index: number) => void
  onMergeWords: (index: number) => void
  onAddWord: (index?: number) => void
  onRemoveWord: (index: number) => void
  onReplaceAllWords?: (replacementText: string) => void
  onSplitSegment?: (wordIndex: number) => void
  onAddSegment?: (beforeIndex: number) => void
  onMergeSegment?: (mergeWithNext: boolean) => void
  isGlobal?: boolean
}

// Memoized word row component
const WordRow = memo(function WordRow({
  word,
  index,
  onWordUpdate,
  onSplitWord,
  onRemoveWord,
  wordsLength,
  onTabNavigation,
  isMobile,
}: {
  word: Word
  index: number
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onSplitWord: (index: number) => void
  onRemoveWord: (index: number) => void
  wordsLength: number
  onTabNavigation: (currentIndex: number) => void
  isMobile: boolean
}) {
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault()
      onTabNavigation(index)
    }
  }

  return (
    <div
      className={`flex ${isMobile ? 'flex-col gap-2' : 'flex-row gap-4'} items-${isMobile ? 'stretch' : 'end'} py-1`}
    >
      {/* Word text field */}
      <div className={`flex gap-2 items-end ${isMobile ? '' : 'flex-1'}`}>
        <div className="flex-1">
          <Label htmlFor={`word-text-${index}`} className="text-xs text-muted-foreground mb-1">
            Word {index}
          </Label>
          <Input
            id={`word-text-${index}`}
            value={word.text}
            onChange={(e) => onWordUpdate(index, { text: e.target.value })}
            onKeyDown={handleKeyDown}
            className="h-8"
          />
        </div>
        {/* Action buttons inline with word on mobile */}
        {isMobile && (
          <>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onSplitWord(index)}
              title="Split Word"
              className="h-8 w-8 text-primary"
            >
              <Split className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onRemoveWord(index)}
              disabled={wordsLength <= 1}
              title="Remove Word"
              className="h-8 w-8 text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </>
        )}
      </div>

      {/* Time fields */}
      <div
        className={`flex gap-2 items-end ${isMobile ? 'justify-start' : 'justify-end'}`}
      >
        <div className={`${isMobile ? 'w-20' : 'w-24'}`}>
          <Label className="text-xs text-muted-foreground mb-1">Start</Label>
          <Input
            type="number"
            value={word.start_time?.toFixed(2) ?? ''}
            onChange={(e) => onWordUpdate(index, { start_time: parseFloat(e.target.value) })}
            step={0.01}
            className="h-8"
          />
        </div>
        <div className={`${isMobile ? 'w-20' : 'w-24'}`}>
          <Label className="text-xs text-muted-foreground mb-1">End</Label>
          <Input
            type="number"
            value={word.end_time?.toFixed(2) ?? ''}
            onChange={(e) => onWordUpdate(index, { end_time: parseFloat(e.target.value) })}
            step={0.01}
            className="h-8"
          />
        </div>
        {/* Action buttons on desktop only */}
        {!isMobile && (
          <>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onSplitWord(index)}
              title="Split Word"
              className="h-8 w-8 text-primary"
            >
              <Split className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onRemoveWord(index)}
              disabled={wordsLength <= 1}
              title="Remove Word"
              className="h-8 w-8 text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </>
        )}
      </div>
    </div>
  )
})

// Memoized word item component
const WordItem = memo(function WordItem({
  word,
  index,
  onWordUpdate,
  onSplitWord,
  onRemoveWord,
  onAddWord,
  onMergeWords,
  onSplitSegment,
  onAddSegment,
  onMergeSegment,
  wordsLength,
  isGlobal,
  onTabNavigation,
  isMobile,
}: {
  word: Word
  index: number
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onSplitWord: (index: number) => void
  onRemoveWord: (index: number) => void
  onAddWord: (index: number) => void
  onMergeWords: (index: number) => void
  onSplitSegment?: (index: number) => void
  onAddSegment?: (index: number) => void
  onMergeSegment?: (mergeWithNext: boolean) => void
  wordsLength: number
  isGlobal: boolean
  onTabNavigation: (currentIndex: number) => void
  isMobile: boolean
}) {
  return (
    <div key={word.id}>
      <WordRow
        word={word}
        index={index}
        onWordUpdate={onWordUpdate}
        onSplitWord={onSplitWord}
        onRemoveWord={onRemoveWord}
        wordsLength={wordsLength}
        onTabNavigation={onTabNavigation}
        isMobile={isMobile}
      />

      {/* Word divider */}
      {!isGlobal && (
        <WordDivider
          onAddWord={() => onAddWord(index)}
          onMergeWords={() => onMergeWords(index)}
          onSplitSegment={() => onSplitSegment?.(index)}
          onAddSegmentAfter={
            index === wordsLength - 1 ? () => onAddSegment?.(index + 1) : undefined
          }
          onMergeSegment={index === wordsLength - 1 ? () => onMergeSegment?.(true) : undefined}
          canMerge={index < wordsLength - 1}
          isLast={index === wordsLength - 1}
        />
      )}
      {isGlobal && (
        <WordDivider
          onAddWord={() => onAddWord(index)}
          onMergeWords={index < wordsLength - 1 ? () => onMergeWords(index) : undefined}
          canMerge={index < wordsLength - 1}
        />
      )}
    </div>
  )
})

export default function EditWordList({
  words,
  onWordUpdate,
  onSplitWord,
  onMergeWords,
  onAddWord,
  onRemoveWord,
  onReplaceAllWords,
  onSplitSegment,
  onAddSegment,
  onMergeSegment,
  isGlobal = false,
}: EditWordListProps) {
  // Simple mobile detection
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  const [replacementText, setReplacementText] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = isGlobal ? 50 : words.length

  const handleReplaceAllWords = () => {
    if (onReplaceAllWords) {
      // Use the callback that handles proper timing
      onReplaceAllWords(replacementText)
    } else {
      // Fallback: just update text of existing words (legacy behavior)
      const newWords = replacementText.trim().split(/\s+/)
      newWords.forEach((text, index) => {
        if (index < words.length) {
          onWordUpdate(index, { text })
        }
      })
    }
    setReplacementText('')
  }

  // Pagination
  const pageCount = Math.ceil(words.length / pageSize)
  const startIndex = (page - 1) * pageSize
  const endIndex = Math.min(startIndex + pageSize, words.length)

  const visibleWords = useMemo(() => {
    return isGlobal ? words.slice(startIndex, endIndex) : words
  }, [words, isGlobal, startIndex, endIndex])

  // Tab navigation between word fields
  const handleTabNavigation = (currentIndex: number) => {
    const nextIndex = (currentIndex + 1) % words.length

    if (isGlobal && (nextIndex < startIndex || nextIndex >= endIndex)) {
      const nextPage = Math.floor(nextIndex / pageSize) + 1
      setPage(nextPage)

      setTimeout(() => {
        focusWordTextField(nextIndex)
      }, 50)
    } else {
      focusWordTextField(nextIndex)
    }
  }

  const focusWordTextField = (index: number) => {
    const element = document.getElementById(`word-text-${index}`)
    if (element) {
      const input = element as HTMLInputElement
      input.focus()
      input.select()
    }
  }

  return (
    <div className="flex flex-col gap-2 flex-grow min-h-0">
      {/* Initial divider */}
      {!isGlobal && (
        <WordDivider
          onAddWord={() => onAddWord(-1)}
          onAddSegmentBefore={() => onAddSegment?.(0)}
          onMergeSegment={() => onMergeSegment?.(false)}
          isFirst={true}
        />
      )}
      {isGlobal && <WordDivider onAddWord={() => onAddWord(-1)} />}

      {/* Word list with scrolling */}
      <div className="flex flex-col gap-1 flex-grow overflow-y-auto mb-0 pt-2 scrollbar-thin">
        {visibleWords.map((word, visibleIndex) => {
          const actualIndex = isGlobal ? startIndex + visibleIndex : visibleIndex
          return (
            <WordItem
              key={word.id}
              word={word}
              index={actualIndex}
              onWordUpdate={onWordUpdate}
              onSplitWord={onSplitWord}
              onRemoveWord={onRemoveWord}
              onAddWord={onAddWord}
              onMergeWords={onMergeWords}
              onSplitSegment={onSplitSegment}
              onAddSegment={onAddSegment}
              onMergeSegment={onMergeSegment}
              wordsLength={words.length}
              isGlobal={isGlobal}
              onTabNavigation={handleTabNavigation}
              isMobile={isMobile}
            />
          )
        })}
      </div>

      {/* Pagination controls */}
      {isGlobal && pageCount > 1 && (
        <div className="flex justify-center items-center gap-2 mb-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
          >
            Previous
          </Button>
          <span className="text-sm">
            Page {page} of {pageCount}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            disabled={page === pageCount}
          >
            Next
          </Button>
          <span className="text-sm text-muted-foreground ml-2">
            Words {startIndex + 1}-{endIndex} of {words.length}
          </span>
        </div>
      )}

      {/* Replace all words */}
      <div className="flex gap-2 mb-1">
        <Input
          value={replacementText}
          onChange={(e) => setReplacementText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && replacementText.trim()) {
              e.preventDefault()
              e.stopPropagation()
              handleReplaceAllWords()
            }
          }}
          placeholder="Replace all words"
          className="flex-grow"
        />
        <Button onClick={handleReplaceAllWords} size="sm" className="whitespace-nowrap">
          <Wand2 className="h-4 w-4 mr-1" />
          Replace All
        </Button>
      </div>
    </div>
  )
}
