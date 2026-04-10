// Color constants for lyrics review - using Tailwind CSS variable references
// These match the theme colors defined in globals.css

export const COLORS = {
  // Background highlight colors (semi-transparent for dark mode)
  anchor: 'rgba(59, 130, 246, 0.25)', // Blue tint
  corrected: 'rgba(34, 197, 94, 0.25)', // Green tint
  uncorrectedGap: 'rgba(249, 115, 22, 0.25)', // Orange tint
  highlighted: 'rgba(251, 191, 36, 0.4)', // Amber highlight

  // Accent colors
  playing: '#3b82f6', // Blue-500

  // Text colors (matching Tailwind CSS variables)
  textPrimary: 'hsl(var(--foreground))',
  textSecondary: 'hsl(var(--muted-foreground))',
  textMuted: 'hsl(var(--muted-foreground))',

  // Background colors (matching Tailwind CSS variables)
  background: 'hsl(var(--background))',
  backgroundPaper: 'hsl(var(--card))',
  backgroundElevated: 'hsl(var(--secondary))',
  border: 'hsl(var(--border))',
} as const

// Tailwind class mappings for highlight types
export const HIGHLIGHT_CLASSES = {
  anchor: 'bg-blue-500/25',
  corrected: 'bg-green-500/25',
  uncorrectedGap: 'bg-orange-500/25',
  highlighted: 'bg-amber-400/40',
  playing: 'text-blue-500',
  userEdited: 'bg-violet-500/25',
} as const

// CSS keyframes for flash animation (use with Tailwind animate utilities)
// Add this to globals.css:
// @keyframes lyrics-flash {
//   0%, 100% { opacity: 1; }
//   50% { opacity: 0.6; background-color: rgba(251, 191, 36, 0.4); }
// }
export const FLASH_ANIMATION_CLASS = 'animate-lyrics-flash'

// Animation duration in milliseconds
export const FLASH_DURATION_MS = 500
