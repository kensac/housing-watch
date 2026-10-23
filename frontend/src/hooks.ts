import { useDocumentTitle, useReducedMotion } from '@mantine/hooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, type MouseEvent } from 'react'
import { useSearchParams } from 'react-router-dom'

import { MetaDoc, qs, request, TrackerDoc, ViewsDoc, type Meta, type Page, type ViewState } from './api'

export const TITLES: Record<Page, string> = { listings: 'Listings', units: 'Units', events: 'Events' }

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => request(MetaDoc).then((d) => d.meta), staleTime: Infinity })
}

export function useViews() {
  return useQuery({ queryKey: ['views'], queryFn: () => request(ViewsDoc).then((d) => d.views) })
}

export function useRefreshViews() {
  const qc = useQueryClient()
  return useCallback(() => qc.invalidateQueries({ queryKey: ['views'] }), [qc])
}

/** Transition props that switch Mantine's motion off when the OS asks for reduced motion. */
export function useMotion() {
  return useReducedMotion() ? { duration: 0 } : undefined
}

export function useTitle(title: string | undefined) {
  useDocumentTitle(title ? `${title} · housing-watch` : 'housing-watch')
}

/** A list page's URL for a state (defaults left out), optionally inside saved view `view`. */
export function stateUrl(page: Page, st: ViewState, meta: Meta | undefined, view?: number | 'none' | null): string {
  const pm = meta?.pages[page]
  const sort = pm && st.sort === pm.default_sort ? '' : st.sort
  const cols = pm && st.columns.join(',') === pm.default_columns.join(',') ? '' : st.columns.join(',')
  return `/${page}${qs({ f: st.f, q: st.q.trim(), sort, cols, view: view ?? null })}`
}

export function sameState(a: ViewState, b: ViewState): boolean {
  return a.f.join('\n') === b.f.join('\n') && a.q.trim() === b.q.trim() && a.sort === b.sort &&
    a.columns.join(',') === b.columns.join(',')
}

// sorts whose natural order is newest first; every other sort starts ascending ("-x" reverses either)
const DESC_FIRST = new Set(['changed', 'first_seen'])

/** Whether a sort string runs descending. */
export const descending = (sort: string) => DESC_FIRST.has(sort.replace(/^-/, '')) !== sort.startsWith('-')

type Patch = Partial<ViewState> & { open?: string | null }

/** The list state a page shows, all of it in the URL: filters, search, sort, columns, saved view, open row. */
export function useListState(page: Page) {
  const [params, setParams] = useSearchParams()
  const meta = useMeta().data
  const views = useViews().data
  const pm = meta?.pages[page]
  const state: ViewState = useMemo(() => ({
    f: params.getAll('f'),
    q: params.get('q') ?? '',
    sort: params.get('sort') ?? pm?.default_sort ?? '',
    columns: params.get('cols')?.split(',').filter(Boolean) ?? pm?.default_columns ?? [],
  }), [params, pm])
  const viewParam = params.get('view')
  const view = views?.find((v) => String(v.id) === viewParam && v.page === page) ?? null
  const open = params.get('open')

  const search = useCallback((patch: Patch) => {
    const next = { ...state, ...patch }
    const p = new URLSearchParams(stateUrl(page, next, meta, viewParam as 'none' | null).split('?')[1] ?? '')
    const o = 'open' in patch ? patch.open : open
    if (o) p.set('open', o)
    return p
  }, [state, page, meta, viewParam, open])

  const set = useCallback((patch: Patch, replace = false) => setParams(search(patch), { replace }), [search, setParams])
  // the list's own query, without `open`: stable while the drawer opens and closes, so rows can skip re-rendering
  const base = useMemo(() => stateUrl(page, state, meta, viewParam as 'none' | null).split('?')[1] ?? '', [page, state, meta, viewParam])

  return {
    state, view, open, set, base, ready: Boolean(meta && pm && views),
    modified: Boolean(view && !sameState(state, view.state)),
    bare: [...params.keys()].length === 0,
  }
}

/** The link that opens listing `key` in the drawer over a list whose query is `base`. */
export const openHref = (page: Page | '', base: string, key: string) =>
  `/${page}?${base ? `${base}&` : ''}open=${encodeURIComponent(key)}`

/** A table row's mouse shortcut to its link: plain clicks on the row open it; clicks on controls, modified
 * clicks (new tab) and text selections are left alone. Keyboard users reach the row's real link instead. */
export function rowClick(go: () => void) {
  return (e: MouseEvent<HTMLElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    if ((e.target as HTMLElement).closest('a, button, input, label, [role="button"]')) return
    if (window.getSelection()?.toString()) return
    go()
  }
}

export function useTracker() {
  return useQuery({ queryKey: ['tracker'], queryFn: () => request(TrackerDoc).then((d) => d.tracker) })
}

/** Re-read everything a tracker link changes: the listing, the lists and the tracker page. */
export function useTrackerRefresh() {
  const qc = useQueryClient()
  return () => Promise.all(['listing', 'listings', 'units', 'tracker', 'overview'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
}
