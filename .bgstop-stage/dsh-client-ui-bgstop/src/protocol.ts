/**
 * Route family + wire types shared by the host routes and the browser API
 * client. Purely structural JSON — no runtime identity to share.
 */

/** The /api/dsh-bgstop route family. */
export const BGSTOP_API = {
  jobs: '/api/dsh-bgstop/jobs',
  stop: '/api/dsh-bgstop/stop',
} as const

/** One background job as exposed to the browser. */
export interface BgstopJobView {
  /** The registry-issued id (`<kind>-N`). */
  id: string
  /** The producer kind the job was registered with. */
  kind: string
  /** The producer-supplied one-line label (the command; the description). */
  label: string
  /** Current lifecycle state. */
  status: 'running' | 'stopping' | 'completed' | 'killed' | 'failed'
  /** Kind-specific status detail, present once the producer supplied one. */
  detail?: string
  /** Epoch ms when the job was registered. */
  startedAt: number
  /** Epoch ms when the job settled; absent while running/stopping. */
  finishedAt?: number
  /** Owner session id used for authorization and correlation; null when unowned. */
  ownerSession: string | null
}

/** Outcome of one stop request. */
export interface BgstopStopResult {
  /** The job id this result describes. */
  id: string
  /** 'requested' when cancellation was issued; 'already-finished' when settled. */
  outcome?: 'requested' | 'already-finished'
  /** The owner session resolved for this job, when known. */
  ownerSession?: string | null
  /** Present when the stop could not be issued (unknown/foreign job, etc.). */
  error?: string
}
