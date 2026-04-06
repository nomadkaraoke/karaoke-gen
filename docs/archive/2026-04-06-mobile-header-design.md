# Mobile-Friendly Header Design

**Date:** 2026-04-06
**Status:** Approved
**File:** `frontend/components/app-header.tsx`

## Problem

The app header crams 6-7 icon-only buttons into a single row on mobile. The referrals icon in particular isn't recognizable, and the row is too crowded for the available width.

## Solution

Add a second row on mobile (`< sm` breakpoint) to give action buttons room for text labels. Desktop layout is unchanged.

## Layout

### Mobile (`< 640px`)

```
┌──────────────────────────────────────┐
│ [Logo] Title              [👤 Login] │
├──────────────────────────────────────┤
│ [↻ Refresh] [👥 Referrals] [? Help] │
│ [🇬🇧 Lang] [☀ Theme]               │
└──────────────────────────────────────┘
```

- **Row 1:** Logo + title (left), AuthStatus (right)
- **Row 2:** Action buttons with text labels, wrapped with `flex-wrap`
- Separated by a subtle border or spacing

### Desktop (`≥ 640px`) — unchanged

Single row: Logo, Title, [children], Help, Referrals, Language, Auth, Theme.

## Implementation

### Changes to `app-header.tsx`

1. **Restructure the outer container** to stack two rows on mobile:
   - Outer: `flex flex-col sm:flex-row` (or keep current layout and add a second div that's `sm:hidden`)
   - Row 1 (mobile): logo/title + AuthStatus
   - Row 2 (mobile): all other action buttons with text labels visible

2. **Move AuthStatus** into row 1 on mobile. On desktop it stays in the existing position.

3. **Show text labels on mobile** for row 2 items:
   - Refresh: show `t('refresh')` text
   - Referrals: show `t('referrals')` text
   - Help: show `tHeader('help')` or similar text label
   - Language: show native language name (remove `hidden sm:inline` from LanguageSwitcher)
   - Theme: stays icon-only (sun/moon is universally understood)

4. **`children` prop** (page-specific buttons like Refresh, Admin toggle): render in row 2 on mobile, current position on desktop.

### Files Changed

- `frontend/components/app-header.tsx` — layout restructure
- `frontend/components/LanguageSwitcher.tsx` — remove `hidden sm:inline` from the native language name span so it's always visible
- `frontend/components/auth/AuthStatus.tsx` — may need minor adjustments for row 1 placement on mobile
- `frontend/messages/en.json` — add `header.help` key if not already present

### No i18n Translation Run Needed

All text labels (`refresh`, `referrals`, login/auth text) already exist as translation keys — they're currently hidden via CSS `hidden sm:inline`. We're just making them visible on mobile. The only potential new key is a "Help" label for the help button.

## Approach

Use Tailwind responsive classes (`sm:hidden` / `hidden sm:flex`) to render two different layouts from the same component, rather than duplicating the buttons. Specifically:

- On mobile: render row 1 (brand + auth) and row 2 (actions with text)
- On desktop: render the current single-row layout
- Shared button components, just different container layout

## Spacing

- Row 2 gets the same horizontal padding as row 1 (`px-3 sm:px-4`)
- Slightly reduced vertical padding on mobile to keep total header height reasonable
- `pt-[header-height]` on main content needs updating to account for taller mobile header

## Testing

- Visual check on mobile viewport (375px, 390px widths)
- Verify desktop layout is unchanged
- Check with children (dashboard page) and without (referrals page)
- Check logged-in vs logged-out states
- Verify language switcher dropdown still positions correctly from row 2
