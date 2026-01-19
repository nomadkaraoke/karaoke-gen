# Task: Migrate Instrumental Review to Main Frontend

**Task ID**: TASK-002
**Status**: Ready for execution
**Estimated scope**: ~1,700 lines (single HTML file → 5-6 React components)
**Approach**: Convert vanilla HTML/JS to React + Tailwind

## Context

The Instrumental Review UI is currently a single HTML file with embedded JavaScript, served by the backend. This task converts it to React components in the main frontend.

### Current State
- **Source**: `karaoke_gen/instrumental_review/static/index.html` (~1,700 lines)
- **Target**: `frontend/components/instrumental-review/`
- **Placeholder exists at**: `frontend/app/app/jobs/[[...slug]]/client.tsx` (search for `InstrumentalSelectionPlaceholder`)

### Goal
Replace `InstrumentalSelectionPlaceholder` with actual `InstrumentalSelector` component.

## Source File Analysis

The source HTML file contains:

### Embedded CSS (~300 lines)
- CSS variables for theming (already matches main frontend)
- Waveform visualization styles
- Audio player controls
- Mute region editor styles

### Embedded JavaScript (~1,200 lines)
Key functionality:
1. **URL parameter parsing** - Gets `baseApiUrl` and `instrumentalToken`
2. **API calls** - Fetches analysis, waveform data, submits selection
3. **Audio playback** - Multiple audio elements for A/B comparison
4. **Waveform rendering** - Canvas-based visualization
5. **Mute region editing** - Interactive drag-to-select regions
6. **Custom instrumental upload** - File upload with validation

### UI Sections
1. **Header** - Song title, back button
2. **Stem comparison** - Play/pause buttons for each instrumental option
3. **Waveform viewer** - Visual representation of audio
4. **Selection options** - Radio buttons for clean/with_backing/custom
5. **Mute region editor** - Interactive timeline for custom muting
6. **Upload section** - Drag-and-drop file upload
7. **Submit button** - Confirm selection

## Target Structure

```
frontend/components/instrumental-review/
├── InstrumentalSelector.tsx     # Main container
├── StemComparison.tsx           # A/B comparison audio players
├── WaveformViewer.tsx           # Canvas waveform visualization
├── SelectionOptions.tsx         # Radio group for selection
├── MuteRegionEditor.tsx         # Interactive mute region UI
└── CustomUpload.tsx             # File upload component
```

## API Integration

### Current Endpoints Used

```javascript
// From index.html
GET  {baseApiUrl}/instrumental-analysis    → Get available stems and analysis
GET  {baseApiUrl}/waveform-data?num_points=1000  → Get waveform points
POST {baseApiUrl}/select-instrumental      → Submit selection choice
POST {baseApiUrl}/upload-instrumental      → Upload custom file
POST {baseApiUrl}/create-custom-instrumental  → Create with mute regions
GET  {baseApiUrl}/audio-stream/{stem_type} → Stream audio for playback
```

### Add to `frontend/lib/api.ts`

```typescript
// Instrumental Review endpoints
async getInstrumentalAnalysis(jobId: string): Promise<InstrumentalAnalysis> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/instrumental-analysis`, {
    headers: getAuthHeaders()
  });
  return handleResponse(response);
},

async getWaveformData(jobId: string, numPoints: number = 1000): Promise<WaveformData> {
  const response = await fetch(
    `${API_BASE_URL}/api/jobs/${jobId}/waveform-data?num_points=${numPoints}`,
    { headers: getAuthHeaders() }
  );
  return handleResponse(response);
},

async selectInstrumentalOption(
  jobId: string,
  selection: 'clean' | 'with_backing' | 'custom',
  muteRegions?: MuteRegion[]
): Promise<void> {
  const body: Record<string, unknown> = { selection };
  if (muteRegions) body.mute_regions = muteRegions;

  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/select-instrumental`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse(response);
},

async uploadCustomInstrumental(jobId: string, file: File): Promise<void> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/upload-instrumental`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: formData,
  });
  return handleResponse(response);
},

async createCustomInstrumental(jobId: string, muteRegions: MuteRegion[]): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/create-custom-instrumental`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ mute_regions: muteRegions }),
  });
  return handleResponse(response);
},

getAudioStreamUrl(jobId: string, stemType: string): string {
  const token = getAccessToken();
  const base = `${API_BASE_URL}/api/jobs/${jobId}/audio-stream/${stemType}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
},
```

### Types to Add

```typescript
// In frontend/lib/types.ts or frontend/lib/api.ts

interface InstrumentalAnalysis {
  job_id: string;
  artist: string;
  title: string;
  duration_seconds: number;
  available_stems: {
    clean: boolean;
    with_backing: boolean;
  };
  backing_vocal_analysis?: {
    detected: boolean;
    confidence: number;
    regions: Array<{ start: number; end: number; confidence: number }>;
  };
}

