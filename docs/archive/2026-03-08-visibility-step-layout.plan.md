# Plan: Visibility Step Layout Rework

**Date**: 2026-03-08
**Branch**: `feat/visibility-step-layout`
**Status**: Implemented

## Problem

The Visibility step requires scrolling past two detailed info cards to reach the Continue button. Users who already know what they want (especially repeat users) are forced to scroll every time.

Screenshot shows: header → Published card (large) → Private card (large) → Continue button (below fold).

## Design Decision

**Chosen approach: Action buttons at top, detail cards below as reference.**

Two prominent, descriptive buttons placed immediately after the header let users select-and-continue in one click. The existing detail cards remain below for first-time users who want to understand the implications before choosing.

### Why this over alternatives

| Approach | Pros | Cons |
|----------|------|------|
| **Action buttons at top** (chosen) | Zero scroll for decisive users; details still visible for cautious users; single click to proceed | Two interaction patterns (buttons vs cards) |
| Move Continue to top | Simple change | Still requires scrolling to understand options before clicking |
| Collapsible detail cards | Compact | Hides important info by default; extra click to expand |
| Sticky bottom button | Easy to implement | Overlaps content on small screens; still generic "Continue" |
| Auto-advance on card select | Fewest clicks | No chance to change mind; jarring UX |

## Design Spec

### New Layout (top to bottom)

```
Header: "How should your video be shared?"
Subtitle + Back button (unchanged)

┌─────────────────────────────────────────────────┐
│  [🌐 Publish & Share →]  [🔒 Keep Private →]   │
│      Recommended                                 │
└─────────────────────────────────────────────────┘

── What's the difference? ──────────────── (divider)

[Published detail card]   (informational, still clickable to select)
[Private detail card]     (informational, still clickable to select)
```

### Action Buttons Detail

- **Two buttons side-by-side** in a flex row with `gap-3`
- Each button is a bordered card-style button (~equal width via `flex-1`)
- **"Publish & Share"** button: Globe icon, "Recommended" badge below, pink border/highlight when Published is currently selected
- **"Keep Private"** button: Lock icon, neutral border, pink highlight when Private is currently selected
- **Clicking either button calls both `onPrivateChange()` AND `onNext()`** — single action to select and continue
- Arrow icon (→) on each button reinforces that clicking advances the flow
- Desktop: side by side. Mobile (`< sm`): stack vertically

### Detail Cards

- Keep the existing Published and Private cards **exactly as-is** content-wise
- They remain clickable (toggling the selection) but do **not** auto-advance
- Add a subtle text divider above them: "What's the difference?" in muted text with horizontal rules
- When a user clicks a detail card, it updates the selection and the action buttons above visually reflect the change — but the user still needs to click an action button (or scroll back up) to continue. This is intentional: clicking a card means "I'm still reading/deciding."
- Consider slightly reducing vertical padding on cards (`p-4` → `p-3`) to reduce total page height

### Visual Treatment

- Action buttons use the same `var(--brand-pink)` theming as the rest of the step
- Selected action button: pink border + subtle glow (matching current card selection style)
- Unselected action button: `var(--card-border)` border, muted icon
- Both buttons have hover states with border color transition
- The "Recommended" label sits under the Publish button (not inside it) as a small badge

### Responsive Behavior

- **Desktop (≥640px / `sm`)**: Action buttons side-by-side
- **Mobile (<640px)**: Action buttons stack vertically, Publish on top

## Implementation

### Files to Change

1. **`frontend/components/job/steps/VisibilityStep.tsx`** — the only file that needs changes

### Steps

1. **Add action buttons section** after the header div and before the detail cards
   - Two `<button>` elements in a flex container
   - Each calls `onPrivateChange(value)` then `onNext()` on click
   - Styled as card-like buttons with icon + label + optional badge
   - Use existing CSS variable theming

2. **Add divider** between action buttons and detail cards
   - Simple flex row with horizontal rules and muted text: "What's the difference?"

3. **Keep existing detail cards** unchanged (content, styling, click handlers)
   - Only change: remove the bottom Continue button entirely

4. **Remove the bottom Continue button** — no longer needed since action buttons serve this purpose

### Code Sketch

```tsx
{/* Action buttons - select and continue */}
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
  >
    <Globe />
    <span>Publish & Share</span>
    <ArrowRight />
    {/* Recommended badge below */}
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
  >
    <Lock />
    <span>Keep Private</span>
    <ArrowRight />
  </button>
</div>

{/* Divider */}
<div className="flex items-center gap-3">
  <div className="flex-1 h-px bg-[var(--card-border)]" />
  <span className="text-xs text-[var(--text-muted)]">What's the difference?</span>
  <div className="flex-1 h-px bg-[var(--card-border)]" />
</div>

{/* Existing detail cards (unchanged) */}
```

## Testing

- Visual: verify no-scroll path works on common viewport sizes (desktop, tablet, mobile)
- Functional: clicking action buttons correctly sets visibility AND advances to step 4
- Functional: clicking detail cards still toggles selection without advancing
- Functional: Back button still works
- Functional: disabled state prevents all interactions
- Accessibility: buttons are keyboard navigable, focus states visible

## Estimate

Small change — single component, ~40 lines added, ~6 lines removed. Should take one implementation session.
