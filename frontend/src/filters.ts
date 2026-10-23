// A list page's state, typed by its page: the filter object (`ListingFilterInput` / `EventFilterInput`, generated
// from schema.graphql), the search, the sort and the columns. It lives in the URL: `filter` as JSON, `sort` as
// `field.direction,...`, `cols` as a list. Everything read from the URL, or edited generically by path, is checked
// against the server's field registry (`meta`) before it is typed; what does not check out is dropped.
import type {
  EventColumn, EventFilterInput, Field, ListingColumn, ListingFilterInput, Meta, Page, PageFilterInput,
  SavedView, SortDirection, UnitColumn,
} from './api'
import type {
  BandFilterInput, BoolFilterInput, BoroughFilterInput, EventFilterFieldsFragment, ListingFilterFieldsFragment,
  ListingSortField, NumberFilterInput, TextFilterInput, TimeFilterInput, UnitFilterInput, UnitSortField,
  VerdictFilterInput, ViewStateInput,
} from './gql/graphql'

export interface Sorted<F extends string> { field: F; direction: SortDirection }
export interface ListingsState { page: 'listings'; filter: ListingFilterInput; q: string; sort: Sorted<ListingSortField>[]; columns: ListingColumn[] }
export interface UnitsState { page: 'units'; filter: ListingFilterInput; q: string; sort: Sorted<UnitSortField>[]; columns: UnitColumn[] }
export interface EventsState { page: 'events'; filter: EventFilterInput; columns: EventColumn[] }
export type ListState = ListingsState | UnitsState | EventsState

/** A change to a page's state, in URL terms; it is checked when the URL is read back. */
export interface Patch { filter?: unknown; q?: string; sort?: { field: string; direction: SortDirection }[]; columns?: string[] }

/** A JSON value: what a filter's operator holds. */
export type Json = string | number | boolean | null | Json[] | { [k: string]: Json }

const isRecord = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x)
const DAY = /^\d{4}-\d{2}-\d{2}$/
const UNITS = new Set(['minutes', 'hours', 'days'])

// --- reading and writing a filter object by path ------------------------------------------------------------

/** The value of operator `op` at `path`, if set. */
export function getCond(filter: unknown, path: readonly string[], op: string): unknown {
  let node: unknown = filter
  for (const k of [...path, op]) node = isRecord(node) ? node[k] : undefined
  return node ?? undefined
}

/** A copy with operator `op` at `path` set to `value` (undefined removes it; emptied objects go too). */
export function setCond(filter: unknown, path: readonly string[], op: string, value: Json | undefined): Record<string, unknown> {
  const put = (node: unknown, keys: readonly string[]): Record<string, unknown> => {
    const out: Record<string, unknown> = isRecord(node) ? { ...node } : {}
    const [k, ...rest] = keys
    const v = rest.length ? put(out[k], rest) : value
    if (v === undefined || (isRecord(v) && Object.keys(v).length === 0)) delete out[k]
    else out[k] = v
    return out
  }
  return put(filter, [...path, op])
}

/** The filter without nulls and empty objects: the form the URL and comparisons use. */
export function prune(x: unknown): unknown {
  if (Array.isArray(x)) return x
  if (!isRecord(x)) return x
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(x)) {
    const p = prune(v)
    if (p !== null && p !== undefined && !(isRecord(p) && Object.keys(p).length === 0)) out[k] = p
  }
  return out
}

// --- checking a filter against the registry ---------------------------------------------------------------

const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

function validOp(f: Field, op: string, v: unknown): boolean {
  if (v === null || v === undefined) return true
  if (!f.ops.some((o) => o === op)) return false
  switch (op) {
    case 'in': case 'not_in':
      return Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === 'string' && (f.type !== 'enum' || f.choices.some((c) => c.name === x)))
    case 'contains': case 'not_contains':
      return typeof v === 'string' && v.trim() !== ''
    case 'empty': case 'is':
      return typeof v === 'boolean'
    case 'between':
      return isRecord(v) && (f.type === 'number'
        ? isNumber(v.min) && isNumber(v.max)
        : typeof v.from === 'string' && DAY.test(v.from) && typeof v.to === 'string' && DAY.test(v.to))
    case 'after': case 'before':
      return typeof v === 'string' && DAY.test(v)
    case 'since': case 'until':
      return typeof v === 'string' && !Number.isNaN(Date.parse(v))
    case 'last':
      return isRecord(v) && Number.isInteger(v.amount) && isNumber(v.amount) && v.amount >= 1 && typeof v.unit === 'string' && UNITS.has(v.unit)
    default:
      return isNumber(v) // eq, ne, lt, le, gt, ge
  }
}

