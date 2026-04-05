export type UserRole = "user" | "admin"

export interface User {
  token: string
  role: UserRole
  credits: number
  email: string
  display_name?: string
  total_jobs_created?: number
  total_jobs_completed?: number
  feedback_eligible?: boolean
  has_active_referral_discount?: boolean
  referral_discount_percent?: number
  referral_discount_expires_at?: string
  referred_by_code?: string
}

export interface UserPublic {
  email: string
  role: UserRole
  credits: number
  display_name?: string
  total_jobs_created?: number
  total_jobs_completed?: number
  feedback_eligible?: boolean
  referral_code?: string
  has_active_referral_discount?: boolean
  referral_discount_percent?: number
  referral_discount_expires_at?: string
  referred_by_code?: string
}

export interface MagicLinkResponse {
  status: string
  message: string
}

export interface VerifyMagicLinkResponse {
  status: string
  session_token: string
  user: UserPublic
  message: string
  tenant_subdomain?: string | null
  credits_granted?: number
  credit_status?: string  // "granted", "denied", "already_granted", "not_applicable"
}

export interface UserProfileResponse {
  user: UserPublic
  has_session: boolean
}

export type JobStatus = "queued" | "processing" | "awaiting_review" | "awaiting_instrumental" | "completed" | "failed"

export interface Job {
  id: string
  userId: string
  artist: string
  title: string
  status: JobStatus
  sourceType: "youtube" | "upload"
  sourceUrl?: string
  fileName?: string
  progress: number
  createdAt: string
  updatedAt: string
  stages: JobStage[]
  resultUrl?: string
  errorMessage?: string
}

export interface JobStage {
  name: string
  status: "pending" | "in_progress" | "completed" | "failed"
  progress: number
  message?: string
}

// Referral system types
export interface ReferralLinkStats {
  clicks: number;
  signups: number;
  purchases: number;
  total_earned_cents: number;
}

export interface ReferralLink {
  code: string;
  display_name: string | null;
  custom_message: string | null;
  discount_percent: number;
  kickback_percent: number;
  discount_duration_days: number;
  earning_duration_days: number;
  stats: ReferralLinkStats;
  enabled: boolean;
  is_vanity: boolean;
}

export interface ReferralEarning {
  id: string;
  referred_email: string;
  purchase_amount_cents: number;
  earning_amount_cents: number;
  status: 'pending' | 'paid' | 'refunded';
  created_at: string;
}

export interface ReferralPayout {
  id: string;
  amount_cents: number;
  status: 'processing' | 'completed' | 'failed';
  created_at: string;
}

export interface ReferralDashboard {
  link: ReferralLink;
  pending_balance_cents: number;
  total_earned_cents: number;
  total_paid_cents: number;
  recent_earnings: ReferralEarning[];
  recent_payouts: ReferralPayout[];
  stripe_connect_configured: boolean;
}

export interface ReferralInterstitial {
  code: string;
  display_name: string | null;
  custom_message: string | null;
  discount_percent: number;
  discount_duration_days: number;
  valid: boolean;
}
