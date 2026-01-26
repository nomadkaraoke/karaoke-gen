/**
 * Unit tests for job-status.ts utility functions
 */

import { getJobStep, formatStepIndicator, isBlockingStatus, getJobProgressPercent, getJobPriority, sortJobsByPriority, JobStep } from '../lib/job-status';
import type { Job } from '../lib/api';

// Helper to create a minimal Job object for testing
function createJob(status: string, stateData?: Record<string, any>): Job {
  return {
    job_id: 'test-job-123',
    status,
    progress: 0,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    state_data: stateData,
  };
}

describe('getJobStep', () => {
  describe('Step 1: Setup', () => {
    it('returns step 1 for pending status', () => {
      const job = createJob('pending');
      const result = getJobStep(job);
      expect(result.step).toBe(1);
      expect(result.total).toBe(10);
      expect(result.label).toBe('Setting up');
      expect(result.isBlocking).toBe(false);
      expect(result.color).toBe('text-muted-foreground');
    });
  });

  describe('Step 2: Audio Search', () => {
    it('returns step 2 for searching_audio status', () => {
      const job = createJob('searching_audio');
      const result = getJobStep(job);
      expect(result.step).toBe(2);
      expect(result.label).toBe('Searching for audio');
      expect(result.isBlocking).toBe(false);
    });

    it('returns step 2 with blocking for awaiting_audio_selection', () => {
      const job = createJob('awaiting_audio_selection');
      const result = getJobStep(job);
      expect(result.step).toBe(2);
      expect(result.label).toBe('Select audio source');
      expect(result.isBlocking).toBe(true);
      expect(result.color).toBe('text-amber-400');
    });
  });

  describe('Step 3: Download', () => {
    it('returns step 3 for downloading_audio status', () => {
      const job = createJob('downloading_audio');
      const result = getJobStep(job);
      expect(result.step).toBe(3);
      expect(result.label).toBe('Downloading audio');
    });

    it('returns step 3 for downloading status without worker progress', () => {
      const job = createJob('downloading');
      const result = getJobStep(job);
      expect(result.step).toBe(3);
      expect(result.label).toBe('Downloading');
    });

    it('returns step 3 for downloading with empty state_data', () => {
      const job = createJob('downloading', {});
      const result = getJobStep(job);
      expect(result.step).toBe(3);
      expect(result.label).toBe('Downloading');
    });
  });

  describe('Step 3→4 Transition: Downloading with active workers', () => {
    // When backend status is "downloading" but workers are actively running,
    // the frontend should show step 4 (Processing) to avoid appearing stuck

    it('returns step 4 when downloading with active audio worker', () => {
      const job = createJob('downloading', {
        audio_progress: { stage: 'separating_stage1', progress: 10 },
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.color).toBe('text-purple-400');
    });

    it('returns step 4 when downloading with active lyrics worker', () => {
      const job = createJob('downloading', {
        lyrics_progress: { stage: 'transcribing', progress: 10 },
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.color).toBe('text-purple-400');
    });

    it('returns step 4 with combined label when both workers active', () => {
      const job = createJob('downloading', {
        audio_progress: { stage: 'separating_stage1', progress: 10 },
        lyrics_progress: { stage: 'transcribing', progress: 10 },
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toMatch(/Audio.*\+.*Transcribing|Transcribing.*\+.*Audio/);
    });

    it('shows only active worker when one is complete', () => {
      const job = createJob('downloading', {
        audio_progress: { stage: 'audio_complete' },
        lyrics_progress: { stage: 'transcribing' },
        audio_complete: true,
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Transcribing');
    });

    it('shows processing complete when both workers done', () => {
      const job = createJob('downloading', {
        audio_progress: { stage: 'audio_complete' },
        lyrics_progress: { stage: 'lyrics_complete' },
        audio_complete: true,
        lyrics_complete: true,
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Processing complete');
    });

    it('returns step 3 when state_data has no stage info', () => {
      // state_data exists but no actual progress stage set
      const job = createJob('downloading', {
        audio_progress: {},
        lyrics_progress: {},
      });
      const result = getJobStep(job);
      expect(result.step).toBe(3);
      expect(result.label).toBe('Downloading');
    });
  });

  describe('Step 4: Parallel Processing', () => {
    it('returns step 4 for separating_stage1', () => {
      const job = createJob('separating_stage1');
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Separating audio (1/2)');
    });

    it('returns step 4 for separating_stage2', () => {
      const job = createJob('separating_stage2');
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Separating audio (2/2)');
    });

    it('returns step 4 for transcribing', () => {
      const job = createJob('transcribing');
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Transcribing lyrics');
    });

    it('returns step 4 for correcting', () => {
      const job = createJob('correcting');
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      expect(result.label).toBe('Correcting lyrics');
    });

    it('shows combined progress when both audio and lyrics are in progress', () => {
      const job = createJob('separating_stage1', {
        audio_progress: { stage: 'separating_stage1' },
        lyrics_progress: { stage: 'transcribing' },
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
      // Should show combined label
      expect(result.label).toMatch(/Audio|Transcribing/);
    });

    it('shows combined status when audio done but lyrics in progress', () => {
      const job = createJob('lyrics_complete', {
        audio_progress: { stage: 'audio_complete' },
        lyrics_progress: { stage: 'transcribing' },
        audio_complete: true,
      });
      const result = getJobStep(job);
      expect(result.step).toBe(4);
    });
  });

  describe('Step 5: Screen Generation', () => {
    it('returns step 5 for generating_screens', () => {
      const job = createJob('generating_screens');
      const result = getJobStep(job);
      expect(result.step).toBe(5);
      expect(result.label).toBe('Generating screens');
    });

    it('returns step 5 for applying_padding', () => {
      const job = createJob('applying_padding');
      const result = getJobStep(job);
      expect(result.step).toBe(5);
      expect(result.label).toBe('Syncing countdown');
    });
  });

  describe('Step 6: Lyrics Review (BLOCKING)', () => {
    it('returns step 6 with blocking for awaiting_review', () => {
      const job = createJob('awaiting_review');
      const result = getJobStep(job);
      expect(result.step).toBe(6);
      expect(result.label).toBe('Review lyrics');
      expect(result.isBlocking).toBe(true);
      expect(result.color).toBe('text-amber-400');
    });

    it('returns step 6 for in_review', () => {
      const job = createJob('in_review');
      const result = getJobStep(job);
      expect(result.step).toBe(6);
      expect(result.label).toBe('In review');
      expect(result.isBlocking).toBe(true);
    });
  });

  describe('Step 7: Video Rendering', () => {
    it('returns step 7 for review_complete', () => {
      const job = createJob('review_complete');
      const result = getJobStep(job);
      expect(result.step).toBe(7);
      expect(result.label).toBe('Starting render');
    });

    it('returns step 7 for rendering_video', () => {
      const job = createJob('rendering_video');
      const result = getJobStep(job);
      expect(result.step).toBe(7);
      expect(result.label).toBe('Rendering video');
    });
  });

  describe('Step 8: Instrumental Selection (BLOCKING)', () => {
    it('returns step 8 with blocking for awaiting_instrumental_selection', () => {
      const job = createJob('awaiting_instrumental_selection');
      const result = getJobStep(job);
      expect(result.step).toBe(8);
      expect(result.label).toBe('Select instrumental');
      expect(result.isBlocking).toBe(true);
      expect(result.color).toBe('text-amber-400');
    });
  });

  describe('Step 9: Final Encoding', () => {
    it('returns step 9 for instrumental_selected', () => {
      const job = createJob('instrumental_selected');
      const result = getJobStep(job);
      expect(result.step).toBe(9);
      expect(result.label).toBe('Starting final encode');
    });

    it('returns step 9 for generating_video', () => {
      const job = createJob('generating_video');
      const result = getJobStep(job);
      expect(result.step).toBe(9);
      expect(result.label).toBe('Generating final video');
    });

    it('returns step 9 for encoding', () => {
      const job = createJob('encoding');
      const result = getJobStep(job);
      expect(result.step).toBe(9);
      expect(result.label).toBe('Encoding video');
    });

    it('returns step 9 for packaging', () => {
      const job = createJob('packaging');
      const result = getJobStep(job);
      expect(result.step).toBe(9);
      expect(result.label).toBe('Packaging files');
    });
  });

  describe('Step 10: Distribution / Complete', () => {
    it('returns step 10 for uploading', () => {
      const job = createJob('uploading');
      const result = getJobStep(job);
      expect(result.step).toBe(10);
      expect(result.label).toBe('Uploading');
      expect(result.color).toBe('text-green-400');
    });

    it('returns step 10 for notifying', () => {
      const job = createJob('notifying');
      const result = getJobStep(job);
      expect(result.step).toBe(10);
      expect(result.label).toBe('Sending notifications');
    });

    it('returns step 10 for complete', () => {
      const job = createJob('complete');
      const result = getJobStep(job);
      expect(result.step).toBe(10);
      expect(result.label).toBe('Complete');
      expect(result.color).toBe('text-green-400');
    });

    it('returns step 10 for prep_complete', () => {
      const job = createJob('prep_complete');
      const result = getJobStep(job);
      expect(result.step).toBe(10);
      expect(result.label).toBe('Prep complete');
    });
  });

  describe('Terminal states', () => {
    it('returns step 0 for failed status', () => {
      const job = createJob('failed');
      const result = getJobStep(job);
      expect(result.step).toBe(0);
      expect(result.label).toBe('Failed');
      expect(result.color).toBe('text-red-400');
    });

    it('returns step 0 for cancelled status', () => {
      const job = createJob('cancelled');
      const result = getJobStep(job);
      expect(result.step).toBe(0);
      expect(result.label).toBe('Cancelled');
      expect(result.color).toBe('text-muted-foreground');
    });
  });

  describe('Unknown status handling', () => {
    it('returns step 0 with humanized label for unknown status', () => {
      const job = createJob('some_unknown_status');
      const result = getJobStep(job);
      expect(result.step).toBe(0);
      expect(result.label).toBe('some unknown status');
      expect(result.isBlocking).toBe(false);
      expect(result.color).toBe('text-muted-foreground');
    });

    it('handles null status gracefully', () => {
      const job = createJob(null as unknown as string);
      const result = getJobStep(job);
      expect(result.step).toBe(1); // Falls back to 'pending'
    });

    it('handles undefined status gracefully', () => {
      const job = createJob(undefined as unknown as string);
      const result = getJobStep(job);
      expect(result.step).toBe(1); // Falls back to 'pending'
    });
  });

  describe('Case insensitivity', () => {
    it('handles uppercase status', () => {
      const job = createJob('PENDING');
      const result = getJobStep(job);
      expect(result.step).toBe(1);
    });

    it('handles mixed case status', () => {
      const job = createJob('Downloading');
      const result = getJobStep(job);
      expect(result.step).toBe(3);
    });
  });
});

describe('formatStepIndicator', () => {
  it('formats active step correctly', () => {
    expect(formatStepIndicator(4, 10, 'Processing')).toBe('[4/10] Processing');
  });

  it('omits step number for terminal states (step 0)', () => {
    expect(formatStepIndicator(0, 10, 'Failed')).toBe('Failed');
  });

  it('formats first step correctly', () => {
    expect(formatStepIndicator(1, 10, 'Setting up')).toBe('[1/10] Setting up');
  });

  it('formats last step correctly', () => {
    expect(formatStepIndicator(10, 10, 'Complete')).toBe('[10/10] Complete');
  });
});

describe('isBlockingStatus', () => {
  it('returns true for awaiting_review', () => {
    expect(isBlockingStatus('awaiting_review')).toBe(true);
  });

  it('returns true for in_review', () => {
    expect(isBlockingStatus('in_review')).toBe(true);
  });

  it('returns true for awaiting_audio_selection', () => {
    expect(isBlockingStatus('awaiting_audio_selection')).toBe(true);
  });

  it('returns true for awaiting_instrumental_selection', () => {
    expect(isBlockingStatus('awaiting_instrumental_selection')).toBe(true);
  });

  it('returns false for non-blocking statuses', () => {
    expect(isBlockingStatus('pending')).toBe(false);
    expect(isBlockingStatus('downloading')).toBe(false);
    expect(isBlockingStatus('complete')).toBe(false);
    expect(isBlockingStatus('failed')).toBe(false);
  });

  it('returns false for unknown statuses', () => {
    expect(isBlockingStatus('unknown_status')).toBe(false);
  });

  it('handles case insensitivity', () => {
    expect(isBlockingStatus('AWAITING_REVIEW')).toBe(true);
  });

  it('handles null/undefined gracefully', () => {
    expect(isBlockingStatus(null as unknown as string)).toBe(false);
    expect(isBlockingStatus(undefined as unknown as string)).toBe(false);
  });
});

describe('getJobProgressPercent', () => {
  it('returns 0 for terminal states (step 0)', () => {
    const job = createJob('failed');
    expect(getJobProgressPercent(job)).toBe(0);
  });

  it('returns 10% for step 1', () => {
    const job = createJob('pending');
    expect(getJobProgressPercent(job)).toBe(10);
  });

  it('returns 40% for step 4', () => {
    const job = createJob('transcribing');
    expect(getJobProgressPercent(job)).toBe(40);
  });

  it('returns 60% for step 6', () => {
    const job = createJob('awaiting_review');
    expect(getJobProgressPercent(job)).toBe(60);
  });

  it('returns 100% for step 10', () => {
    const job = createJob('complete');
    expect(getJobProgressPercent(job)).toBe(100);
  });

  it('returns 0 for unknown status', () => {
    const job = createJob('unknown_status_xyz');
    expect(getJobProgressPercent(job)).toBe(0);
  });
});

describe('getJobPriority', () => {
  it('returns 0 for blocking statuses (user action needed)', () => {
    expect(getJobPriority(createJob('awaiting_review'))).toBe(0);
    expect(getJobPriority(createJob('awaiting_audio_selection'))).toBe(0);
    expect(getJobPriority(createJob('awaiting_instrumental_selection'))).toBe(0);
    expect(getJobPriority(createJob('in_review'))).toBe(0);
  });

  it('returns 1 for active/processing statuses', () => {
    expect(getJobPriority(createJob('pending'))).toBe(1);
    expect(getJobPriority(createJob('downloading'))).toBe(1);
    expect(getJobPriority(createJob('rendering_video'))).toBe(1);
    expect(getJobPriority(createJob('separating_stage1'))).toBe(1);
  });

  it('returns 2 for completed statuses', () => {
    expect(getJobPriority(createJob('complete'))).toBe(2);
    expect(getJobPriority(createJob('prep_complete'))).toBe(2);
  });

  it('returns 3 for failed/cancelled statuses', () => {
    expect(getJobPriority(createJob('failed'))).toBe(3);
    expect(getJobPriority(createJob('cancelled'))).toBe(3);
  });

  it('handles case insensitivity', () => {
    expect(getJobPriority(createJob('AWAITING_REVIEW'))).toBe(0);
    expect(getJobPriority(createJob('COMPLETE'))).toBe(2);
  });
});

describe('sortJobsByPriority', () => {
  // Helper to create job with specific date
  function createJobWithDate(status: string, createdAt: string): Job {
    return {
      job_id: `job-${status}-${createdAt}`,
      status,
      progress: 0,
      created_at: createdAt,
      updated_at: createdAt,
    };
  }

  it('sorts blocking jobs before completed jobs', () => {
    const jobs = [
      createJobWithDate('complete', '2024-01-01T12:00:00Z'),
      createJobWithDate('awaiting_review', '2024-01-01T12:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted[0].status).toBe('awaiting_review');
    expect(sorted[1].status).toBe('complete');
  });

  it('sorts blocking jobs before processing jobs', () => {
    const jobs = [
      createJobWithDate('rendering_video', '2024-01-01T12:00:00Z'),
      createJobWithDate('awaiting_review', '2024-01-01T12:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted[0].status).toBe('awaiting_review');
    expect(sorted[1].status).toBe('rendering_video');
  });

  it('sorts processing jobs before completed jobs', () => {
    const jobs = [
      createJobWithDate('complete', '2024-01-01T12:00:00Z'),
      createJobWithDate('rendering_video', '2024-01-01T12:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted[0].status).toBe('rendering_video');
    expect(sorted[1].status).toBe('complete');
  });

  it('sorts completed jobs before failed jobs', () => {
    const jobs = [
      createJobWithDate('failed', '2024-01-01T12:00:00Z'),
      createJobWithDate('complete', '2024-01-01T12:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted[0].status).toBe('complete');
    expect(sorted[1].status).toBe('failed');
  });

  it('sorts by created_at (newest first) within same priority', () => {
    const jobs = [
      createJobWithDate('complete', '2024-01-01T10:00:00Z'),
      createJobWithDate('complete', '2024-01-01T14:00:00Z'),
      createJobWithDate('complete', '2024-01-01T12:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted[0].created_at).toBe('2024-01-01T14:00:00Z');
    expect(sorted[1].created_at).toBe('2024-01-01T12:00:00Z');
    expect(sorted[2].created_at).toBe('2024-01-01T10:00:00Z');
  });

  it('does not mutate the original array', () => {
    const jobs = [
      createJobWithDate('complete', '2024-01-01T12:00:00Z'),
      createJobWithDate('awaiting_review', '2024-01-01T12:00:00Z'),
    ];
    const originalOrder = [...jobs];
    sortJobsByPriority(jobs);
    expect(jobs[0].status).toBe(originalOrder[0].status);
    expect(jobs[1].status).toBe(originalOrder[1].status);
  });

  it('handles empty array', () => {
    const sorted = sortJobsByPriority([]);
    expect(sorted).toEqual([]);
  });

  it('handles single job', () => {
    const jobs = [createJobWithDate('complete', '2024-01-01T12:00:00Z')];
    const sorted = sortJobsByPriority(jobs);
    expect(sorted.length).toBe(1);
    expect(sorted[0].status).toBe('complete');
  });

  it('correctly orders a realistic job list', () => {
    const jobs = [
      createJobWithDate('complete', '2024-01-01T10:00:00Z'),
      createJobWithDate('failed', '2024-01-01T11:00:00Z'),
      createJobWithDate('awaiting_review', '2024-01-01T09:00:00Z'),
      createJobWithDate('rendering_video', '2024-01-01T12:00:00Z'),
      createJobWithDate('complete', '2024-01-01T14:00:00Z'),
    ];
    const sorted = sortJobsByPriority(jobs);
    // Priority order: blocking (0), processing (1), complete (2), failed (3)
    expect(sorted[0].status).toBe('awaiting_review'); // blocking
    expect(sorted[1].status).toBe('rendering_video'); // processing
    expect(sorted[2].status).toBe('complete'); // complete, newer
    expect(sorted[2].created_at).toBe('2024-01-01T14:00:00Z');
    expect(sorted[3].status).toBe('complete'); // complete, older
    expect(sorted[3].created_at).toBe('2024-01-01T10:00:00Z');
    expect(sorted[4].status).toBe('failed'); // failed
  });
});
