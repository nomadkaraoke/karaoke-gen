/**
 * Tests for the persisted admin settings store.
 */
import { useAdminSettings } from '../lib/admin-settings';

describe('useAdminSettings', () => {
  beforeEach(() => {
    localStorage.clear();
    useAdminSettings.setState({ showTestData: false, showAwaitingAudioJobs: true });
  });

  it('defaults to hiding test data and showing jobs awaiting audio', () => {
    const state = useAdminSettings.getState();
    expect(state.showTestData).toBe(false);
    expect(state.showAwaitingAudioJobs).toBe(true);
  });

  it('toggles showAwaitingAudioJobs and persists it to localStorage', () => {
    useAdminSettings.getState().setShowAwaitingAudioJobs(false);
    expect(useAdminSettings.getState().showAwaitingAudioJobs).toBe(false);
    const persisted = JSON.parse(localStorage.getItem('admin-settings') || '{}');
    expect(persisted.state.showAwaitingAudioJobs).toBe(false);
  });
});
