/**
 * API client for karaoke-gen backend
 */

import type { VideoThemeSummary, VideoThemeDetail, ThemesListResponse, ThemeDetailResponse, ColorOverrides } from './video-themes';
import type { MagicLinkResponse, VerifyMagicLinkResponse, UserProfileResponse } from './types';
import type { CorrectionData, CorrectionAnnotation, EditLog, SearchLyricsResponse } from './lyrics-review/types';

// In development, use relative URLs to go through Next.js proxy (avoids CORS)
// In production (static export), use the full backend URL
// For local mode (localhost or 127.0.0.1), use relative URLs
const isLocalHostname = typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');
export const API_BASE_URL = isLocalHostname
  ? ''  // Relative URL - goes to the local server
  : (process.env.NEXT_PUBLIC_API_URL || 'https://api.nomadkaraoke.com');

// Token management - stored in localStorage (client-side only)
// Always read fresh from localStorage to avoid stale cached values
// during Next.js module caching or hydration edge cases

export function setAccessToken(token: string) {
  if (typeof window !== 'undefined') {
    localStorage.setItem('karaoke_access_token', token);
  }
}

export function getAccessToken(): string | null {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('karaoke_access_token');
  }
  return null;
}

export function clearAccessToken() {
  if (typeof window !== 'undefined') {
    localStorage.removeItem('karaoke_access_token');
  }
}

