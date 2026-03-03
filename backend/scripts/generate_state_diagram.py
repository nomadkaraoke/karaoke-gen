#!/usr/bin/env python3
"""
Generate a Mermaid state diagram from the job state machine.

This script reads the STATE_TRANSITIONS dictionary from models/job.py
and generates a Mermaid diagram that can be embedded in documentation.

Usage:
    python scripts/generate_state_diagram.py > docs/state-machine.mmd
    # Or to update ARCHITECTURE.md directly:
    python scripts/generate_state_diagram.py --update-docs

Created as part of state machine robustness improvements (2026-02-02).
"""
import sys
import argparse
from pathlib import Path

# Add backend to path for imports
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Import the state machine data
# Note: Must be run from an environment where karaoke_gen is installed
try:
    from models.job import STATE_TRANSITIONS, JobStatus
except ImportError:
    # Fallback: define the state machine inline for documentation purposes
    from enum import Enum

    class JobStatus(str, Enum):
        PENDING = "pending"
        SEARCHING_AUDIO = "searching_audio"
        AWAITING_AUDIO_SELECTION = "awaiting_audio_selection"
        DOWNLOADING_AUDIO = "downloading_audio"
        DOWNLOADING = "downloading"
        SEPARATING_STAGE1 = "separating_stage1"
        SEPARATING_STAGE2 = "separating_stage2"
        AUDIO_COMPLETE = "audio_complete"
        TRANSCRIBING = "transcribing"
        CORRECTING = "correcting"
        LYRICS_COMPLETE = "lyrics_complete"
        GENERATING_SCREENS = "generating_screens"
        APPLYING_PADDING = "applying_padding"
        AWAITING_REVIEW = "awaiting_review"
        IN_REVIEW = "in_review"
        REVIEW_COMPLETE = "review_complete"
        RENDERING_VIDEO = "rendering_video"
        AWAITING_INSTRUMENTAL_SELECTION = "awaiting_instrumental_selection"
        INSTRUMENTAL_SELECTED = "instrumental_selected"
        GENERATING_VIDEO = "generating_video"
        ENCODING = "encoding"
        PACKAGING = "packaging"
        UPLOADING = "uploading"
        NOTIFYING = "notifying"
        COMPLETE = "complete"
        PREP_COMPLETE = "prep_complete"
        FAILED = "failed"
        CANCELLED = "cancelled"
        QUEUED = "queued"
        PROCESSING = "processing"
        ERROR = "error"
        READY_FOR_FINALIZATION = "ready_for_finalization"
        FINALIZING = "finalizing"

    STATE_TRANSITIONS = {
        JobStatus.PENDING: [JobStatus.DOWNLOADING, JobStatus.SEARCHING_AUDIO, JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED, JobStatus.CANCELLED],
        JobStatus.SEARCHING_AUDIO: [JobStatus.AWAITING_AUDIO_SELECTION, JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED],
        JobStatus.AWAITING_AUDIO_SELECTION: [JobStatus.DOWNLOADING_AUDIO, JobStatus.FAILED, JobStatus.CANCELLED],
        JobStatus.DOWNLOADING_AUDIO: [JobStatus.DOWNLOADING, JobStatus.FAILED],
        JobStatus.DOWNLOADING: [JobStatus.SEPARATING_STAGE1, JobStatus.TRANSCRIBING, JobStatus.GENERATING_SCREENS, JobStatus.FAILED],
        JobStatus.SEPARATING_STAGE1: [JobStatus.SEPARATING_STAGE2, JobStatus.FAILED],
        JobStatus.SEPARATING_STAGE2: [JobStatus.AUDIO_COMPLETE, JobStatus.FAILED],
        JobStatus.AUDIO_COMPLETE: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],
        JobStatus.TRANSCRIBING: [JobStatus.CORRECTING, JobStatus.FAILED],
        JobStatus.CORRECTING: [JobStatus.LYRICS_COMPLETE, JobStatus.FAILED],
        JobStatus.LYRICS_COMPLETE: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],
        JobStatus.GENERATING_SCREENS: [JobStatus.APPLYING_PADDING, JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
        JobStatus.APPLYING_PADDING: [JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
        JobStatus.AWAITING_REVIEW: [JobStatus.IN_REVIEW, JobStatus.REVIEW_COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED],
        JobStatus.IN_REVIEW: [JobStatus.REVIEW_COMPLETE, JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
        JobStatus.REVIEW_COMPLETE: [JobStatus.RENDERING_VIDEO, JobStatus.FAILED],
        JobStatus.RENDERING_VIDEO: [JobStatus.INSTRUMENTAL_SELECTED, JobStatus.FAILED],
        JobStatus.INSTRUMENTAL_SELECTED: [JobStatus.GENERATING_VIDEO, JobStatus.FAILED],
        JobStatus.GENERATING_VIDEO: [JobStatus.ENCODING, JobStatus.FAILED],
        JobStatus.ENCODING: [JobStatus.PACKAGING, JobStatus.COMPLETE, JobStatus.PREP_COMPLETE, JobStatus.FAILED],
        JobStatus.PACKAGING: [JobStatus.UPLOADING, JobStatus.COMPLETE, JobStatus.PREP_COMPLETE, JobStatus.FAILED],
        JobStatus.UPLOADING: [JobStatus.NOTIFYING, JobStatus.COMPLETE, JobStatus.FAILED],
        JobStatus.NOTIFYING: [JobStatus.COMPLETE, JobStatus.FAILED],
    }


def generate_mermaid() -> str:
    """
    Generate a Mermaid state diagram from STATE_TRANSITIONS.

    Returns:
        Mermaid diagram as a string
    """
    lines = ["stateDiagram-v2"]
    lines.append("    direction TB")
    lines.append("")

    # Group states by category for better visualization
    categories = {
        "Initial": [JobStatus.PENDING],
        "Audio Search": [
            JobStatus.SEARCHING_AUDIO,
            JobStatus.AWAITING_AUDIO_SELECTION,
            JobStatus.DOWNLOADING_AUDIO,
        ],
        "Processing": [
            JobStatus.DOWNLOADING,
            JobStatus.SEPARATING_STAGE1,
            JobStatus.SEPARATING_STAGE2,
            JobStatus.AUDIO_COMPLETE,
            JobStatus.TRANSCRIBING,
            JobStatus.CORRECTING,
            JobStatus.LYRICS_COMPLETE,
        ],
        "Screens": [
            JobStatus.GENERATING_SCREENS,
            JobStatus.APPLYING_PADDING,
        ],
        "Review": [
            JobStatus.AWAITING_REVIEW,
            JobStatus.IN_REVIEW,
            JobStatus.REVIEW_COMPLETE,
        ],
        "Video": [
            JobStatus.RENDERING_VIDEO,
            JobStatus.INSTRUMENTAL_SELECTED,
            JobStatus.GENERATING_VIDEO,
            JobStatus.ENCODING,
            JobStatus.PACKAGING,
        ],
        "Distribution": [
            JobStatus.UPLOADING,
            JobStatus.NOTIFYING,
        ],
        "Terminal": [
            JobStatus.COMPLETE,
            JobStatus.PREP_COMPLETE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ],
    }

    # Add state definitions with notes for key states
    lines.append("    %% State definitions")
    key_states = {
        JobStatus.PENDING: "Job created",
        JobStatus.AWAITING_AUDIO_SELECTION: "⚠️ USER ACTION",
        JobStatus.AWAITING_REVIEW: "⚠️ USER ACTION",
        JobStatus.COMPLETE: "✅ Success",
        JobStatus.FAILED: "❌ Error",
    }

    for status, note in key_states.items():
        lines.append(f"    {status.value}: {status.value}")
        lines.append(f"    note right of {status.value}: {note}")

    lines.append("")
    lines.append("    %% Transitions")

    # Generate transitions
    for from_status, to_statuses in STATE_TRANSITIONS.items():
        for to_status in to_statuses:
            # Skip deprecated statuses
            if to_status in [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.ERROR,
                             JobStatus.READY_FOR_FINALIZATION, JobStatus.FINALIZING]:
                continue
            if from_status in [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.ERROR,
                               JobStatus.READY_FOR_FINALIZATION, JobStatus.FINALIZING]:
                continue

            lines.append(f"    {from_status.value} --> {to_status.value}")

    # Add start and end markers
    lines.append("")
    lines.append("    %% Start and end")
    lines.append("    [*] --> pending")
    lines.append("    complete --> [*]")
    lines.append("    prep_complete --> [*]")
    lines.append("    failed --> [*]")
    lines.append("    cancelled --> [*]")

    return "\n".join(lines)


def generate_markdown_section() -> str:
    """
    Generate a markdown section with the state diagram.

    Returns:
        Markdown string with the diagram
    """
    diagram = generate_mermaid()

    return f"""## Job State Machine

The following diagram shows all valid state transitions for a karaoke generation job.

Key states:
- **PENDING**: Initial state when job is created
- **AWAITING_AUDIO_SELECTION**: Human selects audio source (for artist+title search)
- **AWAITING_REVIEW**: Human reviews lyrics and selects instrumental
- **COMPLETE**: All processing finished successfully

```mermaid
{diagram}
```

### Parallel Processing

Jobs use parallel processing for audio separation and lyrics transcription:

1. **DOWNLOADING** triggers both:
   - Audio worker → SEPARATING_STAGE1 → SEPARATING_STAGE2 → AUDIO_COMPLETE
   - Lyrics worker → TRANSCRIBING → CORRECTING → LYRICS_COMPLETE

2. When BOTH complete, the screens worker is triggered automatically.

### Human Review Points

Jobs wait for human input at these points:
- **AWAITING_AUDIO_SELECTION**: User selects audio source from search results
- **AWAITING_REVIEW**: User reviews/corrects lyrics AND selects instrumental

### Error Handling

- Any state can transition to **FAILED** on unrecoverable errors
- **CANCELLED** is set when user cancels the job

"""


def main():
    parser = argparse.ArgumentParser(description="Generate state machine diagram")
    parser.add_argument(
        "--update-docs",
        action="store_true",
        help="Update ARCHITECTURE.md with the diagram"
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Output markdown section instead of raw Mermaid"
    )
    args = parser.parse_args()

    if args.update_docs:
        # TODO: Implement auto-update of ARCHITECTURE.md
        print("Auto-update of ARCHITECTURE.md not yet implemented")
        print("Please copy the output below to the appropriate section:")
        print()
        print(generate_markdown_section())
    elif args.markdown:
        print(generate_markdown_section())
    else:
        print(generate_mermaid())


if __name__ == "__main__":
    main()
