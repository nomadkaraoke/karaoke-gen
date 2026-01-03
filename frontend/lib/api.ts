/**
 * API client for karaoke-gen backend
 */

import type { VideoThemeSummary, VideoThemeDetail, ThemesListResponse, ThemeDetailResponse, ColorOverrides } from './video-themes';
import type { MagicLinkResponse, VerifyMagicLinkResponse, UserProfileResponse } from './types';

// In development, use relative URLs to go through Next.js proxy (avoids CORS)
// In production (static export), use the full backend URL
const API_BASE_URL = typeof window !== 'undefined' && window.location.hostname === 'localhost'
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
  async listJobs(params?: { status?: string; limit?: number }): Promise<Job[]> {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    
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
  // Version API endpoint
  // ==========================================================================

  /**
   * Get backend service info including version
   */
  async getBackendInfo(): Promise<{ service: string; version: string; status: string }> {
    const response = await fetch(`${API_BASE_URL}/`, {
      headers: getAuthHeaders()
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

export { ApiError };