function getAuthHeaders(): HeadersInit {
  const headers: HeadersInit = {};
  const token = getAccessToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export interface Job {
  job_id: string;
  status: string;
  progress: number;
  created_at: string;
  updated_at: string;
  url?: string;
  artist?: string;
  title?: string;
  filename?: string;
  error_message?: string;
  error_details?: Record<string, any>;
  file_urls?: Record<string, any>;
  state_data?: Record<string, any>;
  timeline?: Array<{
    status: string;
    timestamp: string;
    progress?: number;
    message?: string;
  }>;
  review_token?: string;
  instrumental_token?: string;
  audio_hash?: string;
  non_interactive?: boolean;
  is_private?: boolean;
  user_email?: string;
  outputs_deleted_at?: string;
  outputs_deleted_by?: string;
  edit_count?: number;
  existing_instrumental_gcs_path?: string;
  // Audio search fields
  audio_search_artist?: string;
  audio_search_title?: string;
  // Audio source tracking
  audio_source_type?: string;
  source_name?: string;
  source_id?: string;
  target_file?: string;
  download_url?: string;
  // Theme and branding
  theme_id?: string;
  brand_prefix?: string;
  // Customer order fields
  customer_email?: string;
  // Request tracking metadata
  request_metadata?: Record<string, any>;
}

export interface UploadJobResponse {
  status: string;
  job_id: string;
  message: string;
  filename?: string;
  style_assets_uploaded?: string[];
  server_version?: string;
  distribution_services?: Record<string, any>;
}

export interface EditTrackResponse {
  status: string;
  job_id: string;
  message: string;
  review_url: string;
  review_token: string;
  metadata_updated: boolean;
  cleanup_results: Record<string, any>;
}

export interface InstrumentalOption {
  id: string;
  label: string;
  audio_url: string;
  duration_seconds?: number;
}

export interface InstrumentalOptionsResponse {
  options: InstrumentalOption[];
  status: string;
  artist?: string;
  title?: string;
}

// Instrumental Review Types
export interface MuteRegion {
  start_seconds: number;
  end_seconds: number;
}

export interface AudibleSegment {
  start_seconds: number;
  end_seconds: number;
  confidence?: number;
}

export interface BackingVocalAnalysis {
  audible_segments: AudibleSegment[];
  audible_percentage: number;
  recommended_selection: 'clean' | 'with_backing';
}

export interface InstrumentalAnalysis {
  job_id?: string;
  artist?: string;
  title?: string;
  duration_seconds?: number;
  analysis: BackingVocalAnalysis;
  audio_urls: {
    clean?: string;
    with_backing?: string;
    backing_vocals?: string;
    original?: string;
    custom?: string;
    uploaded?: string;
  };
  has_uploaded_instrumental?: boolean;
  has_original?: boolean;
}

export interface WaveformData {
  amplitudes: number[];
  duration_seconds?: number;
  duration?: number;  // Legacy field name
  sample_rate?: number;
}

export interface AudioEditEntry {
  edit_id: string;
  operation: string;
  params: Record<string, unknown>;
  duration_before: number;
  duration_after: number;
  timestamp: string;
}

export interface AudioEditInfo {
  job_id: string;
  artist?: string;
  title?: string;
  original_duration_seconds: number;
  current_duration_seconds: number;
  original_audio_url: string;
  current_audio_url: string;
  waveform_data: { amplitudes: number[] };
  original_waveform_data: { amplitudes: number[] };
  edit_stack: AudioEditEntry[];
  can_undo: boolean;
  can_redo: boolean;
}

export interface AudioEditSessionSummary {
  total_operations: number;
  operations_breakdown: Record<string, number>;
  duration_change_seconds: number;
  net_duration_seconds: number;
}

export interface AudioEditSessionMeta {
  session_id: string;
  job_id: string;
  user_email?: string;
  edit_count: number;
  trigger: string;
  summary?: AudioEditSessionSummary;
  created_at: string;
  updated_at: string;
}

export interface AudioEditSessionWithData extends AudioEditSessionMeta {
  edit_data?: {
    entries: AudioEditEntry[];
    [key: string]: unknown;
  };
}

export interface AudioEditResponse {
  status: string;
  edit_id?: string;
  operation?: string;
  duration_before: number;
  duration_after: number;
  current_audio_url: string;
  waveform_data: { amplitudes: number[] };
  edit_stack: AudioEditEntry[];
  can_undo: boolean;
  can_redo: boolean;
}

export type InstrumentalSelectionType = 'clean' | 'with_backing' | 'custom' | 'uploaded' | 'original';

export interface DownloadUrlsResponse {
  job_id: string;
  artist?: string;
  title?: string;
  download_urls: Record<string, any>;
}

export interface AudioSearchResult {
  index: number;
  title: string;
  artist: string;
  provider: string;
  url?: string;
  duration?: number;
  quality?: string;
  source_id?: string;
  seeders?: number;
  is_lossless?: boolean;
  year?: number;
  album?: string;
  // Additional fields from CLI
  category?: string;
  release_type?: string;
  label?: string;
  size_mb?: number;
  filename?: string;
  views?: number;
  bitrate?: number;
}

export interface AudioSearchResponse {
  status: string;
  job_id: string;
  results: AudioSearchResult[];
  total_results: number;
  message?: string;
}

export interface JobLog {
  timestamp: string;
  level: string;
  message: string;
  details?: Record<string, any>;
}

export interface EncodingWorkerHealth {
  available: boolean;
  status: string;  // 'ok' | 'offline' | 'not_configured'
  version?: string | null;
  active_jobs?: number;
  queue_length?: number;
  error?: string;
}

export interface FlacfetchHealth {
  available: boolean;
  status: string;  // 'ok' | 'offline' | 'not_configured'
  version?: string | null;
  error?: string;
}

export interface AudioSeparatorHealth {
  available: boolean;
  status: string;  // 'ok' | 'offline' | 'not_configured'
  version?: string | null;
  error?: string;
}

export interface ServiceStatus {
  status: string;  // 'ok' | 'offline'
  version?: string | null;
  deployed_at?: string | null;
  commit_sha?: string | null;
  pr_number?: string | null;
  pr_title?: string | null;
  active_jobs?: number;
  admin_details?: {
    // Encoder blue-green
    primary_vm?: string;
    primary_ip?: string;
    primary_version?: string;
    primary_deployed_at?: string;
    secondary_vm?: string;
    secondary_ip?: string;
    secondary_version?: string;
    secondary_deployed_at?: string;
    last_swap_at?: string;
    deploy_in_progress?: boolean;
    active_jobs?: number;
    queue_length?: number;
    // Error details for offline services
    error?: string | null;
  };
}

export interface SystemStatus {
  services: {
    frontend: ServiceStatus;
    backend: ServiceStatus;
    encoder: ServiceStatus;
    flacfetch: ServiceStatus;
    separator: ServiceStatus;
  };
}

// Signed URL upload types
export interface SignedUploadUrl {
  file_type: string;
  gcs_path: string;
  upload_url: string;
  content_type: string;
}

export interface CreateJobWithUploadUrlsResponse {
  status: string;
  job_id: string;
  message: string;
  upload_urls: SignedUploadUrl[];
  server_version: string;
}

export interface UploadProgress {
  phase: 'creating' | 'uploading' | 'finalizing';
  loaded: number;
  total: number;
}

class ApiError extends Error {
  status: number;
  data?: any;
  
  constructor(message: string, status: number, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

// Exported for testing
export function extractErrorMessage(data: any, fallback: string): string {
  // Handle various error response formats
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.message === 'string') return data.message;
  if (typeof data?.error === 'string') return data.error;
  // Handle nested error objects
  if (data?.detail?.message) return String(data.detail.message);
  if (data?.detail?.error) return String(data.detail.error);
  // If detail is an object, try to stringify it meaningfully
  if (data?.detail && typeof data.detail === 'object') {
    try {
      return JSON.stringify(data.detail);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = { detail: response.statusText };
    }
    throw new ApiError(
      extractErrorMessage(data, `Request failed with status ${response.status}`),
      response.status,
      data
    );
  }
  return response.json();
}

// --- Catalog types (song/artist autocomplete + community check) ---

export interface CatalogArtistResult {
  name: string;
  mbid?: string;
  disambiguation?: string;
  artist_type?: string;
  spotify_id?: string;
  popularity?: number;
  genres?: string[];
  tags?: string[];
}

export interface CatalogTrackResult {
  track_name: string;
  artist_name: string;
  track_id?: string;
  artist_id?: string;
  popularity?: number;
  duration_ms?: number;
  explicit?: boolean;
}

export interface CommunityTrack {
  brand_name: string;
  brand_code: string;
  youtube_url: string;
  is_community: boolean;
}

export interface CommunityCheckSong {
  title: string;
  artist: string;
  community_tracks: CommunityTrack[];
}

export interface CommunityCheckResponse {
  has_community: boolean;
  songs: CommunityCheckSong[];
  best_youtube_url: string | null;
}

export const api = {
  /**
   * List all jobs
   */
  async listJobs(params?: {
    status?: string;
    limit?: number;
    exclude_test?: boolean;
    fields?: 'summary';
    hide_completed?: boolean;
    search?: string;
  }): Promise<Job[]> {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    if (params?.fields) searchParams.set('fields', params.fields);
    if (params?.hide_completed !== undefined) searchParams.set('hide_completed', String(params.hide_completed));
    if (params?.search) searchParams.set('search', params.search);

    const url = `${API_BASE_URL}/api/jobs${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse<Job[]>(response);
  },
  
  /**
   * Get a single job by ID
   */
  async getJob(jobId: string): Promise<Job> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`, {
      headers: getAuthHeaders()
    });
    return handleResponse<Job>(response);
  },
  
  /**
   * Upload a file and create a new job
   */
  async uploadJob(
    file: File,
    artist: string,
    title: string,
    options?: {
      enable_cdg?: boolean;
      enable_txt?: boolean;
      brand_prefix?: string;
      enable_youtube_upload?: boolean;
      theme_id?: string;
      color_overrides?: ColorOverrides;
      non_interactive?: boolean;
      is_private?: boolean;
      requires_audio_edit?: boolean;
    }
  ): Promise<UploadJobResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('artist', artist);
    formData.append('title', title);

    if (options?.enable_cdg !== undefined) {
      formData.append('enable_cdg', String(options.enable_cdg));
    }
    if (options?.enable_txt !== undefined) {
      formData.append('enable_txt', String(options.enable_txt));
    }
    if (options?.brand_prefix) {
      formData.append('brand_prefix', options.brand_prefix);
    }
    if (options?.enable_youtube_upload !== undefined) {
      formData.append('enable_youtube_upload', String(options.enable_youtube_upload));
    }
    if (options?.theme_id) {
      formData.append('theme_id', options.theme_id);
    }
    if (options?.color_overrides) {
      formData.append('color_overrides', JSON.stringify(options.color_overrides));
    }
    if (options?.non_interactive !== undefined) {
      formData.append('non_interactive', String(options.non_interactive));
    }
    if (options?.is_private !== undefined) {
      formData.append('is_private', String(options.is_private));
    }
    if (options?.requires_audio_edit) {
      formData.append('requires_audio_edit', String(options.requires_audio_edit));
    }

    const response = await fetch(`${API_BASE_URL}/api/jobs/upload`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: formData,
    });

    return handleResponse<UploadJobResponse>(response);
  },

  /**
   * Create a job and get signed URLs for direct GCS upload.
   * Used for large files that exceed Cloud Run's 32MB body limit.
   */
  async createJobWithUploadUrls(
    artist: string,
    title: string,
    files: Array<{ filename: string; content_type: string; file_type: string }>,
    options?: {
      is_private?: boolean;
      existing_instrumental?: boolean;
      requires_audio_edit?: boolean;
    }
  ): Promise<CreateJobWithUploadUrlsResponse> {
    const body: Record<string, any> = { artist, title, files };
    if (options?.is_private !== undefined) body.is_private = options.is_private;
    if (options?.existing_instrumental !== undefined) body.existing_instrumental = options.existing_instrumental;
    if (options?.requires_audio_edit) body.requires_audio_edit = options.requires_audio_edit;

    const response = await fetch(`${API_BASE_URL}/api/jobs/create-with-upload-urls`, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    return handleResponse<CreateJobWithUploadUrlsResponse>(response);
  },

  /**
   * Upload a file directly to a GCS signed URL.
   * Uses XMLHttpRequest for progress tracking.
   */
  uploadToSignedUrl(
    signedUrl: string,
    file: File,
    contentType: string,
    onProgress?: (loaded: number, total: number) => void,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', signedUrl, true);
      xhr.setRequestHeader('Content-Type', contentType);

      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            onProgress(e.loaded, e.total);
          }
        };
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve();
        } else {
          reject(new ApiError(`Upload failed: ${xhr.statusText}`, xhr.status));
        }
      };

      xhr.onerror = () => reject(new ApiError('Upload failed: network error', 0));
      xhr.send(file);
    });
  },

  /**
   * Mark uploads as complete so the backend starts processing.
   */
  async completeJobUpload(
    jobId: string,
    uploadedFileTypes: string[],
  ): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/uploads-complete`, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ uploaded_files: uploadedFileTypes }),
    });

    return handleResponse<{ status: string; message: string }>(response);
  },

  /**
   * Smart upload: uses direct upload for small files (<25MB),
   * signed URL flow for large files (>=25MB).
   */
  async uploadJobSmart(
    file: File,
    artist: string,
    title: string,
    options?: { is_private?: boolean; requires_audio_edit?: boolean },
    onProgress?: (progress: UploadProgress) => void,
  ): Promise<UploadJobResponse> {
    const SIGNED_URL_THRESHOLD = 25 * 1024 * 1024; // 25MB

    if (file.size < SIGNED_URL_THRESHOLD) {
      // Small file: use existing direct upload
      return this.uploadJob(file, artist, title, options);
    }

    // Large file: use signed URL flow
    // Step 1: Create job and get signed URL
    onProgress?.({ phase: 'creating', loaded: 0, total: file.size });

    const contentType = file.type || 'application/octet-stream';
    const createResponse = await this.createJobWithUploadUrls(
      artist, title,
      [{ filename: file.name, content_type: contentType, file_type: 'audio' }],
      options,
    );

    const audioUrl = createResponse.upload_urls.find(u => u.file_type === 'audio');
    if (!audioUrl) {
      throw new ApiError('No upload URL returned for audio file', 500);
    }

    // Step 2: Upload file directly to GCS
    onProgress?.({ phase: 'uploading', loaded: 0, total: file.size });

    await this.uploadToSignedUrl(
      audioUrl.upload_url,
      file,
      audioUrl.content_type,
      (loaded, total) => onProgress?.({ phase: 'uploading', loaded, total }),
    );

    // Step 3: Notify backend that upload is complete
    onProgress?.({ phase: 'finalizing', loaded: file.size, total: file.size });

    await this.completeJobUpload(createResponse.job_id, ['audio']);

    return {
      status: 'success',
      job_id: createResponse.job_id,
      message: 'Job created via signed URL upload',
    };
  },

  /**
   * Create a job from a URL (YouTube, etc.)
   */
  async createJobFromUrl(
    url: string,
    artist?: string,
    title?: string,
    options?: {
      enable_cdg?: boolean;
      enable_txt?: boolean;
      brand_prefix?: string;
      enable_youtube_upload?: boolean;
      theme_id?: string;
      color_overrides?: ColorOverrides;
      non_interactive?: boolean;
      is_private?: boolean;
      requires_audio_edit?: boolean;
    }
  ): Promise<{ status: string; job_id: string; message: string }> {
    const body: Record<string, any> = { url };
    if (artist) body.artist = artist;
    if (title) body.title = title;
    if (options?.enable_cdg !== undefined) body.enable_cdg = options.enable_cdg;
    if (options?.enable_txt !== undefined) body.enable_txt = options.enable_txt;
    if (options?.brand_prefix) body.brand_prefix = options.brand_prefix;
    if (options?.enable_youtube_upload !== undefined) body.enable_youtube_upload = options.enable_youtube_upload;
    if (options?.theme_id) body.theme_id = options.theme_id;
    if (options?.color_overrides) body.color_overrides = options.color_overrides;
    if (options?.non_interactive !== undefined) body.non_interactive = options.non_interactive;
    if (options?.is_private !== undefined) body.is_private = options.is_private;
    if (options?.requires_audio_edit) body.requires_audio_edit = options.requires_audio_edit;

    const response = await fetch(`${API_BASE_URL}/api/jobs/create-from-url`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify(body),
    });

    return handleResponse(response);
  },
  
  /**
   * Get review data for lyrics review
   */
  async getReviewData(jobId: string): Promise<{
    corrections_url: string;
    audio_url: string;
    status: string;
    artist?: string;
    title?: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/review-data`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },
  
  /**
   * Complete the lyrics review with optional instrumental selection (combined flow)
   */
  async completeReview(jobId: string, instrumentalSelection?: InstrumentalSelectionType): Promise<{ status: string; job_status: string; message: string }> {
    const body = instrumentalSelection ? { instrumental_selection: instrumentalSelection } : undefined;
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/complete-review`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json', ...getAuthHeaders() } : getAuthHeaders(),
      body: body ? JSON.stringify(body) : undefined
    });
    return handleResponse(response);
  },
  
  /**
   * Get instrumental options
   */
  async getInstrumentalOptions(jobId: string): Promise<InstrumentalOptionsResponse> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/instrumental-options`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },
  
  /**
   * Select an instrumental
   */
  async selectInstrumental(
    jobId: string,
    selection: InstrumentalSelectionType
  ): Promise<{ status: string; job_status: string; selection: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/select-instrumental`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ selection }),
    });
    return handleResponse(response);
  },

  /**
   * Submit audio edit (finalize and continue processing).
   * For auto-processor: submits with no edits to skip the audio edit phase.
   */
  /**
   * Get input audio info for the audio editor.
   */
  async getInputAudioInfo(jobId: string): Promise<AudioEditInfo> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/input-audio-info`, {
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    });
    return handleResponse(response);
  },

  /**
   * Apply an audio edit operation (trim, cut, mute, join).
   */
  async applyAudioEdit(jobId: string, operation: string, params: Record<string, unknown>): Promise<AudioEditResponse> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ operation, params }),
    });
    return handleResponse(response);
  },

  /**
   * Undo the last audio edit operation.
   */
  async undoAudioEdit(jobId: string): Promise<AudioEditResponse> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    });
    return handleResponse(response);
  },

  /**
   * Redo a previously undone audio edit.
   */
  async redoAudioEdit(jobId: string): Promise<AudioEditResponse> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit/redo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    });
    return handleResponse(response);
  },

  /**
   * Upload an audio file for join operations.
   */
  async uploadAudioForJoin(jobId: string, file: File): Promise<{ upload_id: string; duration_seconds: number; filename: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit/upload`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: formData,
    });
    return handleResponse(response);
  },

  async submitAudioEdit(jobId: string, editLog?: unknown): Promise<{ status: string; message: string; job_id: string }> {
    const body = editLog ? { edit_log: editLog } : {};
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  /**
   * Save an audio edit session snapshot.
   */
  async saveAudioEditSession(
    jobId: string,
    data: {
      edit_data: unknown;
      edit_count: number;
      trigger: string;
      summary?: AudioEditSessionSummary;
    }
  ): Promise<{ status: string; session_id?: string; reason?: string }> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit-sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(data),
    });
    return handleResponse(response);
  },

  /**
   * List audio edit sessions for a job.
   */
  async listAudioEditSessions(
    jobId: string
  ): Promise<{ sessions: AudioEditSessionMeta[] }> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit-sessions`, {
      headers: getAuthHeaders(),
    });
    return handleResponse(response);
  },

  /**
   * Get a single audio edit session with full edit_data.
   */
  async getAudioEditSession(
    jobId: string,
    sessionId: string
  ): Promise<AudioEditSessionWithData> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/audio-edit-sessions/${sessionId}`, {
      headers: getAuthHeaders(),
    });
    return handleResponse(response);
  },

  /**
   * Get instrumental analysis data for review
   */
  async getInstrumentalAnalysis(jobId: string): Promise<InstrumentalAnalysis> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/instrumental-analysis`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Get waveform data for visualization
   */
  async getWaveformData(jobId: string, numPoints: number = 1000): Promise<WaveformData> {
    const response = await fetch(
      `${API_BASE_URL}/api/jobs/${jobId}/waveform-data?num_points=${numPoints}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Upload a custom instrumental file
   */
  async uploadCustomInstrumental(jobId: string, file: File): Promise<{ status: string; duration_seconds: number; message: string }> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/upload-instrumental`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: formData,
    });
    return handleResponse(response);
  },

  /**
   * Create a custom instrumental with mute regions
   */
  async createCustomInstrumental(jobId: string, muteRegions: MuteRegion[]): Promise<{ status: string; message: string; audio_url?: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/create-custom-instrumental`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ mute_regions: muteRegions }),
    });
    return handleResponse(response);
  },

  /**
   * Get audio stream URL for a specific stem type
   */
  getAudioStreamUrl(jobId: string, stemType: string): string {
    const token = getAccessToken();
    const base = `${API_BASE_URL}/api/jobs/${jobId}/audio-stream/${stemType}`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  },
  
  /**
   * Get download URLs for completed job
   */
  async getDownloadUrls(jobId: string): Promise<DownloadUrlsResponse> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/download-urls`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },
  
  /**
   * Cancel a job
   */
  async cancelJob(jobId: string, reason?: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ reason: reason || 'Cancelled by user' }),
    });
    return handleResponse(response);
  },
  
  /**
   * Delete a job
   */
  async deleteJob(jobId: string, deleteFiles: boolean = true): Promise<{ status: string; message: string }> {
    const response = await fetch(
      `${API_BASE_URL}/api/jobs/${jobId}?delete_files=${deleteFiles}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },
  
  /**
   * Get the download URL for a file, including authentication token
   */
  getDownloadUrl(jobId: string, category: string, fileKey: string): string {
    const baseUrl = `${API_BASE_URL}/api/jobs/${jobId}/download/${category}/${fileKey}`;
    const token = getAccessToken();
    if (token) {
      return `${baseUrl}?token=${encodeURIComponent(token)}`;
    }
    return baseUrl;
  },
  
  /**
   * Search for audio by artist and title
   */
  async searchAudio(
    artist: string,
    title: string,
    autoDownload: boolean = false,
    options?: {
      enable_cdg?: boolean;
      enable_txt?: boolean;
      brand_prefix?: string;
      enable_youtube_upload?: boolean;
      theme_id?: string;
      color_overrides?: ColorOverrides;
      non_interactive?: boolean;
      is_private?: boolean;
      // Display overrides - if provided, these appear on title screens/filenames instead of search values
      display_artist?: string;
      display_title?: string;
    }
  ): Promise<AudioSearchResponse> {
    const body: Record<string, any> = { artist, title, auto_download: autoDownload };
    if (options?.enable_cdg !== undefined) body.enable_cdg = options.enable_cdg;
    if (options?.enable_txt !== undefined) body.enable_txt = options.enable_txt;
    if (options?.brand_prefix) body.brand_prefix = options.brand_prefix;
    if (options?.enable_youtube_upload !== undefined) body.enable_youtube_upload = options.enable_youtube_upload;
    if (options?.theme_id) body.theme_id = options.theme_id;
    if (options?.color_overrides) body.color_overrides = options.color_overrides;
    if (options?.non_interactive !== undefined) body.non_interactive = options.non_interactive;
    if (options?.is_private !== undefined) body.is_private = options.is_private;
    // Display overrides
    if (options?.display_artist) body.display_artist = options.display_artist;
    if (options?.display_title) body.display_title = options.display_title;

    const response = await fetch(`${API_BASE_URL}/api/audio-search/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify(body),
    });

    return handleResponse(response);
  },
  
  /**
   * Search for audio WITHOUT creating a job (guided flow Step 2).
   * Returns a search session ID and results.
   * Use createJobFromSearch() to create the actual job.
   */
  async searchStandalone(
    artist: string,
    title: string
  ): Promise<{ search_session_id: string; results: AudioSearchResult[]; results_count: number }> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(`${API_BASE_URL}/api/audio-search/search-standalone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ artist, title }),
        signal: controller.signal,
      });
      return handleResponse(response);
    } finally {
      clearTimeout(timeout);
    }
  },

  /**
   * Create a job from a standalone search session (guided flow Step 3 confirm).
   * Deducts a credit and starts the download + processing pipeline.
   */
  async createJobFromSearch(params: {
    search_session_id: string;
    selection_index: number;
    artist: string;
    title: string;
    display_artist?: string;
    display_title?: string;
    is_private?: boolean;
    requires_audio_edit?: boolean;
  }): Promise<{ status: string; job_id: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/create-from-search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(params),
    });
    return handleResponse(response);
  },

  /**
   * Get signed upload URLs for style assets on an existing job.
   */
  async getStyleUploadUrls(
    jobId: string,
    files: Array<{ filename: string; content_type: string; file_type: string }>
  ): Promise<{ status: string; job_id: string; upload_urls: SignedUploadUrl[] }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/style-upload-urls`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ files }),
    });
    return handleResponse(response);
  },

  /**
   * Upload a file directly to a GCS signed URL.
   */
  async uploadFileToSignedUrl(url: string, file: File, contentType: string): Promise<void> {
    const response = await fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      body: file,
    });
    if (!response.ok) {
      throw new ApiError(`Upload failed: ${response.statusText}`, response.status);
    }
  },

  /**
   * Finalize style asset uploads on an existing job.
   */
  async completeStyleUploads(
    jobId: string,
    uploadedFiles: string[],
    colorOverrides?: { artist_color?: string; title_color?: string }
  ): Promise<{ status: string; job_id: string; message: string; assets_updated: string[] }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/style-uploads-complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({
        uploaded_files: uploadedFiles,
        color_overrides: colorOverrides,
      }),
    });
    return handleResponse(response);
  },

  /**
   * Get audio search results for a job
   */
  async getAudioSearchResults(jobId: string): Promise<AudioSearchResponse> {
    const response = await fetch(`${API_BASE_URL}/api/audio-search/${jobId}/results`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },
  
  /**
   * Select an audio search result
   */
  async selectAudioResult(
    jobId: string,
    index: number,
    overrides?: { is_private?: boolean; display_artist?: string; display_title?: string }
  ): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/audio-search/${jobId}/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ selection_index: index, ...overrides }),
    });
    return handleResponse(response);
  },
  
  /**
   * Get job logs
   */
  async getJobLogs(jobId: string, limit: number = 100): Promise<JobLog[]> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/logs?limit=${limit}`, {
      headers: getAuthHeaders()
    });
    const data = await handleResponse<{ logs: JobLog[] }>(response);
    return data.logs || [];
  },
  
  /**
   * Retry a failed job
   */
  async retryJob(jobId: string): Promise<{ status: string; job_id: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/retry`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  // ==========================================================================
  // Theme API endpoints
  // ==========================================================================

  /**
   * List all available video themes
   */
  async listThemes(): Promise<VideoThemeSummary[]> {
    const response = await fetch(`${API_BASE_URL}/api/themes`, {
      headers: getAuthHeaders()
    });
    const data = await handleResponse<ThemesListResponse>(response);
    return data.themes;
  },

  /**
   * Get full details for a specific theme
   */
  async getTheme(themeId: string): Promise<VideoThemeDetail> {
    const response = await fetch(`${API_BASE_URL}/api/themes/${themeId}`, {
      headers: getAuthHeaders()
    });
    const data = await handleResponse<ThemeDetailResponse>(response);
    return data.theme;
  },

  /**
   * Get preview URL for a theme
   */
  async getThemePreview(themeId: string): Promise<string | null> {
    const response = await fetch(`${API_BASE_URL}/api/themes/${themeId}/preview`, {
      headers: getAuthHeaders()
    });
    const data = await handleResponse<{ preview_url: string | null }>(response);
    return data.preview_url;
  },

  /**
   * Get YouTube description template for a theme
   */
  async getThemeYoutubeDescription(themeId: string): Promise<string | null> {
    const response = await fetch(`${API_BASE_URL}/api/themes/${themeId}/youtube-description`, {
      headers: getAuthHeaders()
    });
    const data = await handleResponse<{ description: string | null }>(response);
    return data.description;
  },

  // ==========================================================================
  // Auth API endpoints (Magic Link)
  // ==========================================================================

  /**
   * Send a magic link email for passwordless login
   */
  async sendMagicLink(email: string, deviceFingerprint?: string | null): Promise<MagicLinkResponse> {
    const body: Record<string, string> = { email }
    if (deviceFingerprint) {
      body.device_fingerprint = deviceFingerprint
    }
    const response = await fetch(`${API_BASE_URL}/api/users/auth/magic-link`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    return handleResponse(response);
  },

  /**
   * Verify a magic link token and get a session
   */
  async verifyMagicLink(token: string): Promise<VerifyMagicLinkResponse> {
    const response = await fetch(`${API_BASE_URL}/api/users/auth/verify?token=${encodeURIComponent(token)}`, {
      method: 'GET',
    });
    return handleResponse(response);
  },

  /**
   * Get the current user's profile
   */
  async getCurrentUser(): Promise<UserProfileResponse> {
    const response = await fetch(`${API_BASE_URL}/api/users/me`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Logout and invalidate the current session
   */
  async logout(): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/users/auth/logout`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  // ==========================================================================
  // Feedback-for-Credits API endpoints
  // ==========================================================================

  /**
   * Submit product feedback to earn free credits
   */
  async submitFeedback(data: FeedbackRequest): Promise<FeedbackResponse> {
    const response = await fetch(`${API_BASE_URL}/api/users/feedback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify(data),
    });
    return handleResponse(response);
  },

  // ==========================================================================
  // Version & Health API endpoints
  // ==========================================================================

  /**
   * Get backend service info including version
   * Note: Uses /backend-info in dev (proxied to production /) to avoid proxying all routes
   */
  async getBackendInfo(): Promise<{ service: string; version: string; status: string }> {
    const url = API_BASE_URL ? `${API_BASE_URL}/` : '/backend-info';
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Get encoding worker health status
   */
  async getEncodingWorkerHealth(): Promise<EncodingWorkerHealth> {
    const response = await fetch(`${API_BASE_URL}/api/health/encoding-worker`);
    return handleResponse(response);
  },

  /**
   * Get flacfetch service health status
   */
  async getFlacfetchHealth(): Promise<FlacfetchHealth> {
    const response = await fetch(`${API_BASE_URL}/api/health/flacfetch`);
    return handleResponse(response);
  },

  /**
   * Get audio separator service health status
   */
  async getAudioSeparatorHealth(): Promise<AudioSeparatorHealth> {
    const response = await fetch(`${API_BASE_URL}/api/health/audio-separator`);
    return handleResponse(response);
  },

  /**
   * Get aggregated system status for all services
   */
  async getSystemStatus(): Promise<SystemStatus> {
    const response = await fetch(`${API_BASE_URL}/api/health/system-status`, {
      headers: getAuthHeaders(),
    });
    return handleResponse(response);
  },

  // ==========================================================================
  // Credits/Payment API endpoints
  // ==========================================================================

  /**
   * Get available credit packages
   */
  async getCreditPackages(): Promise<CreditPackage[]> {
    const response = await fetch(`${API_BASE_URL}/api/users/credits/packages`);
    const data = await handleResponse<{ packages: CreditPackage[] }>(response);
    return data.packages;
  },

  /**
   * Create a Stripe checkout session
   */
  async createCheckout(packageId: string, email: string): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/api/users/credits/checkout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        package_id: packageId,
        email: email.toLowerCase(),
      }),
    });
    const data = await handleResponse<{ checkout_url: string }>(response);
    return data.checkout_url;
  },

  /**
   * Create a Made For You checkout session ($15 full-service)
   */
  async createMadeForYouCheckout(data: {
    email: string;
    artist: string;
    title: string;
    source_type: 'search' | 'youtube' | 'upload';
    youtube_url?: string;
    notes?: string;
  }): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/api/users/made-for-you/checkout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: data.email.toLowerCase(),
        artist: data.artist,
        title: data.title,
        source_type: data.source_type,
        youtube_url: data.youtube_url,
        notes: data.notes,
      }),
    });
    const result = await handleResponse<{ checkout_url: string; status: string; message: string }>(response);
    if (!result.checkout_url) {
      throw new Error('No checkout URL received');
    }
    return result.checkout_url;
  },

  // ==========================================================================
  // Push Notifications API endpoints
  // ==========================================================================

  /**
   * Get VAPID public key for push subscription
   * Returns whether push is enabled and the public key if so
   */
  async getVapidPublicKey(): Promise<{ enabled: boolean; vapid_public_key: string | null }> {
    const response = await fetch(`${API_BASE_URL}/api/push/vapid-public-key`);
    return handleResponse(response);
  },

  /**
   * Subscribe to push notifications
   */
  async subscribePush(
    endpoint: string,
    keys: { p256dh: string; auth: string },
    deviceName?: string,
    tenantId?: string | null
  ): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/push/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({
        endpoint,
        keys,
        device_name: deviceName,
        tenant_id: tenantId || null,
      }),
    });
    return handleResponse(response);
  },

  /**
   * Unsubscribe from push notifications
   */
  async unsubscribePush(endpoint: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/push/unsubscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ endpoint }),
    });
    return handleResponse(response);
  },

  /**
   * List user's push notification subscriptions
   */
  async listPushSubscriptions(): Promise<PushSubscriptionsListResponse> {
    const response = await fetch(`${API_BASE_URL}/api/push/subscriptions`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  // --- Catalog (song/artist autocomplete + community check) ---

  async searchCatalogArtists(query: string, limit: number = 10): Promise<CatalogArtistResult[]> {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    const response = await fetch(`${API_BASE_URL}/api/catalog/artists?${params}`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  async searchCatalogTracks(query: string, artist?: string, limit: number = 10): Promise<CatalogTrackResult[]> {
    const params = new URLSearchParams({ q: query, limit: String(limit) });
    if (artist) params.set('artist', artist);
    const response = await fetch(`${API_BASE_URL}/api/catalog/tracks?${params}`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  async checkCommunityVersions(artist: string, title: string): Promise<CommunityCheckResponse> {
    const response = await fetch(`${API_BASE_URL}/api/catalog/community-check`, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ artist, title }),
    });
    return handleResponse(response);
  },

  async changeVisibility(jobId: string, targetVisibility: 'public' | 'private'): Promise<ChangeVisibilityResponse> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/change-visibility`, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ target_visibility: targetVisibility }),
    });
    return handleResponse(response);
  },

  async editCompletedTrack(jobId: string, updates?: { artist?: string; title?: string }): Promise<EditTrackResponse> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/edit`, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updates || {}),
    });
    return handleResponse(response);
  },
};

