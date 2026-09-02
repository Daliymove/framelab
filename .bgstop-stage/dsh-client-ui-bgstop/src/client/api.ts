/**
 * Browser-side API client for the /api/dsh-bgstop route family. The only data
 * access path the stop-background-tasks button uses — plain fetch, same origin.
 */

import { BGSTOP_API, type BgstopJobView, type BgstopStopResult } from '../protocol.ts'

/** Parse a JSON response or throw an Error carrying the route's message. */
async function readJson<T>(response: Response): Promise<T> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error(`HTTP ${response.status}: invalid JSON response`)
  }
  if (!response.ok) {
    const message = typeof body === 'object' && body !== null && typeof (body as { error?: unknown }).error === 'string'
      ? (body as { error: string }).error
      : `HTTP ${response.status}`
    throw new Error(message)
  }
  return body as T
}

/** List every background job the host registry holds (across sessions). */
export async function listJobs(): Promise<BgstopJobView[]> {
  const response = await fetch(BGSTOP_API.jobs)
  const body = await readJson<{ jobs: BgstopJobView[] }>(response)
  return body.jobs
}

/** Request cancellation of the given background jobs. */
export async function stopJobs(jobIds: string[]): Promise<BgstopStopResult[]> {
  const response = await fetch(BGSTOP_API.stop, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jobIds }),
  })
  const body = await readJson<{ results: BgstopStopResult[] }>(response)
  return body.results
}
