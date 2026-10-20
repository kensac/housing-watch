// The JSON API of src/housing_watch/web/app.py. Filters travel as repeated `f=<field>.<op>.<values>`.

export type Page = 'listings' | 'units' | 'events'
export type Verdict = 'eligible' | 'possible' | 'excluded'

export interface Field {
  name: string
  label: string
  group: string
  type: 'text' | 'number' | 'time' | 'bool'
  ops: string[]
  money: boolean
  choices: string[]
}

export interface PageMeta {
  fields: Field[]
  columns: Record<string, string>
  default_columns: string[]
  default_sort: string
  sorts: Record<string, string>
}

export interface Meta {
  profile: { household: number; income: number; sizes: string[]; excluded_boroughs: string[] }
  statuses: string[]
  verdicts: Verdict[]
  op_labels: Record<string, string>
  pages: Record<Page, PageMeta>
}

export interface Chip {
  i: number
  f: string
  label: string
  field: string
  op: string
  values: string[]
}

export interface ListResponse<T> {
  items: T[]
  total: number
  next_offset: number | null
  verdict_counts: Record<Verdict, number>
  filters: Chip[]
  problems: string[]
  hidden_gone: boolean
}

export interface CommuteRoute { lines?: string[]; walk_min?: number; transfers?: number } // {} when no route was found

export interface Listing {
  key: string
  source: string
  title: string
  url: string
  address: string | null
  borough: string | null
  status: string
  kind: string
  deadline: string | null
  senior_only: boolean
  first_seen: string
  last_changed: string
  gone_since: string | null
  verdict: Verdict
  reason: string
  units: number
  eligible_units: number
  sizes: string[]
  rent_min: number | null
  rent_max: number | null
  ami_min: number | null
  ami_max: number | null
  commute?: number | null // transit minutes to work; only eligible listings get one
  commute_route?: CommuteRoute | null
  application?: string | null // the tracker sheet's Status for this listing's row
  application_notes?: string | null
  tracker_row?: string | null // that row's Property
  tracker_match?: 'url' | 'address' | 'name' | 'manual' | null
}

export interface Unit {
  id: number
  key: string
  source: string
  title: string
  url: string
  address: string | null
  borough: string | null
  status: string
  kind: string
  deadline: string | null
  first_seen: string
  last_changed: string
  gone_since: string | null
  listing_verdict: Verdict
  size: string
  unit_no: string | null
  rent: number | null
  ami: number | null
  set_aside: string | null
  available: boolean | null
  chart_year: number | null
  income_min: number | null
  income_max: number | null
  verdict: Verdict
  reason: string
  commute?: number | null
  application?: string | null
}

export interface Event {
  id: number
  at: string
  source: string
  key: string | null
  type: string
  summary: string | null
  detail: Record<string, unknown> | null
  notified: string | null
  baseline: boolean
  title: string | null
  verdict: Verdict | null
}

export interface EventsResponse {
  items: Event[]
  total: number
  next_before: number | null
  filters: Chip[]
  problems: string[]
}

export interface DetailUnit {
  size: string
  unit_no: string | null
  rent: number | null
  ami: number | null
  set_aside: string | null
  available: boolean | null
  bands: [number, number | null, number | null][]
  verdict: Verdict
  reason: string
  chart_year: number | null
}

export interface Detail {
  summary: Listing
  notes: string | null
  ext_id: string
  last_seen: string
  units: DetailUnit[]
  events: Event[]
  household: number
  pdfs: string[]
}

export interface Applications {
  ready: boolean
  pdfs: { n: number; url: string; form: string | null; error: string | null }[]
}

export interface ViewState {
  f: string[]
  q: string
  sort: string
  columns: string[]
}

export interface SavedView {
  id: number
  page: Page
  name: string
  position: number
  is_default: boolean
  warnings: string[]
  count: number | null
  updated: string | null
  state: ViewState
}

export interface Overview {
  matrix: Record<Verdict, Record<string, number>>
  eligible_open: number
  eligible_live: number
  new_24h: number
  new_7d: number
  baseline_24h: number
  changed_24h: number
  changed_7d: number
  failing: string[]
  last_digest: string | null
  pending: number
  eligible_now: Listing[]
  recent: Event[]
  total: number
  n_sources: number
  digest: { subject: string; text: string; events: number } | null
}

export interface Source {
  source: string
  title: string | null
  url: string | null
  every_hours: number | null
  health: 'ok' | 'failing' | 'never'
  last_ok: string | null
  last_attempt: string | null
  failing_since: string | null
  consecutive_failures: number
  next_due: string | null
  last_duration: number | null
  last_error: string | null
  last_events: number | null
  listings: number
  eligible: number
  possible: number
  workers: string[]
}

export interface Fleet {
  available: boolean
  workers: { name: string; queues: string[]; concurrency: number; started: string; last_seen: string; age: number; doing: string[] }[]
  queues: Record<string, Record<string, number>>
  failures: { id: number; queue_name: string; task_name: string; args: Record<string, unknown>; attempts: number; at: string | null; error: string | null }[]
  hosts: { name: string; last_run: string; runs_24h: number; fails_24h: number; sources: number }[]
}

export interface TrackerRow {
  row_key: string
  position: number
  property: string
  status: string | null
  notes: string | null
  source_url: string | null
  page_url: string | null
  cells: Record<string, string>
  listings: { key: string; title: string; verdict: Verdict; status: string; how: string; commute: number | null }[]
}

export interface Tracker {
  configured: boolean
  synced: string | null
  rows: TrackerRow[]
}

export class ApiError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (r.status === 204) return undefined as T
  const data = await r.json().catch(() => ({ error: r.statusText }))
  if (!r.ok) throw new ApiError(data.error ?? r.statusText)
  return data as T
}

export function qs(params: Record<string, string | number | string[] | null | undefined>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    for (const x of Array.isArray(v) ? v : [v]) u.append(k, String(x))
  }
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const api = {
  get: <T>(path: string) => call<T>('GET', path),
  post: <T>(path: string, body?: unknown) => call<T>('POST', path, body ?? {}),
  patch: <T>(path: string, body: unknown) => call<T>('PATCH', path, body),
  delete: (path: string) => call<void>('DELETE', path),
}