// Types for credits/payment
export interface CreditPackage {
  id: string;
  credits: number;
  price_cents: number;
  name: string;
  description: string;
}

// Types for push notifications
export interface PushSubscriptionInfo {
  endpoint: string;
  device_name: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface PushSubscriptionsListResponse {
  subscriptions: PushSubscriptionInfo[];
  count: number;
}

// ==========================================================================
// Admin Types
// ==========================================================================

export interface AdminStatsOverview {
  total_users: number;
  active_users_7d: number;
  active_users_30d: number;
  total_jobs: number;
  jobs_last_7d: number;
  jobs_last_30d: number;
  jobs_by_status: {
    pending: number;
    processing: number;
    awaiting_review: number;
    awaiting_instrumental: number;
    complete: number;
    failed: number;
    cancelled: number;
  };
  total_credits_issued_30d: number;
}

export interface AdminUser {
  email: string;
  role: 'user' | 'admin';
  credits: number;
  display_name?: string;
  total_jobs_created?: number;
  total_jobs_completed?: number;
  total_spent?: number;
  created_at?: string;
  last_login_at?: string;
}

export interface AdminUserListResponse {
  users: AdminUser[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface AdminUserDetail {
  email: string;
  role: 'user' | 'admin';
  credits: number;
  display_name?: string;
  is_active: boolean;
  email_verified: boolean;
  created_at?: string;
  updated_at?: string;
  last_login_at?: string;
  total_jobs_created: number;
  total_jobs_completed: number;
  credit_transactions: Array<{
    id: string;
    amount: number;
    reason: string;
    created_at: string;
    job_id?: string;
    created_by?: string;
  }>;
  total_spent: number;
  recent_jobs: Array<{
    job_id: string;
    status: string;
    artist?: string;
    title?: string;
    created_at?: string;
  }>;
  active_sessions_count: number;
  // Anti-abuse fields
  signup_ip?: string | null;
  device_fingerprint?: string | null;
  welcome_credits_granted?: boolean;
  has_submitted_feedback?: boolean;
  recent_sessions?: Array<{
    ip_address?: string;
    user_agent?: string;
    device_fingerprint?: string;
    created_at?: string;
    last_activity_at?: string;
  }>;
}

export interface AdminJobListParams {
  status?: string;
  user_email?: string;
  environment?: string;
  created_after?: string;
  created_before?: string;
  limit?: number;
}

// Audio Search Admin Types
export interface AudioSearchResultSummary {
  index: number;
  provider: string;
  artist: string;
  title: string;
  is_lossless: boolean;
  quality?: string;
  seeders?: number;
}

export interface AudioSearchJobSummary {
  job_id: string;
  status: string;
  user_email?: string;
  audio_search_artist?: string;
  audio_search_title?: string;
  created_at?: string;
  results_count: number;
  results_summary: AudioSearchResultSummary[];
  has_lossless: boolean;
  providers: string[];
}

export interface AudioSearchListResponse {
  jobs: AudioSearchJobSummary[];
  total: number;
}

export interface ClearSearchCacheResponse {
  status: string;
  job_id: string;
  message: string;
  previous_status: string;
  new_status: string;
  results_cleared: number;
  flacfetch_cache_cleared: boolean;
  flacfetch_error?: string;
}

export interface ClearAllCacheResponse {
  status: string;
  message: string;
  deleted_count: number;
}

export interface CacheStatsResponse {
  count: number;
  total_size_bytes: number;
  oldest_entry?: string;
  newest_entry?: string;
  configured: boolean;
}

// Admin API namespace
export const adminApi = {
  /**
   * Get admin dashboard statistics
   */
  async getStats(params?: { exclude_test?: boolean }): Promise<AdminStatsOverview> {
    const searchParams = new URLSearchParams();
    if (params?.exclude_test !== undefined) {
      searchParams.set('exclude_test', String(params.exclude_test));
    }
    const url = `${API_BASE_URL}/api/admin/stats/overview${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * List all users with pagination and search
   */
  async listUsers(params?: {
    limit?: number;
    offset?: number;
    search?: string;
    sort_by?: 'created_at' | 'last_login_at' | 'credits' | 'email';
    sort_order?: 'asc' | 'desc';
    include_inactive?: boolean;
    exclude_test?: boolean;
  }): Promise<AdminUserListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.search) searchParams.set('search', params.search);
    if (params?.sort_by) searchParams.set('sort_by', params.sort_by);
    if (params?.sort_order) searchParams.set('sort_order', params.sort_order);
    if (params?.include_inactive) searchParams.set('include_inactive', 'true');
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));

    const url = `${API_BASE_URL}/api/users/admin/users${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Get detailed user information
   */
  async getUserDetail(email: string): Promise<AdminUserDetail> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/users/${encodeURIComponent(email)}/detail`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Add credits to a user
   */
  async addCredits(email: string, amount: number, reason: string): Promise<{
    status: string;
    email: string;
    credits_added: number;
    new_balance: number;
    message: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/credits`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({ email, amount, reason }),
    });
    return handleResponse(response);
  },

  /**
   * Impersonate a user (admin only)
   * Creates a session token that allows viewing the app as the target user
   */
  async impersonateUser(email: string): Promise<{
    session_token: string;
    user_email: string;
    message: string;
  }> {
    const response = await fetch(`${API_BASE_URL}/api/admin/users/${encodeURIComponent(email)}/impersonate`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Enable a user account
   */
  async enableUser(email: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/users/${encodeURIComponent(email)}/enable`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Disable a user account
   */
  async disableUser(email: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/users/${encodeURIComponent(email)}/disable`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Permanently delete a user and all associated data
   */
  async deleteUser(email: string): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/users/${encodeURIComponent(email)}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * List all jobs (admin view)
   */
  async listAllJobs(params?: AdminJobListParams & { exclude_test?: boolean }): Promise<Job[]> {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.user_email) searchParams.set('user_email', params.user_email);
    if (params?.environment) searchParams.set('environment', params.environment);
    if (params?.created_after) searchParams.set('created_after', params.created_after);
    if (params?.created_before) searchParams.set('created_before', params.created_before);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));

    const url = `${API_BASE_URL}/api/jobs${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse<Job[]>(response);
  },

  /**
   * Delete a job (admin)
   */
  async deleteJob(jobId: string, deleteFiles: boolean = true): Promise<{ status: string; message: string }> {
    const response = await fetch(
      `${API_BASE_URL}/api/jobs/${jobId}?delete_files=${deleteFiles}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * List jobs with audio search results
   */
  async listAudioSearches(params?: {
    limit?: number;
    status_filter?: string;
    exclude_test?: boolean;
  }): Promise<AudioSearchListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.status_filter) searchParams.set('status_filter', params.status_filter);
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));

