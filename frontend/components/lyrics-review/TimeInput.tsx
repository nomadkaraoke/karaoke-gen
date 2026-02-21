'use client'

import { useState, useRef, useEffect, useCallback, useId, memo } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface TimeInputProps {
  value: number | null
  onChange: (value: number | null) => void
  label: string
  widthClass?: string
  debounceMs?: number
}

const TimeInput = memo(function TimeInput({
  value,
  onChange,
  label,
  widthClass = 'w-24',
  debounceMs = 300
}: TimeInputProps) {
  const inputId = useId()
  const [localValue, setLocalValue] = useState<string>('')
  const [isFocused, setIsFocused] = useState(false)
  const debounceRef = useRef<NodeJS.Timeout | null>(null)

  // Sync local state when parent value changes (while not focused)
  useEffect(() => {
    if (!isFocused) {
      setLocalValue(value?.toFixed(2) ?? '')
    }
  }, [value, isFocused])

  const debouncedUpdate = useCallback((strValue: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const parsed = parseFloat(strValue)
      onChange(isNaN(parsed) ? null : parsed)
    }, debounceMs)
  }, [onChange, debounceMs])

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const handleFocus = (e: React.FocusEvent<HTMLInputElement>) => {
    setLocalValue(value?.toFixed(2) ?? '')
    setIsFocused(true)
    e.target.select()
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalValue(e.target.value)
    debouncedUpdate(e.target.value)
  }

  const handleBlur = () => {
    setIsFocused(false)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const parsed = parseFloat(localValue)
    onChange(isNaN(parsed) ? null : parsed)
  }

  return (
    <div className={widthClass}>
      <Label htmlFor={inputId} className="text-xs text-muted-foreground mb-1">{label}</Label>
      <Input
        id={inputId}
        type="number"
        value={isFocused ? localValue : (value?.toFixed(2) ?? '')}
        onFocus={handleFocus}
        onChange={handleChange}
        onBlur={handleBlur}
        step={0.01}
        className="h-8"
      />
    </div>
  )
})

export default TimeInput
