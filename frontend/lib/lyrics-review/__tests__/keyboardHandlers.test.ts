import { setupKeyboardHandlers } from '../utils/keyboardHandlers'

describe('keyboard shortcuts — duet', () => {
  it('pressing 1 calls onAssignSegmentSinger with 1', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: 3,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', bubbles: true }))
    expect(onAssign).toHaveBeenCalledWith(3, 1)
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('pressing 2 calls onAssignSegmentSinger with 2', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: 5,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '2', bubbles: true }))
    expect(onAssign).toHaveBeenCalledWith(5, 2)
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('pressing b calls onAssignSegmentSinger with 0 (Both)', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: 0,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', bubbles: true }))
    expect(onAssign).toHaveBeenCalledWith(0, 0)
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('pressing B (uppercase) calls onAssignSegmentSinger with 0 (Both)', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: 2,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'B', bubbles: true }))
    expect(onAssign).toHaveBeenCalledWith(2, 0)
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('keys are ignored when isDuet is false', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: false,
      focusedSegmentIndex: 0,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', bubbles: true }))
    expect(onAssign).not.toHaveBeenCalled()
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('keys are ignored when focusedSegmentIndex is null', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: null,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '1', bubbles: true }))
    expect(onAssign).not.toHaveBeenCalled()
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })

  it('keys are ignored when target is an input element', () => {
    const onAssign = jest.fn()
    const { handleKeyDown, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed: jest.fn(),
      isDuet: true,
      focusedSegmentIndex: 1,
      onAssignSegmentSinger: onAssign,
    })
    window.addEventListener('keydown', handleKeyDown)
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: '1', bubbles: true }))
    expect(onAssign).not.toHaveBeenCalled()
    document.body.removeChild(input)
    window.removeEventListener('keydown', handleKeyDown)
    cleanup()
  })
})