const samePath = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((k, i) => k === b[i])

function validTree(x: unknown, fields: readonly Field[], prefix: readonly string[]): boolean {
  if (!isRecord(x)) return false
  return Object.entries(x).every(([k, v]) => {
    if (v === null || v === undefined) return true
    const path = [...prefix, k]
    const f = fields.find((fl) => samePath(fl.path, path))
    if (f) return isRecord(v) && Object.entries(v).every(([op, val]) => validOp(f, op, val))
    return fields.some((fl) => samePath(fl.path.slice(0, path.length), path)) && validTree(v, fields, path)
  })
}

/** Whether `x` is a filter the Listings or Units page's fields allow. */
export const isListingFilter = (x: unknown, fields: readonly Field[]): x is ListingFilterInput => validTree(x, fields, [])
/** Whether `x` is a filter the Events page's fields allow. */
export const isEventFilter = (x: unknown, fields: readonly Field[]): x is EventFilterInput => validTree(x, fields, [])

/** The page's filter without one condition, as the `values` query takes it (the counts for the one being edited). */
export function pageFilter(page: Page, filter: unknown, meta: Meta): PageFilterInput {
  if (page === 'events') return { events: isEventFilter(filter, meta.pages.events.fields) ? filter : {} }
  const f = isListingFilter(filter, meta.pages[page].fields) ? filter : {}
  return page === 'units' ? { units: f } : { listings: f }
}

// A saved view's filter is read with fragments that must select every field of every filter type, or a condition
// would silently fall out of the view. Each output type has the fields of its input twin, so a field the schema
// adds and the fragments miss fails this typecheck.
type Missing<Full, Got> = Exclude<keyof Full, keyof Got>
type Covered<Full, Got> = [Missing<Full, Got>] extends [never] ? true : { missing: Missing<Full, Got> }
type Sel<T> = NonNullable<T>
type LF = ListingFilterFieldsFragment
export const FRAGMENTS_COVER_THE_SCHEMA: [
  Covered<ListingFilterInput, LF>, Covered<EventFilterInput, EventFilterFieldsFragment>,
  Covered<UnitFilterInput, Sel<LF['units']>>, Covered<BandFilterInput, Sel<Sel<LF['units']>['bands']>>,
  Covered<TextFilterInput, Sel<LF['notes']>>, Covered<NumberFilterInput, Sel<LF['commute']>>,
  Covered<TimeFilterInput, Sel<LF['first_seen']>>, Covered<BoolFilterInput, Sel<LF['senior_only']>>,
  Covered<BoroughFilterInput, Sel<LF['borough']>>, Covered<VerdictFilterInput, Sel<LF['verdict']>>,
] = [true, true, true, true, true, true, true, true, true, true]

// --- the URL ------------------------------------------------------------------------------------------------

function pick<T extends string>(choices: readonly T[], s: string): T | undefined {
  return choices.find((c) => c === s)
}

function parseSort<F extends string>(raw: string | null, fields: readonly F[], dflt: readonly Sorted<F>[]): Sorted<F>[] {
  const out: Sorted<F>[] = []
  for (const part of raw?.split(',') ?? []) {
    const [name, dir] = part.split('.')
    const field = pick(fields, name ?? '')
    if (field && (dir === 'asc' || dir === 'desc')) out.push({ field, direction: dir })
  }
  return out.length ? out : [...dflt]
}

function parseCols<C extends string>(raw: string | null, cols: readonly C[], dflt: readonly C[]): C[] {
  const out = (raw?.split(',') ?? []).map((s) => pick(cols, s)).filter((c) => c !== undefined)
  return out.length ? out : [...dflt]
}

function readFilter(params: URLSearchParams): unknown {
  const raw = params.get('filter')
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed
  } catch {
    return undefined // not JSON: reported as unreadable
  }
}

