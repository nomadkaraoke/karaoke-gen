// Global state for modal keyboard handling
let currentModalHandler: ((e: KeyboardEvent) => void) | undefined
let isModalOpen = false
const debugLog = false

// Extend Window interface for global audio toggle
declare global {
  interface Window {
    toggleAudioPlayback?: () => void
  }
}

type KeyboardState = {
  setIsShiftPressed: (value: boolean) => void
  setIsCtrlPressed?: (value: boolean) => void
  modalHandler?: {
    isOpen: boolean
    onSpacebar?: (e: KeyboardEvent) => void
  }
}

// Update the modal handler state
export const setModalHandler = (
  handler: ((e: KeyboardEvent) => void) | undefined,
  open: boolean
) => {
  if (debugLog) {
    console.log('setModalHandler called', {
      hasHandler: !!handler,
      open,
      previousState: {
        hadHandler: !!currentModalHandler,
        wasOpen: isModalOpen,
      },
    })
  }

  currentModalHandler = handler
  isModalOpen = open
}

export const setupKeyboardHandlers = (state: KeyboardState) => {
  const handlerId = Math.random().toString(36).substr(2, 9)
  if (debugLog) {
    console.log(`Setting up keyboard handlers [${handlerId}]`)
  }

  // Function to reset modifier key states
  const resetModifierStates = () => {
    if (debugLog) {
      console.log(`Resetting modifier states [${handlerId}]`)
    }
    state.setIsShiftPressed(false)
    state.setIsCtrlPressed?.(false)
    document.body.style.userSelect = ''
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (debugLog) {
      console.log(`Keyboard event captured [${handlerId}]`, {
        key: e.key,
        code: e.code,
        type: e.type,
        target: e.target,
        currentTarget: e.currentTarget,
        eventPhase: e.eventPhase,
        isModalOpen,
        hasModalHandler: !!currentModalHandler,
      })
    }

    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      if (debugLog) {
        console.log(`[${handlerId}] Ignoring keydown in input/textarea`)
      }
      return
    }

    if (e.key === 'Shift') {
      state.setIsShiftPressed(true)
      document.body.style.userSelect = 'none'
    } else if (e.key === 'Control' || e.key === 'Ctrl' || e.key === 'Meta') {
      state.setIsCtrlPressed?.(true)
    } else if (e.key === ' ' || e.code === 'Space') {
      if (debugLog) {
        console.log('Keyboard handler - Spacebar pressed down', {
          modalOpen: isModalOpen,
          hasModalHandler: !!currentModalHandler,
          hasGlobalToggle: !!window.toggleAudioPlayback,
          target: e.target,
          eventPhase: e.eventPhase,
          handlerFunction: currentModalHandler?.toString().slice(0, 100),
        })
      }

      e.preventDefault()

      if (isModalOpen && currentModalHandler) {
        currentModalHandler(e)
      } else if (window.toggleAudioPlayback && !isModalOpen) {
        if (debugLog) {
          console.log('Keyboard handler - Using global audio toggle')
        }
        window.toggleAudioPlayback()
      }
    }
  }

  const handleKeyUp = (e: KeyboardEvent) => {
    if (debugLog) {
      console.log(`Keyboard up event captured [${handlerId}]`, {
        key: e.key,
        code: e.code,
        type: e.type,
        target: e.target,
        eventPhase: e.eventPhase,
        isModalOpen,
        hasModalHandler: !!currentModalHandler,
      })
    }

    // Ignore keyup events in input and textarea elements
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      if (debugLog) {
        console.log(`[${handlerId}] Ignoring keyup in input/textarea`)
      }
      return
    }

    // Always reset the modifier states regardless of the key which was released
    // to help prevent accidentally getting stuck in a mode or accidentally deleting words
    resetModifierStates()

    if (e.key === ' ' || e.code === 'Space') {
      if (debugLog) {
        console.log('Keyboard handler - Spacebar released', {
          modalOpen: isModalOpen,
          hasModalHandler: !!currentModalHandler,
          target: e.target,
          eventPhase: e.eventPhase,
        })
      }

      e.preventDefault()

      if (isModalOpen && currentModalHandler) {
        currentModalHandler(e)
      }
    }
  }

  // Handle window blur event (user switches tabs or apps)
  const handleWindowBlur = () => {
    if (debugLog) {
      console.log(`Window blur detected [${handlerId}], resetting modifier states`)
    }
    resetModifierStates()
  }

  // Handle window focus event (user returns to the app)
  const handleWindowFocus = () => {
    if (debugLog) {
      console.log(`Window focus detected [${handlerId}], ensuring modifier states are reset`)
    }
    resetModifierStates()
  }

  // Add window event listeners
  window.addEventListener('blur', handleWindowBlur)
  window.addEventListener('focus', handleWindowFocus)

  // Return a cleanup function that includes removing the window event listeners
  return {
    handleKeyDown,
    handleKeyUp,
    cleanup: () => {
      window.removeEventListener('blur', handleWindowBlur)
      window.removeEventListener('focus', handleWindowFocus)
    },
  }
}

// Export these for external use
export const getModalState = () => ({
  currentModalHandler,
  isModalOpen,
})
