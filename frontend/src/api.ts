// The GraphQL API of src/housing_watch/web/schema.py, at /graphql. Filters travel as `f: ["<field>.<op>.<values>"]`.

export type Page = 'listings' | 'units' | 'events'
export type Verdict = 'eligible' | 'possible' | 'excluded'
export type StageGroup = 'your move' | 'their move' | 'done' // whose move an application stage is

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
  application?: string | null // the stage of this listing's tracker row (its sheet Status, legacy names mapped)
  application_group?: StageGroup | null // null for a status that is not a known stage
  application_since?: string | null // when the row entered that stage
  application_since_baseline?: boolean // the stage predates tracking, so it began before application_since
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
  application_group?: StageGroup | null
  application_since?: string | null
  application_since_baseline?: boolean
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

// a stage change recorded by a sync; `baseline`: the stage was already set when tracking began
export interface StageStep { at: string; stage_from: string | null; stage_to: string | null; group: StageGroup | null; baseline: boolean }

export interface TrackerRow {
  row_key: string
  position: number
  property: string
  status: string | null // as written in the sheet
  stage: string | null
  group: StageGroup | null
  stage_since: string | null
  stage_since_baseline: boolean
  events: StageStep[] // oldest first
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

/** POST one GraphQL document; its `data`, or the first error's message as an ApiError. */
export async function gql<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const r = await fetch('/graphql', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  })
  const res = await r.json().catch(() => ({ errors: [{ message: r.statusText }] }))
  if (res.errors?.length) throw new ApiError(res.errors[0].message)
  if (!r.ok) throw new ApiError(r.statusText)
  return res.data as T
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

// --- documents: each selects every field its type above declares ------------------------------------

const CHIP = 'i f label field op values'
const LISTING = `key source title url address borough status kind deadline senior_only first_seen last_changed gone_since
  verdict reason units eligible_units sizes rent_min rent_max ami_min ami_max commute commute_route application
  application_group application_since application_since_baseline application_notes tracker_row tracker_match`
const UNIT = `id key source title url address borough status kind deadline first_seen last_changed gone_since
  listing_verdict size unit_no rent ami set_aside available chart_year income_min income_max verdict reason commute
  application application_group application_since application_since_baseline`
const EVENT = 'id at source key type summary detail notified baseline title verdict'
const VIEW = 'id page name position is_default warnings count updated state { f q sort columns }'
const page = (items: string) => `items { ${items} } total next_offset verdict_counts { eligible possible excluded }
  filters { ${CHIP} } problems hidden_gone`
const PAGE_META = 'fields { name label group type ops money choices } columns default_columns default_sort sorts'
const LIST_ARGS = '$f: [String!], $q: String! = "", $sort: String! = "", $offset: Int! = 0, $limit: Int! = 200'

export const Q = {
  meta: `{ meta { profile { household income sizes excluded_boroughs } statuses verdicts op_labels
    pages { listings { ${PAGE_META} } units { ${PAGE_META} } events { ${PAGE_META} } } } }`,
  listings: `query(${LIST_ARGS}) { listings(f: $f, q: $q, sort: $sort, offset: $offset, limit: $limit) { ${page(LISTING)} } }`,
  units: `query(${LIST_ARGS}) { units(f: $f, q: $q, sort: $sort, offset: $offset, limit: $limit) { ${page(UNIT)} } }`,
  listing: `query($key: String!) { listing(key: $key) { summary { ${LISTING} } notes ext_id last_seen household pdfs
    units { size unit_no rent ami set_aside available bands verdict reason chart_year } events { ${EVENT} } } }`,
  applications: 'query($key: String!) { listingApplications(key: $key) { ready pdfs { n url form error } } }',
  values: `query($page: String!, $field: String!, $f: [String!], $q: String! = "") {
    values(page: $page, field: $field, f: $f, q: $q) { enum counts { value count } } }`,
  events: `query($f: [String!], $before: Int) { events(f: $f, before: $before) {
    items { ${EVENT} } total next_before filters { ${CHIP} } problems } }`,
  overview: `{ overview { matrix eligible_open eligible_live new_24h new_7d baseline_24h changed_24h changed_7d failing
    last_digest pending eligible_now { ${LISTING} } recent { ${EVENT} } total n_sources digest { subject text events } } }`,
  sources: `{ sources { source title url every_hours health last_ok last_attempt failing_since consecutive_failures
    next_due last_duration last_error last_events listings eligible possible workers } }`,
  fleet: `{ fleet { available workers { name queues concurrency started last_seen age doing } queues
    failures { id queue_name task_name args attempts at error } hosts { name last_run runs_24h fails_24h sources } } }`,
  tracker: `{ tracker { configured synced rows { row_key position property status stage group stage_since
    stage_since_baseline events { at stage_from stage_to group baseline } notes source_url page_url cells
    listings { key title verdict status how commute } } } }`,
  views: `{ views { ${VIEW} } }`,
  createView: `mutation($page: String!, $name: String!, $is_default: Boolean! = false, $state: ViewStateInput) {
    createView(page: $page, name: $name, is_default: $is_default, state: $state) { ${VIEW} } }`,
  changeView: `mutation($id: Int!, $name: String, $state: ViewStateInput, $is_default: Boolean, $move: Int) {
    changeView(id: $id, name: $name, state: $state, is_default: $is_default, move: $move) { ${VIEW} } }`,
  duplicateView: `mutation($id: Int!) { duplicateView(id: $id) { ${VIEW} } }`,
  deleteView: 'mutation($id: Int!) { deleteView(id: $id) }',
  linkTracker: `mutation($listing_key: String!, $row_key: String) {
    linkTracker(listing_key: $listing_key, row_key: $row_key) { listing_key row_key } }`,
  unlinkTracker: 'mutation($listing_key: String!) { unlinkTracker(listing_key: $listing_key) }',
}
