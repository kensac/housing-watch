import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

export const money = (v: number | null | undefined) => (v == null ? '—' : `$${Math.round(v).toLocaleString('en-US')}`)

export function range(lo: number | null, hi: number | null, fmt: (v: number) => string): string {
  if (lo == null && hi == null) return '—'
  if (lo == null || hi == null || lo === hi) return fmt((lo ?? hi)!)
  return `${fmt(lo)}–${fmt(hi)}`
}

export const ago = (t: string | null | undefined) => (t ? dayjs(t).fromNow() : 'never')
export const when = (t: string | null | undefined) => (t ? dayjs(t).format('MMM D YYYY, h:mm A') : '—')
export const day = (t: string | null | undefined) => (t ? dayjs(t).format('MMM D') : '—')

export function duration(s: number | null | undefined): string {
  if (s == null) return '—'
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, '0')}s`
}

export const VERDICT_COLOR = { eligible: 'green', possible: 'yellow', excluded: 'gray' } as const
export const STATUS_COLOR: Record<string, string> = {
  open: 'green', waitlist: 'blue', upcoming: 'cyan', closed: 'gray', gone: 'red',
}
export const EVENT_COLOR: Record<string, string> = {
  new: 'green', reopened: 'teal', changed: 'blue', closed: 'gray', gone: 'red', page_changed: 'violet',
  watching: 'gray', source_failed: 'red', source_recovered: 'green',
}
