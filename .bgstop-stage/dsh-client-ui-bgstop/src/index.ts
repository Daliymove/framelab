/**
 * dsh-client-ui-bgstop — host half. Registers the /api/dsh-bgstop route
 * family (list background jobs of every session; stop them through the jobs
 * registry with the owner session's agent as caller), plus a system-prompt
 * announcement. The browser half (./client) renders the session-header
 * stop-background-tasks button that talks to these routes.
 *
 * Everything rides official NPM SDK packages — no dsh source changes.
 */

import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-host-webserver'
import type {} from '@deepseek-ai/dsh-system-prompt'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { JobSnapshot } from '@deepseek-ai/dsh-jobs'
import z from 'schemastery'
import type { BgstopJobView, BgstopStopResult } from './protocol.ts'
import { makeRoutes } from './routes.ts'

/** Stable cordis plugin name. */
export const name = 'bgstop'

/** Services required before the routes can mount. */
export const inject = ['webServer', 'systemPrompt']

/** Minimal jobs-service surface this plugin reads (ctx.get, optional). */
interface JobsServiceLike {
  list(caller?: Agent): JobSnapshot[]
  kill(id: string, caller?: Agent, reason?: string): 'requested' | 'already-finished'
}

/** Minimal agents-service surface (ctx.get, optional). */
interface AgentsServiceLike {
  list(): Agent[]
}

/** Plugin config, validated by the same-named schemastery schema. */
export interface Config {
  /** Master switch for the plugin (routes + announcement). */
  enabled?: boolean
}

export const Config: z<Config> = z.object({
  enabled: z.boolean().default(true),
})

/** Order of the announcement section within the tool-guidance band. */
const SECTION_ORDER = 160

/** Model-facing announcement: plugin presence, capabilities, and limits. */
export const BGSTOP_GUIDANCE = '本机已安装 dsh-client-ui-bgstop 插件（DSH Web GUI 的停止后台任务按钮）：会话头部新增「停止后台任务」按钮，可快速列出并停止残留运行的后台任务（如 Python 后台任务），跨会话可见并标注来源会话；在 dsh-web-ui 插件全家桶仓库（packages/dsh-client-ui-bgstop）统一维护，经聚合包 web-ui-all 一键安装。用户提到「停止后台任务 / 残留后台任务 / 停掉后台应用」时即指本插件，请据此协作。'

/** Map one registry snapshot to the wire view. */
function toView(snapshot: JobSnapshot): BgstopJobView {
  return {
    id: snapshot.id,
    kind: snapshot.kind,
    label: snapshot.label,
    status: snapshot.status,
    ...(snapshot.detail !== undefined ? { detail: snapshot.detail } : {}),
    startedAt: snapshot.startedAt,
    ...(snapshot.finishedAt !== undefined ? { finishedAt: snapshot.finishedAt } : {}),
    ownerSession: snapshot.ownerSession !== undefined ? snapshot.ownerSession : null,
  }
}

/**
 * Mount the routes and the announcement.
 * @param ctx - host plugin context carrying webServer/systemPrompt.
 * @param config - resolved plugin config (schema defaults applied by the loader).
 */
export function apply(ctx: Context, config?: Config): void {
  if ((config?.enabled ?? true) === false) return

  const jobs = ctx.get('jobs') as JobsServiceLike | undefined
  const agents = ctx.get('agents') as AgentsServiceLike | undefined

  /** Enumerate every session's jobs (plus unowned) through the registry. */
  const listJobs = (): BgstopJobView[] => {
    if (jobs === undefined) return []
    const seen = new Map<string, JobSnapshot>()
    const collect = (caller?: Agent): void => {
      let snapshots: JobSnapshot[] = []
      try {
        snapshots = jobs.list(caller)
      } catch {
        return
      }
      for (const snapshot of snapshots) {
        if (!seen.has(snapshot.id)) seen.set(snapshot.id, snapshot)
      }
    }
    if (agents !== undefined) {
      for (const agent of agents.list()) collect(agent)
    }
    collect(undefined)
    return [...seen.values()].map(toView)
  }

  /** Stop jobs by id, resolving the owner agent per job. */
  const stopJobs = (ids: string[]): BgstopStopResult[] => {
    if (jobs === undefined) return ids.map(id => ({ id, error: 'jobs service unavailable' }))
    const results: BgstopStopResult[] = []
    for (const id of ids) {
      try {
        let owner: Agent | undefined
        let ownerSession: string | null = null
        if (agents !== undefined) {
          for (const agent of agents.list()) {
            let snapshots: JobSnapshot[] = []
            try {
              snapshots = jobs.list(agent)
            } catch {
              continue
            }
            const match = snapshots.find(snapshot => snapshot.id === id)
            if (match !== undefined) {
              owner = agent
              ownerSession = match.ownerSession !== undefined ? match.ownerSession : null
              break
            }
          }
        }
        const outcome = jobs.kill(id, owner, 'stopped from the workbench UI')
        results.push({ id, outcome, ownerSession })
      } catch (error) {
        results.push({ id, error: error instanceof Error ? error.message : String(error) })
      }
    }
    return results
  }

  const routes = makeRoutes({ listJobs, stopJobs })
  ctx.effect(() => {
    const disposers = routes.map(route => ctx.webServer.register(route))
    return () => {
      for (const dispose of disposers) dispose()
    }
  }, 'dsh-client-ui-bgstop: routes')

  ctx.effect(() => ctx.systemPrompt.section({
    name: 'plugin:dsh-client-ui-bgstop',
    order: SECTION_ORDER,
    text: BGSTOP_GUIDANCE,
  }), 'dsh-client-ui-bgstop: announcement')
}