    const url = `${API_BASE_URL}/api/admin/audio-searches${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Clear audio search cache for a job
   */
  async clearAudioSearchCache(jobId: string): Promise<ClearSearchCacheResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/audio-searches/${jobId}/clear-cache`,
      { method: 'POST', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Clear all flacfetch search cache
   */
  async clearAllCache(): Promise<ClearAllCacheResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/cache`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Get flacfetch cache statistics
   */
  async getCacheStats(): Promise<CacheStatsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/cache/stats`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Get rendered completion message for a job (for copy to clipboard)
   */
  async getCompletionMessage(jobId: string): Promise<CompletionMessageResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/completion-message`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Send completion email for a job
   */
  async sendCompletionEmail(jobId: string, toEmail: string, ccAdmin: boolean = true): Promise<SendCompletionEmailResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/send-completion-email`,
      {
        method: 'POST',
        headers: {
          ...getAuthHeaders(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ to_email: toEmail, cc_admin: ccAdmin }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Get all files for a job with signed download URLs
   */
  async getJobFiles(jobId: string): Promise<JobFilesResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/files`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Update editable fields of a job (admin only)
   */
  async updateJob(jobId: string, updates: JobUpdateRequest): Promise<JobUpdateResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}`,
      {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(updates),
      }
    );
    return handleResponse(response);
  },

  /**
   * Reset a job to a specific state for re-processing (admin only)
   */
  async resetJob(jobId: string, targetState: string): Promise<JobResetResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/reset`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ target_state: targetState }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Delete all distributed outputs for a job (admin only).
   * Deletes YouTube video, Dropbox folder, and Google Drive files.
   * Job record is preserved with outputs_deleted_at timestamp.
   */
  async deleteJobOutputs(jobId: string): Promise<DeleteOutputsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/delete-outputs`,
      {
        method: 'POST',
        headers: getAuthHeaders()
      }
    );
    return handleResponse(response);
  },

  /**
   * Clear all worker progress markers for a job (admin only).
   * This allows workers to re-execute without skipping due to idempotency checks.
   * Does NOT change job status - use resetJob for that.
   */
  async clearWorkers(jobId: string): Promise<ClearWorkersResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/clear-workers`,
      {
        method: 'POST',
        headers: getAuthHeaders()
      }
    );
    return handleResponse(response);
  },

  /**
   * Manually trigger a worker for a job (admin only).
   * Use when auto-trigger fails after reset, or to re-run processing.
   */
  async triggerWorker(jobId: string, workerType: string = "video"): Promise<TriggerWorkerResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/trigger-worker`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ worker_type: workerType }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Regenerate title and end screens with current artist/title metadata (admin only).
   * Use when you've edited artist/title and need screens to reflect the new metadata.
   */
  async regenerateScreens(jobId: string): Promise<RegenerateScreensResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/regenerate-screens`,
      {
        method: 'POST',
        headers: getAuthHeaders()
      }
    );
    return handleResponse(response);
  },

  /**
   * Fully restart a job from the beginning (admin only).
   * Unlike reset (which just changes state), restart actually triggers workers.
   */
  async restartJob(jobId: string, options: RestartJobRequest): Promise<RestartJobResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/restart`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(options),
      }
    );
    return handleResponse(response);
  },

  /**
   * Override the audio source for a job (admin only).
   * Switch from YouTube URL to audio search mode.
   */
  async overrideAudioSource(jobId: string, request: OverrideAudioSourceRequest): Promise<OverrideAudioSourceResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/jobs/${jobId}/override-audio-source`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(request),
      }
    );
    return handleResponse(response);
  },

  // =========================================================================
  // Rate Limits API
  // =========================================================================

  /**
   * Get all blocklists
   */
  async getBlocklists(): Promise<BlocklistsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Add a disposable domain
   */
  async addDisposableDomain(domain: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/disposable-domains`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ domain }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Remove a disposable domain
   */
  async removeDisposableDomain(domain: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/disposable-domains/${encodeURIComponent(domain)}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Add a blocked email
   */
  async addBlockedEmail(email: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/blocked-emails`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ email }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Remove a blocked email
   */
  async removeBlockedEmail(email: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/blocked-emails/${encodeURIComponent(email)}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Add a blocked IP
   */
  async addBlockedIP(ipAddress: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/blocked-ips`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ ip_address: ipAddress }),
      }
    );
    return handleResponse(response);
  },

  /**
   * Remove a blocked IP
   */
  async removeBlockedIP(ipAddress: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/blocked-ips/${encodeURIComponent(ipAddress)}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async syncDisposableDomains(): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/sync`,
      { method: 'POST', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async addAllowlistedDomain(domain: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/allowlisted-domains`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ domain }),
      }
    );
    return handleResponse(response);
  },

  async removeAllowlistedDomain(domain: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/blocklists/allowlisted-domains/${encodeURIComponent(domain)}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  // YouTube Upload Queue

  async getYouTubeQueue(): Promise<YouTubeQueueListResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/youtube-queue`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async retryYouTubeUpload(jobId: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/youtube-queue/${encodeURIComponent(jobId)}/retry`,
      { method: 'POST', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async processYouTubeQueue(): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/youtube-queue/process`,
      { method: 'POST', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  // =========================================================================
  // Payments API
  // =========================================================================

  async getPaymentSummary(params?: { days?: number; exclude_test?: boolean }): Promise<RevenueSummary> {
    const searchParams = new URLSearchParams();
    if (params?.days) searchParams.set('days', String(params.days));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    const url = `${API_BASE_URL}/api/admin/payments/summary${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async getRevenueChart(params?: { days?: number; group_by?: string; exclude_test?: boolean }): Promise<RevenueChartPoint[]> {
    const searchParams = new URLSearchParams();
    if (params?.days) searchParams.set('days', String(params.days));
    if (params?.group_by) searchParams.set('group_by', params.group_by);
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    const url = `${API_BASE_URL}/api/admin/payments/revenue-chart${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async listPayments(params?: {
    limit?: number;
    offset?: number;
    order_type?: string;
    status?: string;
    email?: string;
    exclude_test?: boolean;
  }): Promise<PaymentListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.order_type) searchParams.set('order_type', params.order_type);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.email) searchParams.set('email', params.email);
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    const url = `${API_BASE_URL}/api/admin/payments${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async getPaymentDetail(sessionId: string): Promise<PaymentRecord> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/${encodeURIComponent(sessionId)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getStripeBalance(): Promise<StripeBalance> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/balance`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getPayouts(limit: number = 20): Promise<PayoutRecord[]> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/payouts?limit=${limit}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getDisputes(): Promise<DisputeRecord[]> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/disputes`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getUserPayments(email: string): Promise<UserPaymentHistory> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/by-user/${encodeURIComponent(email)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getWebhookEvents(params?: { limit?: number; event_type?: string; status?: string }): Promise<WebhookEvent[]> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.event_type) searchParams.set('event_type', params.event_type);
    if (params?.status) searchParams.set('status', params.status);
    const url = `${API_BASE_URL}/api/admin/payments/webhook-events${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async refundPayment(sessionId: string, request: RefundRequest): Promise<RefundResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/payments/${encodeURIComponent(sessionId)}/refund`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(request),
      }
    );
    return handleResponse(response);
  },

  async listEditReviews(params?: {
    limit?: number;
    offset?: number;
    exclude_test?: boolean;
    search?: string;
  }): Promise<EditReviewListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    if (params?.search) searchParams.set('search', params.search);
    const url = `${API_BASE_URL}/api/admin/edit-reviews${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async getEditReview(jobId: string): Promise<EditReviewDetail> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/edit-reviews/${encodeURIComponent(jobId)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async listAudioEditReviews(params?: {
    limit?: number;
    offset?: number;
    exclude_test?: boolean;
    search?: string;
  }): Promise<AudioEditReviewListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));
    if (params?.search) searchParams.set('search', params.search);
    const url = `${API_BASE_URL}/api/admin/audio-edit-reviews${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async getAudioEditReview(jobId: string): Promise<AudioEditReviewDetail> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/audio-edit-reviews/${encodeURIComponent(jobId)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  // Anti-abuse investigation endpoints
  async getAbuseCorrelations(): Promise<AbuseCorrelationsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/correlations`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getAbuseSuspicious(params?: { min_jobs?: number; max_spend?: number }): Promise<AbuseSuspiciousResponse> {
    const searchParams = new URLSearchParams();
    if (params?.min_jobs) searchParams.set('min_jobs', String(params.min_jobs));
    if (params?.max_spend !== undefined) searchParams.set('max_spend', String(params.max_spend));
    const url = `${API_BASE_URL}/api/admin/abuse/suspicious${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, { headers: getAuthHeaders() });
    return handleResponse(response);
  },

  async getAbuseRelated(email: string): Promise<AbuseRelatedResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/related/${encodeURIComponent(email)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getAbuseByIp(ip: string): Promise<AbuseByIpResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/by-ip/${encodeURIComponent(ip)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getAbuseByFingerprint(fingerprint: string): Promise<AbuseByFingerprintResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/by-fingerprint/${encodeURIComponent(fingerprint)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getIpInfo(ip: string): Promise<IpGeoInfo> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/ip-info/${encodeURIComponent(ip)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  async getIpInfoBatch(ips: string[]): Promise<Record<string, IpGeoInfo>> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/abuse/ip-info/batch`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ ips }),
      }
    );
    return handleResponse(response);
  },

  async listFeedback(params?: {
    limit?: number;
    offset?: number;
    search?: string;
    exclude_test?: boolean;
  }): Promise<AdminFeedbackListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.search) searchParams.set('search', params.search);
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));

    const url = `${API_BASE_URL}/api/admin/feedback${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },
};

export interface EditReviewSummary {
  job_id: string;
  artist: string;
  title: string;
  user_email: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  edit_log_session?: string;
  edit_log_path?: string;
  has_corrections_updated: boolean;
}

export interface EditReviewListResponse {
  reviews: EditReviewSummary[];
  total: number;
  has_more: boolean;
}

export interface AdminEditLogEntry {
  id: string;
  timestamp: string;
  operation: string;
  segment_id?: string;
  segment_index?: number;
  word_ids_before?: string[];
  word_ids_after?: string[];
  text_before: string;
  text_after: string;
  details?: Record<string, unknown>;
  feedback?: {
    reason: string;
    timestamp: string;
  };
}

export interface AdminEditLog {
  session_id: string;
  job_id: string;
  audio_hash?: string;
  started_at?: string;
  entries: AdminEditLogEntry[];
}

export interface EditReviewDetail {
  job: {
    job_id: string;
    artist: string;
    title: string;
    user_email: string;
    status: string;
    created_at?: string;
    updated_at?: string;
  };
  original_corrections: Record<string, unknown> | null;
  updated_corrections: Record<string, unknown> | null;
  edit_log: AdminEditLog | null;
  annotations: Record<string, unknown> | null;
  audio_url: string | null;
}

// Types for admin audio edit reviews
export interface AudioEditReviewSummary {
  job_id: string;
  artist: string;
  title: string;
  user_email: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  session_count: number;
  total_edits: number;
  original_duration?: number;
  current_duration?: number;
  latest_trigger?: string;
}

export interface AudioEditReviewListResponse {
  reviews: AudioEditReviewSummary[];
  total: number;
  has_more: boolean;
}

export interface AudioEditReviewDetail {
  job: {
    job_id: string;
    artist: string;
    title: string;
    user_email: string;
    status: string;
    created_at?: string;
    updated_at?: string;
  };
  sessions: Array<{
    session_id: string;
    job_id: string;
    user_email: string;
    created_at: string;
    updated_at: string;
    edit_count: number;
    trigger: string;
    audio_duration_seconds?: number;
    original_duration_seconds?: number;
    artist?: string;
    title?: string;
    summary: AudioEditSessionSummary;
    edit_data_gcs_path: string;
    data_hash: string;
  }>;
  edit_stack: Array<{
    edit_id: string;
    operation: string;
    params: Record<string, unknown>;
    gcs_path: string;
    duration_before?: number;
    duration_after?: number;
    timestamp: string;
  }>;
  edit_log: unknown;
  original_audio_url: string | null;
  current_audio_url: string | null;
}

// Types for admin completion message API
export interface CompletionMessageResponse {
  job_id: string;
  message: string;
  subject: string;
  youtube_url?: string;
  dropbox_url?: string;
}

export interface SendCompletionEmailResponse {
  success: boolean;
  job_id: string;
  to_email: string;
  message: string;
}

// Types for admin job files API
export interface FileInfo {
  name: string;
  path: string;
  download_url: string;
  category: string;
  file_key: string;
}

export interface JobFilesResponse {
  job_id: string;
  artist?: string;
  title?: string;
  files: FileInfo[];
  total_files: number;
}

export interface JobUpdateRequest {
  artist?: string;
  title?: string;
  user_email?: string;
  theme_id?: string;
  brand_prefix?: string;
  discord_webhook_url?: string;
  youtube_description?: string;
  youtube_description_template?: string;
  customer_email?: string;
  customer_notes?: string;
  enable_cdg?: boolean;
  enable_txt?: boolean;
  enable_youtube_upload?: boolean;
  non_interactive?: boolean;
  prep_only?: boolean;
  is_private?: boolean;
}

export interface JobUpdateResponse {
  status: string;
  job_id: string;
  updated_fields: string[];
  message: string;
}

export interface JobResetResponse {
  status: string;
  job_id: string;
  previous_status: string;
  new_status: string;
  message: string;
  cleared_data: string[];
  worker_triggered?: boolean;  // Was worker auto-triggered? (only for instrumental_selected)
  worker_trigger_error?: string;  // Error message if trigger failed
}

export interface TriggerWorkerResponse {
  status: string;
  job_id: string;
  worker_type: string;
  triggered: boolean;
  message: string;
  error?: string;
}

export interface ClearWorkersResponse {
  status: string;
  job_id: string;
  message: string;
  cleared_keys: string[];
}

export interface RegenerateScreensResponse {
  status: string;
  job_id: string;
  message: string;
  previous_screens_deleted: boolean;
  worker_triggered: boolean;
  error?: string;
}

export interface RestartJobRequest {
  preserve_audio_stems: boolean;
  delete_outputs: boolean;
}

export interface RestartJobResponse {
  status: string;
  job_id: string;
  message: string;
  previous_status: string;
  new_status: string;
  cleared_data: string[];
  deleted_gcs_paths: string[];
  workers_triggered: string[];
  error?: string;
}

export interface OverrideAudioSourceRequest {
  source_type: "audio_search";
}

export interface OverrideAudioSourceResponse {
  status: string;
  job_id: string;
  message: string;
  previous_source: string;
  new_source: string;
  cleared_data: string[];
  new_status: string;
}

// ==========================================================================
// Lyrics Review API endpoints
// ==========================================================================

/**
 * API client interface for LyricsAnalyzer component
 */
// Review Session types (backup/restore)
export interface ReviewSessionSummary {
  total_segments: number
  total_words: number
  corrections_made: number
  changed_words: Array<{ original: string; corrected: string; segment_index: number }>
}

export interface ReviewSession {
  session_id: string
  job_id: string
  user_email: string
  created_at: string | null
  updated_at: string | null
  edit_count: number
  trigger: 'auto' | 'preview' | 'manual'
  audio_duration_seconds: number | null
  artist: string | null
  title: string | null
  summary: ReviewSessionSummary
}

export interface ReviewSessionWithData extends ReviewSession {
  correction_data: CorrectionData | null
}

export interface LyricsReviewApiClient {
  submitCorrections: (data: CorrectionData) => Promise<void>
  submitAnnotations: (annotations: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>[]) => Promise<void>
  submitEditLog: (editLog: EditLog) => Promise<void>
  updateHandlers: (handlers: string[]) => Promise<CorrectionData>
  addLyrics: (source: string, lyrics: string) => Promise<CorrectionData>
  searchLyrics: (artist: string, title: string, forceSources?: string[]) => Promise<SearchLyricsResponse>
  getAudioUrl: (hash: string) => string
  generatePreviewVideo: (data: CorrectionData) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
  completeReview: () => Promise<{ status: string; job_status: string; message: string }>
  // Review session methods
  saveReviewSession: (data: CorrectionData, editCount: number, trigger: string, summary: ReviewSessionSummary) => Promise<{ status: string; session_id?: string; reason?: string }>
  listReviewSessions: () => Promise<{ sessions: ReviewSession[] }>
  getReviewSession: (sessionId: string) => Promise<ReviewSessionWithData>
  deleteReviewSession: (sessionId: string) => Promise<{ status: string }>
}

/**
 * Create a lyrics review API client for a specific job
 */
export function createLyricsReviewApiClient(jobId: string): LyricsReviewApiClient {
  return {
    /**
     * Submit corrected lyrics data
     *
     * Backend expects: { corrections: { lines: [...], metadata: {...}, ...rest } }
     * - 'lines' and 'metadata' are required by Pydantic validator
     * - 'corrected_segments' and 'corrections' array are needed by render worker
     */
    async submitCorrections(data: CorrectionData): Promise<void> {
      // Wrap CorrectionData in the structure expected by CorrectionsSubmission
      // - 'lines' is an alias for corrected_segments (required by validator)
      // - Include full data for render worker compatibility
      const payload = {
        corrections: {
          ...data,
          lines: data.corrected_segments,
        }
      }
      const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/corrections`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(payload),
      })
      await handleResponse<{ status: string }>(response)
    },

    /**
     * Submit human annotations/feedback on corrections
     */
    async submitAnnotations(annotations: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>[]): Promise<void> {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/v1/annotations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ annotations }),
      })
      await handleResponse<{ status: string }>(response)
    },

    async submitEditLog(editLog: EditLog): Promise<void> {
      const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/edit-log`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(editLog),
      })
      await handleResponse<{ status: string }>(response)
    },

    /**
     * Update enabled correction handlers and get recalculated corrections
     */
    async updateHandlers(handlers: string[]): Promise<CorrectionData> {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/handlers`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ enabled_handlers: handlers }),
      })
      return handleResponse<CorrectionData>(response)
    },

    /**
     * Add lyrics from a new source
     */
    async addLyrics(source: string, lyrics: string): Promise<CorrectionData> {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/add-lyrics`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({ source, lyrics }),
      })
      // Backend returns { status: "success", data: CorrectionData }
      const result = await handleResponse<{ status: string; data: CorrectionData }>(response)
      return result.data
    },

    /**
     * Search for lyrics from configured providers
     */
    async searchLyrics(artist: string, title: string, forceSources: string[] = []): Promise<SearchLyricsResponse> {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/search-lyrics`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({
          artist,
          title,
          force_sources: forceSources,
        }),
      })
      return handleResponse<SearchLyricsResponse>(response)
    },

    /**
     * Get audio URL for playback
     */
    getAudioUrl(hash: string): string {
      const token = getAccessToken()
      const base = `${API_BASE_URL}/api/review/${jobId}/audio/${hash}`
      return token ? `${base}?token=${encodeURIComponent(token)}` : base
    },

    /**
     * Generate a preview video from the current correction data
     */
    async generatePreviewVideo(data: CorrectionData): Promise<{
      status: string
      message?: string
      preview_hash?: string
    }> {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/preview-video`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(data),
      })
      return handleResponse(response)
    },

    /**
     * Get preview video URL for playback
     */
    getPreviewVideoUrl(hash: string): string {
      const token = getAccessToken()
      const base = `${API_BASE_URL}/api/review/${jobId}/preview-video/${hash}`
      return token ? `${base}?token=${encodeURIComponent(token)}` : base
    },

    /**
     * Complete the review and trigger video rendering
     */
    async completeReview(): Promise<{ status: string; job_status: string; message: string }> {
      const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/complete-review`, {
        method: 'POST',
        headers: getAuthHeaders()
      })
      return handleResponse(response)
    },

    // Review session methods
    async saveReviewSession(data: CorrectionData, editCount: number, trigger: string, summary: ReviewSessionSummary) {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify({
          correction_data: data,
          edit_count: editCount,
          trigger,
          summary,
        }),
      })
      return handleResponse<{ status: string; session_id?: string; reason?: string }>(response)
    },

    async listReviewSessions() {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/sessions`, {
        headers: getAuthHeaders()
      })
      return handleResponse<{ sessions: ReviewSession[] }>(response)
    },

    async getReviewSession(sessionId: string) {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/sessions/${sessionId}`, {
        headers: getAuthHeaders()
      })
      return handleResponse<ReviewSessionWithData>(response)
    },

    async deleteReviewSession(sessionId: string) {
      const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/sessions/${sessionId}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      })
      return handleResponse<{ status: string }>(response)
    },
  }
}

// Standalone lyrics review API functions (for use without jobId context)
export const lyricsReviewApi = {
  /**
   * Get correction data for a job
   */
  async getCorrectionData(jobId: string): Promise<CorrectionData> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/correction-data`, {
      headers: getAuthHeaders()
    })
    return handleResponse<CorrectionData>(response)
  },

  /**
   * Get audio URL for a job by hash
   */
  getAudioUrl(jobId: string, hash: string): string {
    const token = getAccessToken()
    const base = `${API_BASE_URL}/api/review/${jobId}/audio/${hash}`
    return token ? `${base}?token=${encodeURIComponent(token)}` : base
  },

  /**
   * Complete the review with corrections and instrumental selection
   * This is the final submission endpoint for the combined review flow
   */
  async completeReview(
    jobId: string,
    correctionData: CorrectionData,
    instrumentalSelection: InstrumentalSelectionType
  ): Promise<{ status: string; instrumental_selection: string }> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/complete`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      },
      body: JSON.stringify({
        ...correctionData,
        instrumental_selection: instrumentalSelection
      })
    })
    return handleResponse(response)
  },

  /**
   * Get instrumental analysis data for review
   * Uses the review API endpoint (for cloud mode)
   */
  async getInstrumentalAnalysis(jobId: string): Promise<InstrumentalAnalysis> {
    const response = await fetch(`${API_BASE_URL}/api/review/${jobId}/instrumental-analysis`, {
      headers: getAuthHeaders()
    })
    return handleResponse(response)
  },

  /**
   * Get waveform data for visualization
   * Uses the review API endpoint (for cloud mode)
   */
  async getWaveformData(jobId: string, numPoints: number = 1000): Promise<WaveformData> {
    const response = await fetch(
      `${API_BASE_URL}/api/review/${jobId}/waveform-data?num_points=${numPoints}`,
      { headers: getAuthHeaders() }
    )
    return handleResponse(response)
  },

  /**
   * Get audio stream URL for stem playback
   * Uses signed URLs from instrumental-analysis response
   */
  getAudioStreamUrl(jobId: string, stemType: string): string {
    const token = getAccessToken()
    const base = `${API_BASE_URL}/api/review/${jobId}/audio/${stemType}`
    return token ? `${base}?token=${encodeURIComponent(token)}` : base
  },
}

/**
 * Signal the backend to start the encoding worker VM.
 * Fire-and-forget — doesn't wait for the VM to boot.
 */
export async function warmupEncodingWorker(): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/api/internal/encoding-worker/warmup`, {
      method: 'POST',
      headers: getAuthHeaders(),
    })
  } catch {
    // Fire-and-forget, don't throw on failure
  }
}

