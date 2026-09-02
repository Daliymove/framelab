/**
 * Stop-background-tasks copy: zh-first dictionaries with an English fallback.
 * The slot-injected translator (bound to the registered namespace) takes
 * precedence; this local lookup covers surfaces without it.
 */

/** zh dictionary (key-set source of truth). */
export const zh = {
  'trigger': '停止后台任务',
  'menu.aria': '运行中的后台任务',
  'head': '运行中的后台任务',
  'refresh': '刷新',
  'stop.all': '全部停止',
  'stop': '停止',
  'stopping': '停止中',
  'status.running': '运行中',
  'status.stopping': '正在停止',
  'flash.ok': '已请求停止',
  'flash.err': '停止失败，请重试',
} satisfies Record<string, string>

/** en dictionary, complete against the zh key set. */
export const en: Record<keyof typeof zh, string> = {
  'trigger': 'Stop background tasks',
  'menu.aria': 'Running background jobs',
  'head': 'Running background jobs',
  'refresh': 'Refresh',
  'stop.all': 'Stop all',
  'stop': 'Stop',
  'stopping': 'Stopping…',
  'status.running': 'running',
  'status.stopping': 'stopping',
  'flash.ok': 'Stop requested',
  'flash.err': 'Failed to stop; please retry',
}

/** The dictionary key union. */
export type BgstopKey = keyof typeof zh

/** Active dictionary, picked by the document language at call time. */
export function dictionary(): Record<BgstopKey, string> {
  const lang = typeof document !== 'undefined' ? document.documentElement.lang : 'zh'
  return lang.toLowerCase().startsWith('en') ? en : zh
}

/** Translate a key. */
export function t(key: BgstopKey): string {
  return dictionary()[key]
}