interface WaveformData {
  peaks: number[];  // Normalized 0-1
  duration_seconds: number;
  sample_rate: number;
}

interface MuteRegion {
  start: number;  // Seconds
  end: number;    // Seconds
}
```

## Component Specifications

### 1. InstrumentalSelector.tsx (Main Container)

```tsx
interface InstrumentalSelectorProps {
  jobId: string;
  artist: string;
  title: string;
  onComplete: () => void;  // Called after successful selection
}
```

Responsibilities:
- Fetch analysis and waveform data on mount
- Manage selection state
- Coordinate between child components
- Handle submission

### 2. StemComparison.tsx

```tsx
interface StemComparisonProps {
  jobId: string;
  availableStems: { clean: boolean; with_backing: boolean };
  currentlyPlaying: string | null;
  onPlay: (stem: string) => void;
  onPause: () => void;
}
```

Features:
- Two audio players (clean, with_backing)
- Play/pause buttons
- A/B toggle
- Volume control (optional)

### 3. WaveformViewer.tsx

```tsx
interface WaveformViewerProps {
  peaks: number[];
  duration: number;
  currentTime: number;
  muteRegions?: MuteRegion[];
  onSeek?: (time: number) => void;
}
```

Features:
- Canvas-based waveform rendering
- Playback position indicator
- Mute regions overlay (if provided)
- Click to seek

### 4. SelectionOptions.tsx

```tsx
interface SelectionOptionsProps {
  value: 'clean' | 'with_backing' | 'custom';
  onChange: (value: 'clean' | 'with_backing' | 'custom') => void;
  availableStems: { clean: boolean; with_backing: boolean };
  backingVocalAnalysis?: BackingVocalAnalysis;
}
```

Features:
- Radio group for selection
- Disabled states for unavailable options
- Help text explaining each option
- AI recommendation badge (if backing vocals detected)

### 5. MuteRegionEditor.tsx

```tsx
interface MuteRegionEditorProps {
  duration: number;
  regions: MuteRegion[];
  onChange: (regions: MuteRegion[]) => void;
  waveformPeaks?: number[];
}
```

Features:
- Timeline with waveform background
- Click-drag to create regions
- Drag edges to resize
- Click region to delete
- List of regions with timestamps

### 6. CustomUpload.tsx

```tsx
interface CustomUploadProps {
  onUpload: (file: File) => Promise<void>;
  isUploading: boolean;
  acceptedFormats: string[];  // e.g., ['.flac', '.wav', '.mp3']
}
```

Features:
- Drag-and-drop zone
- File type validation
- Upload progress indicator
- Error handling

## Styling Approach

The source uses CSS variables that match the main frontend:
```css
:root {
  --background: #1a1a1a;
  --card: #2a2a2a;
  --text: #ffffff;
  --text-muted: #888888;
  --primary: #ff7acc;
  --border: #333333;
}
```

Convert to Tailwind classes:
- `background-color: var(--background)` → `bg-background`
- `color: var(--text-muted)` → `text-muted-foreground`
- `border-color: var(--border)` → `border-border`

## Canvas Components

The waveform viewer and mute region editor use `<canvas>`. Keep the canvas logic but:
1. Wrap in React component with proper lifecycle
2. Use refs for canvas access
3. Handle resize with ResizeObserver
4. Clean up event listeners on unmount

## Acceptance Criteria

1. **Build passes**: `cd frontend && npm run build` succeeds
2. **No vanilla JS**: All logic converted to React
3. **Placeholder replaced**: `InstrumentalSelectionPlaceholder` replaced with `InstrumentalSelector`
4. **Functionality works**:
   - Can play/pause both instrumental options
   - Waveform displays correctly
   - Can select between clean/with_backing/custom
   - Mute region editor works (create, resize, delete regions)
   - Custom upload works
   - Selection submits successfully
   - Redirects to dashboard after completion
5. **Keyboard shortcuts preserved**:
   - Space: Play/pause
   - 1/2: Switch between stems

## Execution Notes

This task is self-contained and can be done in a single session. The main complexity is the canvas-based waveform/timeline components.

### Suggested Order

1. Create types and API client additions
2. Build `SelectionOptions.tsx` (simplest, radio group)
3. Build `StemComparison.tsx` (audio elements)
4. Build `WaveformViewer.tsx` (canvas)
5. Build `MuteRegionEditor.tsx` (canvas + interaction)
6. Build `CustomUpload.tsx` (file handling)
7. Build `InstrumentalSelector.tsx` (assemble everything)
8. Integrate into `client.tsx`

### Testing

After completion:
```bash
cd frontend
npm run build
npm run dev
# Navigate to a job in awaiting_instrumental_selection state
```

## Related Files

- **Plan document**: `docs/archive/2026-01-09-frontend-consolidation-plan.md`
- **Source HTML**: `karaoke_gen/instrumental_review/static/index.html`
- **Backend endpoints**: `karaoke_gen/instrumental_review/server.py`
