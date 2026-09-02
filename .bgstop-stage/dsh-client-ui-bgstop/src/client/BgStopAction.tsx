/**
 * The stop-background-tasks action rendered in the session-header action row.
 *
 * Renders nothing while no background job is running; clicking the trigger
 * opens a menu listing every running job (cross-session, tagged with its
 * owner session) with per-row stop and stop-all actions. Data comes from the
 * /api/dsh-bgstop routes; the menu refreshes on open and after each stop.
 */

import React from 'react'
import { listJobs, stopJobs } from './api.ts'
import type { BgstopJobView } from '../protocol.ts'
import { t as localT } from './locales.ts'
import css from './bgstop.module.css'

/** A job whose lifecycle is still open (duration ticks). */
function isLive(job: BgstopJobView): boolean {
  return job.status === 'running' || job.status === 'stopping'
}

/** Props supplied by the session-header action seat (subset used). */
export interface BgStopActionProps {
  /** The session whose header hosts this button. */
  sessionId: string
  /** Slot-injected translator bound to this plugin's namespace. */
  t?: (key: string) => string
}

/**
 * The stop-background-tasks button.
 * @param props - session id and the optional namespace translator.
 */
export function BgStopAction(props: BgStopActionProps): React.ReactElement | null {
  const { sessionId, t } = props
  const tr = (key: string): string => (typeof t === 'function' ? t(key) : localT(key as never))

  const [items, setItems] = React.useState<BgstopJobView[]>([])
  const [loaded, setLoaded] = React.useState(false)
  const [loadError, setLoadError] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [busyIds, setBusyIds] = React.useState<string[]>([])
  const [flash, setFlash] = React.useState('')
  const [flashDetail, setFlashDetail] = React.useState('')

  const load = async (): Promise<void> => {
    try {
      setItems(await listJobs())
      setLoadError('')
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoaded(true)
    }
  }

  React.useEffect(() => { void load() }, [])

  React.useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent): void => {
      const el = event.target as Element | null
      if (el !== null && typeof el.closest === 'function' && el.closest('[data-bgstop-root]') !== null) return
      setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const doStop = async (ids: string[]): Promise<void> => {
    setBusyIds(prev => [...prev, ...ids.filter(id => !prev.includes(id))])
    setFlash('')
    setFlashDetail('')
    try {
      const results = await stopJobs(ids)
      const failed = results.filter(result => result.error !== undefined)
      if (failed.length > 0) {
        setFlash('err')
        setFlashDetail(failed.map(result => `${result.id}: ${result.error}`).join('; '))
      } else {
        setFlash('ok')
      }
      await load()
    } catch (error) {
      setFlash('err')
      setFlashDetail(error instanceof Error ? error.message : String(error))
    } finally {
      setBusyIds(prev => prev.filter(id => !ids.includes(id)))
    }
  }

  const live = items.filter(isLive)
  if (!loaded) return null
  if (live.length === 0) return null

  return (
    <div className={css.root} data-bgstop-root="1">
      <button
        type="button"
        className={css.trigger}
        aria-expanded={open}
        onClick={() => {
          if (!open) {
            setFlash('')
            setFlashDetail('')
            void load()
          }
          setOpen(value => !value)
        }}
      >
        <span className={css.triggerIcon} aria-hidden="true" />
        <span>{tr('trigger')}</span>
        <span className={css.count}>{live.length}</span>
      </button>
      {open ? (
        <div className={css.menu} role="menu" aria-label={tr('menu.aria')}>
          <div className={css.head}>
            <span>{tr('head')}</span>
            <span className={css.headActions}>
              <button type="button" className={css.headButton} onClick={() => { void load() }}>
                {tr('refresh')}
              </button>
              <button
                type="button"
                className={css.headButton}
                disabled={busyIds.length > 0}
                onClick={() => { void doStop(live.map(job => job.id)) }}
              >
                {tr('stop.all')}
              </button>
            </span>
          </div>
          {loadError !== '' ? (
            <div className={`${css.flash} ${css.flashErr}`}>{loadError}</div>
          ) : null}
          {flash !== '' ? (
            <div className={flash === 'ok' ? `${css.flash} ${css.flashOk}` : `${css.flash} ${css.flashErr}`}>
              {flash === 'ok' ? tr('flash.ok') : tr('flash.err')}
              {flashDetail !== '' ? <div className={css.flashDetail}>{flashDetail}</div> : null}
            </div>
          ) : null}
          <ul className={css.list}>
            {live.map(job => {
              const busy = busyIds.includes(job.id)
              const foreign = job.ownerSession !== null && job.ownerSession !== sessionId
              return (
                <li key={job.id} className={css.row}>
                  <span className={css.kind}>{job.kind}</span>
                  <span className={css.label} title={job.label}>{job.label}</span>
                  {foreign ? (
                    <span className={css.session} title={job.ownerSession ?? undefined}>
                      {job.ownerSession?.slice(0, 12)}
                    </span>
                  ) : null}
                  <span className={css.status}>
                    {job.status === 'stopping' ? tr('status.stopping') : tr('status.running')}
                  </span>
                  <button
                    type="button"
                    className={css.stop}
                    disabled={busy}
                    onClick={() => { void doStop([job.id]) }}
                  >
                    {busy ? tr('stopping') : tr('stop')}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
