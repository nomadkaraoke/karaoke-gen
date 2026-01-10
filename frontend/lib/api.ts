/**
 * API client for karaoke-gen backend
 */

import type { VideoThemeSummary, VideoThemeDetail, ThemesListResponse, ThemeDetailResponse, ColorOverrides } from './video-themes';
import type { MagicLinkResponse, VerifyMagicLinkResponse, UserProfileResponse } from './types';

// In development, use relative URLs to go through Next.js proxy (avoids CORS)
// In production (static export), use the full backend URL
export const API_BASE_URL = typeof window !== 'undefined' && window.location.hostname === 'localhost'
  ? ''  // Relative URL - goes through Next.js proxy
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
  user_email?: string;
  outputs_deleted_at?: string;
  outputs_deleted_by?: string;
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

export const api = {
  /**
   * List all jobs
   */
  async listJobs(params?: { status?: string; limit?: number; exclude_test?: boolean }): Promise<Job[]> {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.exclude_test !== undefined) searchParams.set('exclude_test', String(params.exclude_test));

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

    const response = await fetch(`${API_BASE_URL}/api/jobs/upload`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: formData,
    });

    return handleResponse<UploadJobResponse>(response);
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
   * Complete the lyrics review
   */
  async completeReview(jobId: string): Promise<{ status: string; job_status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/complete-review`, {
      method: 'POST',
      headers: getAuthHeaders()
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
    selection: 'clean' | 'with_backing' | 'custom'
  ): Promise<{ status: string; job_status: string; selection: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/select-instrumental`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ selection }),
    });
    return handleResponse(response);
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
  async selectAudioResult(jobId: string, index: number): Promise<{ status: string; message: string }> {
    const response = await fetch(`${API_BASE_URL}/api/audio-search/${jobId}/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ selection_index: index }),
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
  async sendMagicLink(email: string): Promise<MagicLinkResponse> {
    const response = await fetch(`${API_BASE_URL}/api/users/auth/magic-link`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email }),
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
  // Beta Tester API endpoints
  // ==========================================================================

  /**
   * Enroll as a beta tester to receive free credits
   */
  async enrollBetaTester(
    email: string,
    promiseText: string,
    acceptCorrectionsWork: boolean
  ): Promise<BetaEnrollResponse> {
    const response = await fetch(`${API_BASE_URL}/api/users/beta/enroll`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: email.toLowerCase(),
        promise_text: promiseText,
        accept_corrections_work: acceptCorrectionsWork,
      }),
    });
    return handleResponse(response);
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
    deviceName?: string
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
};

// Types for credits/payment
export interface CreditPackage {
  id: string;
  credits: number;
  price_cents: number;
  name: string;
  description: string;
}

export interface BetaEnrollResponse {
  status: string;
  message: string;
  credits_granted: number;
  session_token: string | null;
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
  total_beta_testers: number;
}

export interface AdminUser {
  email: string;
  role: 'user' | 'admin';
  credits: number;
  display_name?: string;
  total_jobs_created?: number;
  total_jobs_completed?: number;
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
  is_beta_tester: boolean;
  beta_tester_status?: string;
  credit_transactions: Array<{
    id: string;
    amount: number;
    reason: string;
    created_at: string;
    job_id?: string;
    created_by?: string;
  }>;
  recent_jobs: Array<{
    job_id: string;
    status: string;
    artist?: string;
    title?: string;
    created_at?: string;
  }>;
  active_sessions_count: number;
}

export interface AdminBetaStats {
  total_beta_testers: number;
  active_testers: number;
  pending_feedback: number;
  completed_feedback: number;
  total_feedback_submissions: number;
  average_ratings: {
    overall: number;
    ease_of_use: number;
    lyrics_accuracy: number;
    correction_experience: number;
  };
}

export interface AdminBetaFeedback {
  id: string;
  user_email: string;
  job_id?: string;
  overall_rating: number;
  ease_of_use_rating?: number;
  lyrics_accuracy_rating?: number;
  correction_experience_rating?: number;
  what_went_well?: string;
  what_could_improve?: string;
  additional_comments?: string;
  would_recommend?: boolean;
  would_use_again?: boolean;
  created_at: string;
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
   * Get beta program statistics
   */
  async getBetaStats(params?: { exclude_test?: boolean }): Promise<AdminBetaStats> {
    const searchParams = new URLSearchParams();
    if (params?.exclude_test !== undefined) {
      searchParams.set('exclude_test', String(params.exclude_test));
    }
    const url = `${API_BASE_URL}/api/users/admin/beta/stats${searchParams.toString() ? '?' + searchParams.toString() : ''}`;
    const response = await fetch(url, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
  },

  /**
   * Get beta program feedback list
   */
  async getBetaFeedback(limit: number = 50): Promise<{ feedback: AdminBetaFeedback[]; total: number }> {
    const response = await fetch(`${API_BASE_URL}/api/users/admin/beta/feedback?limit=${limit}`, {
      headers: getAuthHeaders()
    });
    return handleResponse(response);
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

  // =========================================================================
  // Rate Limits API
  // =========================================================================

  /**
   * Get rate limit statistics
   */
  async getRateLimitStats(): Promise<RateLimitStatsResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/stats`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Get rate limit status for a specific user
   */
  async getUserRateLimitStatus(email: string): Promise<UserRateLimitStatusResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/users/${encodeURIComponent(email)}`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

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

  /**
   * Get all user overrides
   */
  async getUserOverrides(): Promise<UserOverridesListResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/overrides`,
      { headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },

  /**
   * Set user override
   */
  async setUserOverride(email: string, override: UserOverrideRequest): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/overrides/${encodeURIComponent(email)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(override),
      }
    );
    return handleResponse(response);
  },

  /**
   * Remove user override
   */
  async removeUserOverride(email: string): Promise<SuccessResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/admin/rate-limits/overrides/${encodeURIComponent(email)}`,
      { method: 'DELETE', headers: getAuthHeaders() }
    );
    return handleResponse(response);
  },
};

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
}

export interface DeleteOutputsResponse {
  status: string;
  job_id: string;
  message: string;
  deleted_services: {
    youtube: { status: string; video_id?: string; reason?: string; error?: string };
    dropbox: { status: string; path?: string; reason?: string; error?: string };
    gdrive: { status: string; files?: Record<string, boolean>; reason?: string; error?: string };
  };
  cleared_state_data: string[];
  outputs_deleted_at: string;
}

// Rate Limits API Types
export interface RateLimitStatsResponse {
  jobs_per_day_limit: number;
  youtube_uploads_per_day_limit: number;
  beta_ip_per_day_limit: number;
  rate_limiting_enabled: boolean;
  youtube_uploads_today: number;
  youtube_uploads_remaining: number;
  disposable_domains_count: number;
  blocked_emails_count: number;
  blocked_ips_count: number;
  total_overrides: number;
}

export interface UserRateLimitStatusResponse {
  email: string;
  jobs_today: number;
  jobs_limit: number;
  jobs_remaining: number;
  has_bypass: boolean;
  custom_limit?: number;
  bypass_reason?: string;
}

export interface BlocklistsResponse {
  disposable_domains: string[];
  blocked_emails: string[];
  blocked_ips: string[];
  updated_at?: string;
  updated_by?: string;
}

export interface SuccessResponse {
  success: boolean;
  message: string;
}

export interface UserOverride {
  email: string;
  bypass_job_limit: boolean;
  custom_daily_job_limit?: number;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface UserOverrideRequest {
  bypass_job_limit: boolean;
  custom_daily_job_limit?: number;
  reason: string;
}

export interface UserOverridesListResponse {
  overrides: UserOverride[];
  total: number;
}

export { ApiError };

