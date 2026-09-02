/**
 * The /api/dsh-bgstop route family: list background jobs across sessions and
 * stop them by id. Every route carries a loopback-only trust fence (plus
 * browser same-origin markers) — these endpoints terminate running processes,
 * so LAN-exposed dsh web deployments must not serve them.
 */

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { WebRoute } from '@deepseek-ai/dsh-host-webserver'
import type { BgstopJobView, BgstopStopResult } from './protocol.ts'
import { BGSTOP_API } from './protocol.ts'

/** Cap on JSON request bodies (stop payloads are small). */
const MAX_JSON_BODY_BYTES = 64 * 1024

/** Loopback literal check plus browser same-origin markers (mirrors the pairing routes' fence). */
function isLoopbackRequest(request: IncomingMessage): boolean {
  const address = request.socket.remoteAddress
  if (address !== '127.0.0.1' && address !== '::1' && address !== '::ffff:127.0.0.1') return false
  const host = request.headers.host
  if (typeof host !== 'string') return false
  let hostUrl: URL
  try {
    hostUrl = new URL(`http://${host}`)
  } catch {
    return false
  }
  if (hostUrl.hostname !== '127.0.0.1' && hostUrl.hostname !== 'localhost' && hostUrl.hostname !== '[::1]') return false
  if (request.headers['sec-fetch-site'] === 'cross-site') return false
  const origin = request.headers.origin
  if (origin === undefined) return true
  try {
    return new URL(origin).host === hostUrl.host
  } catch {
    return false
  }
}

/** One JSON response. */
function writeJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'referrer-policy': 'no-referrer' })
  res.end(payload)
}

/** Read a JSON request body (undefined when too large or unparseable). */
async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown> | undefined> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = chunk as Buffer
    size += buffer.length
    if (size > MAX_JSON_BODY_BYTES) return undefined
    chunks.push(buffer)
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, unknown> : undefined
  } catch {
    return undefined
  }
}

/** Dependencies the routes need from the plugin (kept injectable for tests). */
export interface BgstopRouteDeps {
  listJobs(): BgstopJobView[]
  stopJobs(ids: string[]): BgstopStopResult[]
}

/** Build the /api/dsh-bgstop route family. */
export function makeRoutes(deps: BgstopRouteDeps): WebRoute[] {
  const routes: WebRoute[] = [
    {
      kind: 'exact',
      path: BGSTOP_API.jobs,
      handler: (req, res) => {
        if (!isLoopbackRequest(req)) {
          writeJson(res, 403, { error: 'loopback only' })
          return
        }
        if (req.method !== 'GET' && req.method !== 'HEAD') {
          writeJson(res, 405, { error: 'method not allowed' })
          return
        }
        writeJson(res, 200, { jobs: deps.listJobs() })
      },
    },
    {
      kind: 'exact',
      path: BGSTOP_API.stop,
      handler: async (req, res) => {
        if (!isLoopbackRequest(req)) {
          writeJson(res, 403, { error: 'loopback only' })
          return
        }
        if (req.method !== 'POST') {
          writeJson(res, 405, { error: 'method not allowed' })
          return
        }
        const body = await readJsonBody(req)
        const ids = Array.isArray(body?.jobIds)
          ? (body!.jobIds as unknown[]).filter((id): id is string => typeof id === 'string' && id.length > 0)
          : []
        writeJson(res, 200, { results: deps.stopJobs(ids) })
      },
    },
  ]
  return routes
}
