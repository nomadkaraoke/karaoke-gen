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
}

export interface UserPublic {
  email: string
  role: UserRole
  credits: number
  display_name?: string
  total_jobs_created?: number
  total_jobs_completed?: number
  feedback_eligible?: boolean
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
