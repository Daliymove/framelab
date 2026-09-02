/**
 * dsh-client-ui-bgstop — browser half. Registers the session-header
 * stop-background-tasks button: it appears only while background jobs are
 * running and opens a menu that lists every running job (cross-session,
 * tagged with its owner session) with per-row stop and stop-all actions,
 * talking to the /api/dsh-bgstop routes.
 */

import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import type {} from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import { BgStopAction } from './BgStopAction.tsx'
import { en, zh, type BgstopKey } from './locales.ts'

/** Locale namespace this plugin owns. */
const NS = 'bgstop'

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    /** Session-header stop-background-tasks surface copy. */
    'bgstop': BgstopKey
  }
}

/** Required services: the slot registry and the locale dictionary registry. */
export const inject = ['slots', 'locale'] as const

/**
 * Mount the stop-background-tasks action.
 * @param ctx - client root context (services: slots, locale).
 */
export function apply(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'bgstop: dictionaries')
  ctx.slots.inject('conversation.session.header.actions', () => ctx.slots.register({
    name: 'conversation.session.header.actions',
    id: 'bg-stop',
    order: 30,
    locale: NS,
  }, BgStopAction))
}