/** The state a page's URL describes, and whether its filter had to be dropped as unreadable. */
export function parseState(page: Page, params: URLSearchParams, meta: Meta): { state: ListState; unreadable: boolean } {
  const raw = readFilter(params)
  const q = params.get('q') ?? ''
  if (page === 'events') {
    const pm = meta.pages.events
    const ok = isEventFilter(raw, pm.fields)
    const columns = parseCols(params.get('cols'), pm.columns.map((c) => c.column), pm.default_columns)
    return { state: { page, filter: ok ? raw : {}, columns }, unreadable: !ok }
  }
  if (page === 'units') {
    const pm = meta.pages.units
    const ok = isListingFilter(raw, pm.fields)
    const sort = parseSort(params.get('sort'), pm.sorts.map((s) => s.field), pm.default_sort)
    const columns = parseCols(params.get('cols'), pm.columns.map((c) => c.column), pm.default_columns)
    return { state: { page, filter: ok ? raw : {}, q, sort, columns }, unreadable: !ok }
  }
  const pm = meta.pages.listings
  const ok = isListingFilter(raw, pm.fields)
  const sort = parseSort(params.get('sort'), pm.sorts.map((s) => s.field), pm.default_sort)
  const columns = parseCols(params.get('cols'), pm.columns.map((c) => c.column), pm.default_columns)
  return { state: { page, filter: ok ? raw : {}, q, sort, columns }, unreadable: !ok }
}

const sortText = (sort: readonly { field: string; direction: SortDirection }[]) => sort.map((s) => `${s.field}.${s.direction}`).join(',')

/** The URL parameters of a state, defaults left out. */
export function stateParams(page: Page, st: Patch, meta: Meta | undefined): URLSearchParams {
  const p = new URLSearchParams()
  const filter = prune(st.filter ?? {})
  if (isRecord(filter) && Object.keys(filter).length) p.set('filter', JSON.stringify(filter))
  if (st.q?.trim()) p.set('q', st.q.trim())
  const pm = meta?.pages[page]
  if (st.sort?.length && (!pm || !('default_sort' in pm) || sortText(st.sort) !== sortText(pm.default_sort))) p.set('sort', sortText(st.sort))
  if (st.columns?.length && (!pm || st.columns.join(',') !== pm.default_columns.join(','))) p.set('cols', st.columns.join(','))
  return p
}

/** A list page's URL for a state, optionally inside saved view `view` (`none`: the page itself, not its default). */
export function stateUrl(st: ListState, meta: Meta | undefined, view?: string | null): string {
  const p = stateParams(st.page, st, meta)
  if (view) p.set('view', view)
  const s = p.toString()
  return `/${st.page}${s ? `?${s}` : ''}`
}

/** A list page's URL showing just this filter, e.g. from the Overview. */
export const filterUrl = (page: 'listings' | 'units', filter: ListingFilterInput) => {
  const p = new URLSearchParams({ filter: JSON.stringify(prune(filter)) })
  return `/${page}?${p}`
}

export function sameState(a: ListState, b: ListState, meta: Meta | undefined): boolean {
  return a.page === b.page && stateParams(a.page, a, meta).toString() === stateParams(b.page, b, meta).toString()
}

/** A saved view's state, as the page state it opens. */
export function viewState(v: SavedView): ListState {
  const s = v.state
  switch (s.__typename) {
    case 'ListingsViewState':
      return { page: 'listings', filter: s.filter, q: s.q, sort: s.sort, columns: s.columns }
    case 'UnitsViewState':
      return { page: 'units', filter: s.filter, q: s.q, sort: s.unit_sort, columns: s.unit_columns }
    case 'EventsViewState':
      return { page: 'events', filter: s.event_filter, columns: s.event_columns }
  }
}

/** A page state as the views mutations take it. */
export function stateInput(st: ListState): ViewStateInput {
  switch (st.page) {
    case 'listings':
      return { listings: { filter: st.filter, q: st.q, sort: st.sort, columns: st.columns } }
    case 'units':
      return { units: { filter: st.filter, q: st.q, sort: st.sort, columns: st.columns } }
    case 'events':
      return { events: { filter: st.filter, columns: st.columns } }
  }
}

export const hasFilter = (st: ListState) => {
  const f = prune(st.filter)
  return isRecord(f) && Object.keys(f).length > 0
}