/**
 * Send heartbeat to keep encoding worker alive during active session.
 */
export async function heartbeatEncodingWorker(): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/api/internal/encoding-worker/heartbeat`, {
      method: 'POST',
      headers: getAuthHeaders(),
    })
  } catch {
    // Fire-and-forget
  }
}

export interface DeleteOutputsResponse {
  status: string;
  job_id: string;
  message: string;
  deleted_services: {
    youtube: { status: string; video_id?: string; reason?: string; error?: string };
    dropbox: { status: string; path?: string; reason?: string; error?: string };
    gdrive: { status: string; files?: Record<string, boolean>; reason?: string; error?: string };
    brand_code?: { status: string; code?: string; reason?: string; error?: string };
  };
  cleared_state_data: string[];
  outputs_deleted_at: string;
}

// Rate Limits API Types
export interface YouTubeQueueEntry {
  job_id: string;
  status: string;
  reason?: string;
  user_email?: string;
  artist?: string;
  title?: string;
  brand_code?: string;
  queued_at?: string;
  attempts: number;
  max_attempts: number;
  last_error?: string;
  youtube_url?: string;
  notification_sent: boolean;
}

export interface YouTubeQueueListResponse {
  entries: YouTubeQueueEntry[];
  stats: {
    queued: number;
    processing: number;
    failed: number;
    completed: number;
    total: number;
  };
}

export interface BlocklistsResponse {
  external_domains: string[];
  manual_domains: string[];
  allowlisted_domains: string[];
  blocked_emails: string[];
  blocked_ips: string[];
  last_sync_at?: string;
  last_sync_count?: number;
  updated_at?: string;
  updated_by?: string;
}

export interface SuccessResponse {
  success: boolean;
  message: string;
}

// Feedback-for-Credits types
export interface FeedbackRequest {
  overall_rating: number;
  ease_of_use_rating: number;
  lyrics_accuracy_rating: number;
  correction_experience_rating: number;
  what_went_well?: string;
  what_could_improve?: string;
  additional_comments?: string;
  would_recommend: boolean;
  would_use_again: boolean;
}

export interface FeedbackResponse {
  status: string;
  message: string;
  credits_granted: number;
}

// =============================================================================
// Payment Admin Types
// =============================================================================

export interface RevenueSummary {
  total_gross: number;
  total_fees: number;
  total_net: number;
  total_refunds: number;
  transaction_count: number;
  average_order_value: number;
  revenue_by_type: Record<string, number>;
}

export interface RevenueChartPoint {
  date: string;
  gross: number;
  net: number;
  fees: number;
  count: number;
}

export interface PaymentRecord {
  session_id: string;
  payment_intent_id?: string;
  charge_id?: string;
  amount_total: number;
  currency: string;
  stripe_fee: number;
  net_amount: number;
  customer_email: string;
  customer_name: string;
  stripe_customer_id?: string;
  payment_method_type: string;
  card_brand: string;
  card_last4: string;
  order_type: string;
  package_id?: string;
  credits_granted: number;
  product_description: string;
  artist?: string;
  title?: string;
  job_id?: string;
  status: string;
  refund_amount: number;
  refund_id?: string;
  refunded_at?: string;
  refund_reason?: string;
  created_at?: string;
  processed_at?: string;
  is_test: boolean;
  promotion_code?: string;
  discount_amount: number;
  receipt_url?: string;
  stripe_dashboard_url?: string;
}

export interface PaymentListResponse {
  payments: PaymentRecord[];
  total: number;
  has_more: boolean;
}

export interface StripeBalance {
  available: number;
  pending: number;
  currency: string;
}

export interface PayoutRecord {
  id: string;
  amount: number;
  currency: string;
  status: string;
  arrival_date?: number;
  created?: number;
  description?: string;
  method?: string;
}

export interface DisputeRecord {
  id: string;
  amount: number;
  currency: string;
  status: string;
  reason: string;
  charge_id?: string;
  created?: number;
  evidence_due_by?: number;
  payment_intent_id?: string;
}

export interface UserPaymentHistory {
  email: string;
  payments: PaymentRecord[];
  total_spent: number;
  total_refunded: number;
  net_spent: number;
  payment_count: number;
  first_payment_at?: string;
  last_payment_at?: string;
}

export interface WebhookEvent {
  event_id: string;
  event_type: string;
  created_at?: string;
  processed_at?: string;
  status: string;
  error_message?: string;
  session_id?: string;
  customer_email?: string;
  summary?: string;
}

export interface RefundRequest {
  amount?: number;
  reason: string;
}

export interface RefundResponse {
  success: boolean;
  message: string;
  session_id: string;
}

export interface ChangeVisibilityResponse {
  status: string;
  job_id: string;
  message: string;
  previous_visibility: string;
  new_visibility: string;
  reprocessing_required: boolean;
}

// Anti-abuse investigation types
export interface AbuseSuspiciousUser {
  email: string;
  signup_ip: string | null;
  device_fingerprint: string | null;
  credits: number;
  total_jobs_created: number;
  total_jobs_completed: number;
  total_spent: number;
  has_submitted_feedback: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AbuseSuspiciousResponse {
  count: number;
  users: AbuseSuspiciousUser[];
}

export interface AbuseRelatedUser {
  email: string;
  credits: number;
  total_jobs_created: number;
  total_spent: number;
  created_at: string;
  signup_ip?: string | null;
  device_fingerprint?: string | null;
}

export interface AbuseRelatedResponse {
  user: {
    email: string;
    signup_ip: string | null;
    device_fingerprint: string | null;
    credits: number;
    total_jobs_created: number;
    total_spent: number;
    created_at: string;
  };
  related_by_ip: AbuseRelatedUser[];
  related_by_fingerprint: AbuseRelatedUser[];
}

export interface AbuseByIpResponse {
  ip_address: string;
  count: number;
  users: AbuseRelatedUser[];
}

export interface AbuseByFingerprintResponse {
  device_fingerprint: string;
  count: number;
  users: AbuseRelatedUser[];
}

// Correlation cluster types
export interface AbuseClusterUser {
  email: string;
  signup_ip: string | null;
  device_fingerprint: string | null;
  credits: number;
  total_jobs_created: number;
  total_jobs_completed: number;
  total_spent: number;
  created_at: string;
  user_agent?: string | null;
}

export interface AbuseFingerprintCluster {
  fingerprint: string;
  count: number;
  users: AbuseClusterUser[];
}

export interface AbuseIpCluster {
  ip: string;
  count: number;
  users: AbuseClusterUser[];
}

export interface AbuseCorrelationsResponse {
  fingerprint_clusters: AbuseFingerprintCluster[];
  ip_clusters: AbuseIpCluster[];
}

// IP geolocation types
export interface IpGeoInfo {
  status: string;
  ip: string;
  country?: string;
  country_code?: string;
  region?: string;
  city?: string;
  isp?: string;
  org?: string;
  as_number?: string;
  as_name?: string;
  timezone?: string;
  cached_at?: string;
}

// Admin feedback types
export interface AdminFeedbackItem {
  id: string;
  user_email: string;
  created_at: string | null;
  overall_rating: number;
  ease_of_use_rating: number;
  lyrics_accuracy_rating: number;
  correction_experience_rating: number;
  what_went_well: string | null;
  what_could_improve: string | null;
  additional_comments: string | null;
  would_recommend: boolean;
  would_use_again: boolean;
}

export interface AdminFeedbackListResponse {
  items: AdminFeedbackItem[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  avg_overall_rating: number | null;
  avg_ease_of_use_rating: number | null;
  avg_lyrics_accuracy_rating: number | null;
  avg_correction_experience_rating: number | null;
}

export { ApiError };

