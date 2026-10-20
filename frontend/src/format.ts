import type { CommuteRoute } from './api'

// Every number, date and time goes through Intl in the browser's locale; money is always USD.

const NBSP = '\u00a0'
const usd = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })
const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })
const seconds = new Intl.NumberFormat(undefined, { style: 'unit', unit: 'second', unitDisplay: 'narrow', maximumFractionDigits: 1 })
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const calendarDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' })
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

export const money = (v: number | null | undefined) => (v == null ? '—' : usd.format(Math.round(v)))
export const num = (v: number | null | undefined) => (v == null ? '—' : integer.format(v))
export const pct = (v: number | null | undefined) => (v == null ? '—' : percent.format(v / 100))
export const minutes = (v: number | null | undefined) => (v == null ? '—' : `${integer.format(v)}${NBSP}min`)

/** "34 min · A → F · 6 min walk" */
export function commute(min: number | null | undefined, route: CommuteRoute | null | undefined): string {
  if (min == null) return '—'
  const parts = [minutes(min)]
  if (route?.lines?.length) parts.push(route.lines.join(' → '))
  if (route?.walk_min) parts.push(`${minutes(route.walk_min)} walk`)
  return parts.join(' · ')
}

export function range(lo: number | null, hi: number | null, fmt: (v: number) => string): string {
  if (lo == null && hi == null) return '—'
  if (lo == null || hi == null || lo === hi) return fmt((lo ?? hi)!)
  return `${fmt(lo)}–${fmt(hi)}`
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31_536_000], ['month', 2_592_000], ['week', 604_800], ['day', 86_400], ['hour', 3600], ['minute', 60],
]

/** Relative time ("3 hours ago", "in 2 days"); "now" under a minute. */
export function ago(t: string | null | undefined): string {
  if (!t) return 'never'
  const s = (new Date(t).getTime() - Date.now()) / 1000
  if (Number.isNaN(s)) return t
  for (const [unit, size] of UNITS) if (Math.abs(s) >= size) return relative.format(Math.round(s / size), unit)
  return relative.format(0, 'second')
}

export const when = (t: string | null | undefined) => (t ? dateTime.format(new Date(t)) : '—')

/** A calendar date as sources give it (YYYY-MM-DD), formatted without shifting it across time zones. */
export function date(d: string | null | undefined): string {
  if (!d) return '—'
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? calendarDate.format(new Date(`${d}T00:00:00Z`)) : d
}

export function duration(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return seconds.format(s)
  return `${integer.format(Math.floor(s / 60))}m${NBSP}${String(Math.floor(s % 60)).padStart(2, '0')}s`
}

export const VERDICT_COLOR = { eligible: 'green', possible: 'yellow', excluded: 'gray' } as const
export const STATUS_COLOR: Record<string, string> = {
  open: 'green', waitlist: 'blue', upcoming: 'cyan', closed: 'gray', gone: 'red',
}
export const EVENT_COLOR: Record<string, string> = {
  new: 'green', reopened: 'teal', changed: 'blue', closed: 'gray', gone: 'red', page_changed: 'violet',
  watching: 'gray', source_failed: 'red', source_recovered: 'green',
}
